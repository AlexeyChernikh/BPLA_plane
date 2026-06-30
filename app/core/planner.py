from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import Point
from shapely.ops import unary_union

from .geometry_utils import normalize_polygon, true_to_grid_azimuth
from .grid_generator import generate_profiles
from .mission_splitter import split_missions
from .models import (
    Mission,
    OperationalZone,
    PlannerSettings,
    PlanningResult,
    TakeoffSite,
)
from .polygon_loader import load_polygon
from .regular_grid import automatic_grid_sizes, build_regular_grid
from .route_builder import build_terrain_route
from .takeoff_optimizer import profiles_for_zone
from .terrain import TerrainModel
from .terrain_route import terrain_route_metrics

SMALL_MISSION_AREA_RATIO = 0.25
BATTERY_WARNING_STATUS = "Возможен выход за пределы лимита батареи"


def plan_missions(
    polygon_path: str | Path,
    home_latitude: float,
    home_longitude: float,
    settings: PlannerSettings,
) -> PlanningResult:
    _validate_settings(settings)
    loaded = load_polygon(polygon_path, settings.working_crs)
    transformer = Transformer.from_crs(
        CRS.from_epsg(4326), settings.working_crs, always_xy=True
    )
    home_x, home_y = transformer.transform(home_longitude, home_latitude)
    home = Point(home_x, home_y)
    grid_azimuth = true_to_grid_azimuth(
        settings.azimuth_deg,
        settings.working_crs,
        loaded.geometry.centroid.x,
        loaded.geometry.centroid.y,
    )
    grid_settings = replace(settings, azimuth_deg=grid_azimuth)
    profiles = generate_profiles(
        loaded.geometry,
        grid_azimuth,
        settings.profile_spacing_m,
        settings.profile_extension_m,
    )
    missions = split_missions(loaded.geometry, profiles, home, grid_settings)
    return PlanningResult(loaded, home, settings, profiles, missions)


def plan_terrain_missions(
    polygon_path: str | Path,
    dem_path: str | Path,
    settings: PlannerSettings,
    override_homes_wgs84: list[tuple[float, float]] | None = None,
    forced_grid_sizes: tuple[float, float] | None = None,
) -> PlanningResult:
    _validate_settings(settings)
    loaded = load_polygon(polygon_path, settings.working_crs)
    terrain = TerrainModel(dem_path, settings.working_crs, loaded.geometry)
    grid_azimuth = true_to_grid_azimuth(
        settings.azimuth_deg,
        settings.working_crs,
        loaded.geometry.centroid.x,
        loaded.geometry.centroid.y,
    )
    base_profiles = generate_profiles(
        loaded.geometry,
        grid_azimuth,
        settings.profile_spacing_m,
        settings.profile_extension_m,
    )
    if not base_profiles:
        raise ValueError("Внутри полигона не построено ни одного профиля.")

    mission_side, zone_side, multiplier = automatic_grid_sizes(
        settings.profile_spacing_m,
        settings.speed_mps,
        settings.usable_time_sec,
        settings.altitude_m,
        settings.climb_speed_mps,
        settings.descent_speed_mps,
        settings.max_zone_side_m,
        settings.max_mission_side_m,
    )
    maximum_zone_side = zone_side
    override_sites = _transform_override_homes(
        override_homes_wgs84, loaded.geometry, terrain, settings
    )
    if forced_grid_sizes is not None:
        zone_size, mission_size = forced_grid_sizes
        return _build_regular_result(
            loaded,
            Path(dem_path),
            terrain,
            base_profiles,
            settings,
            mission_size,
            zone_size,
            override_sites,
            grid_azimuth,
        )
    best_result: PlanningResult | None = None
    best_zone_side = 0.0
    last_result: PlanningResult | None = None
    candidate_side = mission_side

    while candidate_side >= settings.profile_spacing_m - 1e-7:
        max_multiplier = min(
            6, max(1, int(maximum_zone_side // candidate_side))
        )
        if override_sites is not None:
            for candidate_multiplier in range(max_multiplier, 0, -1):
                count = len(
                    build_regular_grid(
                        loaded.geometry,
                        candidate_side * candidate_multiplier,
                        grid_azimuth,
                    )
                )
                if count != len(override_sites):
                    continue
                return _build_regular_result(
                    loaded,
                    Path(dem_path),
                    terrain,
                    base_profiles,
                    settings,
                    candidate_side,
                    candidate_side * candidate_multiplier,
                    override_sites,
                    grid_azimuth,
                )
        else:
            low, high = 1, max_multiplier
            side_result: PlanningResult | None = None
            side_multiplier = 0
            while low <= high:
                candidate_multiplier = (low + high) // 2
                result = _build_regular_result(
                    loaded,
                    Path(dem_path),
                    terrain,
                    base_profiles,
                    settings,
                    candidate_side,
                    candidate_side * candidate_multiplier,
                    None,
                    grid_azimuth,
                )
                last_result = result
                if result.valid:
                    side_result = result
                    side_multiplier = candidate_multiplier
                    low = candidate_multiplier + 1
                else:
                    high = candidate_multiplier - 1
            if side_result is not None:
                current_zone_side = candidate_side * side_multiplier
                if (
                    current_zone_side > best_zone_side + 1e-7
                    or (
                        abs(current_zone_side - best_zone_side) <= 1e-7
                        and best_result is not None
                        and candidate_side
                        > best_result.missions[0].nominal_side_m
                    )
                ):
                    best_result = side_result
                    best_zone_side = current_zone_side

        next_side = candidate_side - settings.profile_spacing_m
        if next_side < settings.profile_spacing_m - 1e-7:
            break
        next_max_multiplier = min(
            6, max(1, int(maximum_zone_side // next_side))
        )
        if (
            best_result is not None
            and next_side * next_max_multiplier <= best_zone_side + 1e-7
        ):
            break
        candidate_side = next_side

    if override_sites is not None:
        raise ValueError(
            "Не удалось восстановить регулярную сетку для перемещённых Home."
        )
    if best_result is not None:
        return best_result
    if last_result is not None:
        return last_result
    raise ValueError("Не удалось подобрать размеры регулярной сетки.")


def _build_regular_result(
    loaded,
    dem_path: Path,
    terrain: TerrainModel,
    base_profiles,
    settings: PlannerSettings,
    mission_side: float,
    zone_side: float,
    override_sites: list[TakeoffSite] | None,
    grid_azimuth: float,
) -> PlanningResult:
    zone_cells = build_regular_grid(
        loaded.geometry, zone_side, grid_azimuth
    )
    mission_cells = build_regular_grid(
        loaded.geometry, mission_side, grid_azimuth
    )
    work_geometry = unary_union(
        [profile.geometry for profile in base_profiles]
    )
    active_specs: list[list] = []
    empty_zone_cells = []
    for cell in zone_cells:
        if work_geometry.intersection(cell.geometry).length > 1e-6:
            active_specs.append([cell, cell.geometry])
        else:
            empty_zone_cells.append(cell)
    if not active_specs:
        raise ValueError("Регулярная сетка не пересекает ни одного профиля.")
    for empty_cell in empty_zone_cells:
        nearest = min(
            active_specs,
            key=lambda item: item[1].distance(empty_cell.geometry),
        )
        nearest[1] = normalize_polygon(
            nearest[1].union(empty_cell.geometry)
        )

    if override_sites is not None and len(override_sites) != len(active_specs):
        raise ValueError(
            "Число перемещённых Home не совпадает с числом регулярных зон."
        )

    allowed = loaded.geometry.buffer(settings.home_search_buffer_m)
    sites: list[TakeoffSite] = []
    for identifier, (cell, geometry) in enumerate(active_specs, start=1):
        if override_sites is None:
            site = _best_local_site(
                geometry.representative_point(),
                identifier,
                terrain,
                _home_search_area(geometry, settings.home_search_buffer_m).intersection(allowed),
                geometry,
            )
        else:
            source = override_sites[identifier - 1]
            site = TakeoffSite(
                identifier,
                source.point,
                source.elevation_m,
                source.slope_deg,
                source.roughness_m,
            )
        sites.append(site)

    zones: list[OperationalZone] = []
    all_profiles = []
    all_missions = []
    errors: list[str] = []
    next_profile_id = 1

    for identifier, ((zone_cell, zone_geometry), site) in enumerate(
        zip(active_specs, sites), start=1
    ):
        relief = terrain.relief(zone_geometry)
        warning = (
            f"Сложный рельеф: перепад {relief:.0f} м"
            if relief > settings.terrain_warning_m
            else "OK"
        )
        zone = OperationalZone(
            id=identifier,
            home=site,
            geometry=zone_geometry,
            relief_m=relief,
            status=warning,
            grid_row=zone_cell.row,
            grid_col=zone_cell.col,
            nominal_side_m=zone_side,
            edge_clipped=(
                zone_cell.edge_clipped
                or zone_geometry.area
                > zone_cell.nominal_side_m**2 * (1 + 1e-7)
            ),
        )
        empty_cells = []
        for cell in mission_cells:
            clipped = cell.geometry.intersection(zone_geometry)
            if clipped.is_empty or clipped.area <= 1e-6:
                continue
            geometry = normalize_polygon(clipped)
            profiles = profiles_for_zone(
                base_profiles, geometry, 1e12, next_profile_id
            )
            if profiles:
                next_profile_id = profiles[-1].id + 1
            if not profiles:
                empty_cells.append(geometry)
                continue

            points, _ = build_terrain_route(
                profiles,
                site.point,
                grid_azimuth,
                settings.route_mode,
                terrain,
                settings.altitude_m,
                settings.speed_mps,
                settings.climb_speed_mps,
                settings.descent_speed_mps,
                settings.terrain_adjust_tolerance_m,
            )
            length, duration, elevations = terrain_route_metrics(
                site.point, points, settings, terrain
            )
            mission_id = len(zone.missions) + 1
            status = warning
            if duration > settings.usable_time_sec + 1e-7:
                status = (
                    f"Недопустимо: {duration / 60:.1f} мин > "
                    f"{settings.usable_time_sec / 60:.1f} мин"
                )
            mission = Mission(
                id=mission_id,
                profiles=profiles,
                route_points=points,
                route_length_m=length,
                estimated_time_min=duration / 60.0,
                status=status,
                zone=geometry,
                zone_id=identifier,
                home_id=site.id,
                terrain_elevations_m=elevations,
                grid_row=cell.row,
                grid_col=cell.col,
                nominal_side_m=mission_side,
                edge_clipped=(
                    cell.edge_clipped
                    or geometry.area
                    < mission_side * mission_side * (1 - 1e-7)
                ),
            )
            zone.missions.append(mission)
            zone.profiles.extend(profiles)

        if not zone.missions:
            message = f"Зона {identifier} не содержит полётных профилей."
            errors.append(message)
            zone.status = f"Недопустимо: {message}"
        else:
            _merge_small_missions(
                zone,
                site,
                settings,
                terrain,
                grid_azimuth,
                warning,
            )
            for geometry in empty_cells:
                nearest = min(
                    zone.missions,
                    key=lambda mission: mission.zone.distance(geometry),
                )
                nearest.zone = normalize_polygon(nearest.zone.union(geometry))
                nearest.edge_clipped = True
            for mission_index, mission in enumerate(zone.missions, start=1):
                mission.id = mission_index
        if any(
            mission.status.startswith("Недопустимо")
            for mission in zone.missions
        ):
            zone.status = (
                "Недопустимо: одна или несколько миссий превышают лимит"
            )
            for mission in zone.missions:
                if mission.status.startswith("Недопустимо"):
                    errors.append(
                        f"Зона {identifier}, миссия {mission.id}: {mission.status}"
                    )
        elif any(
            mission.status.startswith(BATTERY_WARNING_STATUS)
            for mission in zone.missions
        ):
            zone.status = BATTERY_WARNING_STATUS
        zones.append(zone)
        all_profiles.extend(zone.profiles)
        all_missions.extend(zone.missions)

    return PlanningResult(
        polygon=loaded,
        home_working=sites[0].point,
        settings=settings,
        profiles=all_profiles,
        missions=all_missions,
        takeoff_sites=sites,
        zones=zones,
        dem_path=dem_path,
        valid=not errors,
        errors=errors,
    )


def _merge_small_missions(
    zone: OperationalZone,
    site: TakeoffSite,
    settings: PlannerSettings,
    terrain: TerrainModel,
    grid_azimuth: float,
    base_status: str,
) -> None:
    while len(zone.missions) > 1:
        small = _smallest_merge_candidate(zone.missions)
        if small is None:
            break
        receiver = _merge_receiver(small, zone.missions, site, settings, terrain, grid_azimuth)
        receiver.zone = normalize_polygon(receiver.zone.union(small.zone))
        receiver.profiles = sorted(
            [*receiver.profiles, *small.profiles],
            key=lambda profile: (profile.offset_m, profile.id),
        )
        receiver.edge_clipped = True
        _recalculate_mission_route(
            receiver,
            site,
            settings,
            terrain,
            grid_azimuth,
            base_status,
        )
        zone.missions.remove(small)
    zone.profiles = [
        profile for mission in zone.missions for profile in mission.profiles
    ]


def _smallest_merge_candidate(missions: list[Mission]) -> Mission | None:
    candidates = [
        mission
        for mission in missions
        if mission.zone is not None
        and not mission.zone.is_empty
        and mission.zone.area
        < mission.nominal_side_m**2 * SMALL_MISSION_AREA_RATIO
    ]
    return min(candidates, key=lambda mission: mission.zone.area) if candidates else None


def _merge_receiver(
    small: Mission,
    missions: list[Mission],
    site: TakeoffSite,
    settings: PlannerSettings,
    terrain: TerrainModel,
    grid_azimuth: float,
) -> Mission:
    candidates = [mission for mission in missions if mission is not small]
    touching = [
        mission
        for mission in candidates
        if mission.zone is not None and mission.zone.touches(small.zone)
    ]
    if touching:
        return min(
            touching,
            key=lambda mission: _merged_duration(
                mission, small, site, settings, terrain, grid_azimuth
            ),
        )
    return min(candidates, key=lambda mission: mission.zone.distance(small.zone))


def _merged_duration(
    receiver: Mission,
    small: Mission,
    site: TakeoffSite,
    settings: PlannerSettings,
    terrain: TerrainModel,
    grid_azimuth: float,
) -> float:
    profiles = [*receiver.profiles, *small.profiles]
    points, _ = build_terrain_route(
        profiles,
        site.point,
        grid_azimuth,
        settings.route_mode,
        terrain,
        settings.altitude_m,
        settings.speed_mps,
        settings.climb_speed_mps,
        settings.descent_speed_mps,
        settings.terrain_adjust_tolerance_m,
    )
    _, duration, _ = terrain_route_metrics(site.point, points, settings, terrain)
    return duration


def _recalculate_mission_route(
    mission: Mission,
    site: TakeoffSite,
    settings: PlannerSettings,
    terrain: TerrainModel,
    grid_azimuth: float,
    base_status: str,
) -> None:
    points, _ = build_terrain_route(
        mission.profiles,
        site.point,
        grid_azimuth,
        settings.route_mode,
        terrain,
        settings.altitude_m,
        settings.speed_mps,
        settings.climb_speed_mps,
        settings.descent_speed_mps,
        settings.terrain_adjust_tolerance_m,
    )
    length, duration, elevations = terrain_route_metrics(
        site.point, points, settings, terrain
    )
    mission.route_points = points
    mission.route_length_m = length
    mission.estimated_time_min = duration / 60.0
    mission.terrain_elevations_m = elevations
    if duration > settings.usable_time_sec + 1e-7:
        mission.status = (
            f"{BATTERY_WARNING_STATUS}: {duration / 60:.1f} мин > "
            f"{settings.usable_time_sec / 60:.1f} мин"
        )
    else:
        mission.status = base_status


def _transform_override_homes(
    override_homes_wgs84,
    polygon,
    terrain: TerrainModel,
    settings: PlannerSettings,
) -> list[TakeoffSite] | None:
    if override_homes_wgs84 is None:
        return None
    transformer = Transformer.from_crs(
        CRS.from_epsg(4326), settings.working_crs, always_xy=True
    )
    allowed = polygon.buffer(settings.home_search_buffer_m)
    sites = []
    for identifier, (latitude, longitude) in enumerate(
        override_homes_wgs84, start=1
    ):
        x, y = transformer.transform(longitude, latitude)
        point = Point(x, y)
        if not allowed.covers(point) or polygon.covers(point):
            raise ValueError(
                f"Home {identifier} находится внутри миссии или за пределами допустимого буфера."
            )
        sample = terrain.sample(point)
        sites.append(
            TakeoffSite(
                identifier,
                point,
                sample.elevation_m,
                sample.slope_deg,
                sample.roughness_m,
            )
        )
    return sites


def _best_local_site(
    center: Point,
    identifier: int,
    terrain: TerrainModel,
    allowed_geometry,
    forbidden_geometry,
) -> TakeoffSite:
    candidates = []
    if allowed_geometry.is_empty:
        raise ValueError(
            "Буфер Home не создаёт внешнюю область для точки взлёта."
        )
    for radius in (100.0, 200.0, 350.0, 500.0, 750.0, 1000.0):
        for index in range(8):
            angle = math.tau * index / 8
            candidates.append(
                Point(
                    center.x + math.cos(angle) * radius,
                    center.y + math.sin(angle) * radius,
                )
            )
    nearest = allowed_geometry.boundary.interpolate(
        allowed_geometry.boundary.project(center)
    )
    candidates.append(nearest)
    minx, miny, maxx, maxy = allowed_geometry.bounds
    spacing = max(50.0, min(250.0, max(maxx - minx, maxy - miny) / 12.0))
    x = math.floor(minx / spacing) * spacing
    while x <= maxx:
        y = math.floor(miny / spacing) * spacing
        while y <= maxy:
            point = Point(x, y)
            if allowed_geometry.covers(point):
                candidates.append(point)
            y += spacing
        x += spacing
    ranked = []
    for point in candidates:
        if not allowed_geometry.covers(point):
            continue
        if forbidden_geometry.covers(point):
            continue
        try:
            sample = terrain.sample(point)
        except ValueError:
            continue
        ranked.append(
            (
                sample.slope_deg,
                sample.roughness_m,
                point.distance(center),
                point,
                sample,
            )
        )
    if not ranked:
        raise ValueError("Вне участка рядом с регулярной зоной нет данных DEM для Home.")
    _, _, _, point, sample = min(ranked, key=lambda item: item[:3])
    return TakeoffSite(
        identifier,
        point,
        sample.elevation_m,
        sample.slope_deg,
        sample.roughness_m,
    )


def _home_search_area(polygon, buffer_m: float):
    return polygon.buffer(buffer_m).difference(polygon)


def _validate_settings(settings: PlannerSettings) -> None:
    if not 0 <= settings.battery_reserve_percent < 100:
        raise ValueError("Резерв батареи должен быть от 0 до 100%.")
    for value, name in (
        (settings.profile_spacing_m, "Шаг профилей"),
        (settings.altitude_m, "Высота"),
        (settings.speed_mps, "Скорость"),
        (settings.max_flight_time_min, "Время полёта"),
        (settings.waypoint_step_m, "Шаг waypoint"),
        (settings.climb_speed_mps, "Скорость набора"),
        (settings.descent_speed_mps, "Скорость снижения"),
    ):
        if value <= 0:
            raise ValueError(f"{name}: значение должно быть больше нуля.")
    if settings.terrain_adjust_tolerance_m < 0:
        raise ValueError("Допуск рельефа не может быть отрицательным.")
    if settings.home_search_buffer_m < 0:
        raise ValueError("Буфер поиска Home не может быть отрицательным.")
    if settings.terrain_warning_m <= 0:
        raise ValueError("Порог сложного рельефа должен быть больше нуля.")
    for value, name in (
        (settings.max_zone_side_m, "Максимальная сторона зоны"),
        (settings.max_mission_side_m, "Максимальная сторона миссии"),
    ):
        if value < 0:
            raise ValueError(f"{name} не может быть отрицательной.")
        if 0 < value < settings.profile_spacing_m:
            raise ValueError(
                f"{name} должна быть 0 (авто) или не меньше шага профилей."
            )
