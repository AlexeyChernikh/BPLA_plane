from __future__ import annotations

import math
from collections.abc import Sequence

from shapely import voronoi_polygons
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon
from shapely.ops import substring

from .geometry_utils import collect_lines, normalize_polygon
from .models import PlannerSettings, Profile, TakeoffSite
from .terrain import TerrainModel
from .terrain_route import approximate_segment_duration


def work_unit_length(settings: PlannerSettings) -> float:
    overhead = (
        settings.altitude_m / settings.climb_speed_mps
        + settings.altitude_m / settings.descent_speed_mps
    )
    horizontal_budget = max(
        settings.waypoint_step_m,
        settings.speed_mps * max(settings.usable_time_sec - overhead, 1.0),
    )
    return min(1000.0, max(settings.waypoint_step_m, horizontal_budget * 0.2))


def split_profiles(
    profiles: Sequence[Profile], maximum_length_m: float, start_id: int = 1
) -> list[Profile]:
    result: list[Profile] = []
    identifier = start_id
    for profile in profiles:
        line = profile.geometry
        count = max(1, math.ceil(line.length / maximum_length_m))
        for part_index in range(count):
            start = line.length * part_index / count
            end = line.length * (part_index + 1) / count
            part = substring(line, start, end)
            if isinstance(part, LineString) and part.length > 0:
                result.append(Profile(identifier, profile.offset_m, part))
                identifier += 1
    return result


def select_takeoff_sites(
    polygon: Polygon | MultiPolygon,
    profiles: Sequence[Profile],
    terrain: TerrainModel,
    settings: PlannerSettings,
) -> list[TakeoffSite]:
    units = split_profiles(profiles, work_unit_length(settings))
    if not units:
        raise ValueError("Нет профилей для расчёта точек взлёта.")
    unit_data = []
    for unit in units:
        start, end = Point(unit.geometry.coords[0]), Point(unit.geometry.coords[-1])
        unit_data.append(
            (
                start,
                end,
                terrain.sample(start).elevation_m,
                terrain.sample(end).elevation_m,
            )
        )

    candidates = _generate_candidates(polygon, terrain, settings)
    if not candidates:
        raise ValueError("В области поиска не найдено допустимых точек Home.")
    coverage: list[set[int]] = []
    for candidate in candidates:
        covered: set[int] = set()
        for index, (start, end, start_z, end_z) in enumerate(unit_data):
            duration = approximate_segment_duration(
                candidate.point,
                candidate.elevation_m,
                start,
                end,
                start_z,
                end_z,
                settings,
            )
            if duration <= settings.usable_time_sec + 1e-7:
                covered.add(index)
        coverage.append(covered)

    uncovered = set(range(len(units)))
    selected: list[int] = []
    while uncovered:
        ranked = []
        for index, candidate in enumerate(candidates):
            newly_covered = coverage[index] & uncovered
            if not newly_covered:
                continue
            transit = sum(
                candidate.point.distance(units[unit_index].geometry.centroid)
                for unit_index in newly_covered
            ) / len(newly_covered)
            ranked.append(
                (
                    -len(newly_covered),
                    candidate.slope_deg,
                    candidate.roughness_m,
                    transit,
                    index,
                )
            )
        if not ranked:
            missing = units[min(uncovered)]
            raise ValueError(
                "Часть участка недостижима при заданных параметрах батареи: "
                f"профиль {missing.id}."
            )
        chosen = min(ranked)[-1]
        selected.append(chosen)
        uncovered -= coverage[chosen]

    return [
        TakeoffSite(
            id=identifier,
            point=candidates[index].point,
            elevation_m=candidates[index].elevation_m,
            slope_deg=candidates[index].slope_deg,
            roughness_m=candidates[index].roughness_m,
        )
        for identifier, index in enumerate(selected, start=1)
    ]


def build_voronoi_zones(
    polygon: Polygon | MultiPolygon, sites: Sequence[TakeoffSite]
) -> list[Polygon | MultiPolygon]:
    if not sites:
        raise ValueError("Не задано ни одной точки Home.")
    if len(sites) == 1:
        return [polygon]
    collection = voronoi_polygons(
        MultiPoint([site.point for site in sites]),
        extend_to=polygon.envelope.buffer(1),
    )
    cells = list(collection.geoms)
    result: list[Polygon | MultiPolygon] = []
    unused = set(range(len(cells)))
    for site in sites:
        containing = [
            index for index in unused if cells[index].covers(site.point)
        ]
        if containing:
            cell_index = max(
                containing,
                key=lambda index: cells[index].boundary.distance(site.point),
            )
        else:
            cell_index = min(
                unused, key=lambda index: cells[index].distance(site.point)
            )
        unused.remove(cell_index)
        cell = cells[cell_index]
        clipped = cell.intersection(polygon)
        result.append(normalize_polygon(clipped))
    return result


def profiles_for_zone(
    base_profiles: Sequence[Profile],
    zone: Polygon | MultiPolygon,
    maximum_length_m: float,
    start_id: int,
) -> list[Profile]:
    clipped: list[Profile] = []
    identifier = start_id
    for profile in base_profiles:
        for line in collect_lines(profile.geometry.intersection(zone)):
            if line.length <= 0:
                continue
            clipped.extend(
                split_profiles(
                    [Profile(identifier, profile.offset_m, line)],
                    maximum_length_m,
                    identifier,
                )
            )
            identifier = (clipped[-1].id + 1) if clipped else identifier
    return clipped


def _generate_candidates(
    polygon: Polygon | MultiPolygon,
    terrain: TerrainModel,
    settings: PlannerSettings,
) -> list[TakeoffSite]:
    search = polygon.buffer(settings.home_search_buffer_m).intersection(terrain.bounds)
    spacing = min(1000.0, max(250.0, settings.max_route_length_m / 5.0))
    minx, miny, maxx, maxy = search.bounds
    first_x = math.ceil(minx / spacing) * spacing
    first_y = math.ceil(miny / spacing) * spacing
    points: list[Point] = []
    x = first_x
    while x <= maxx:
        y = first_y
        while y <= maxy:
            point = Point(x, y)
            if search.covers(point):
                points.append(point)
            y += spacing
        x += spacing
    points.extend(
        [
            polygon.centroid,
            polygon.representative_point(),
        ]
    )
    candidates: list[TakeoffSite] = []
    for point in points:
        try:
            sample = terrain.sample(point)
        except ValueError:
            continue
        candidates.append(
            TakeoffSite(
                id=len(candidates) + 1,
                point=point,
                elevation_m=sample.elevation_m,
                slope_deg=sample.slope_deg,
                roughness_m=sample.roughness_m,
            )
        )
    return candidates
