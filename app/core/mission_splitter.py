from __future__ import annotations

from collections.abc import Sequence
from shapely import voronoi_polygons
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from .geometry_utils import (
    direction_vectors,
    dot,
    normalize_polygon,
    polygon_vertices,
)
from .models import Mission, PlannerSettings, Profile
from .route_builder import build_route, build_terrain_route
from .terrain import TerrainModel
from .terrain_route import terrain_route_metrics


def split_missions(
    polygon: Polygon | MultiPolygon,
    profiles: Sequence[Profile],
    home: Point,
    settings: PlannerSettings,
    terrain: TerrainModel | None = None,
    zone_id: int = 1,
    home_id: int = 1,
) -> list[Mission]:
    if not profiles:
        raise ValueError("Внутри полигона не построено ни одного профиля.")
    if settings.max_route_length_m <= 0:
        raise ValueError("Доступная длина маршрута должна быть больше нуля.")

    batches = [
        [profile]
        for profile in sorted(
            profiles, key=lambda profile: (profile.offset_m, profile.id)
        )
    ]
    groups: list[list[Profile]] = []
    current: list[Profile] = []

    for batch in batches:
        candidate = current + batch
        _, length, duration, _ = _route(candidate, home, settings, terrain)
        if _fits(length, duration, settings, terrain):
            current = candidate
            continue
        if not current:
            raise ValueError(
                "Профиль или группа профилей на одной линии не помещается "
                "в доступный лимит батареи."
            )
        groups.append(current)
        current = list(batch)
        _, length, duration, _ = _route(current, home, settings, terrain)
        if not _fits(length, duration, settings, terrain):
            raise ValueError(
                "Профиль или группа профилей на одной линии не помещается "
                "в доступный лимит батареи."
            )
    if current:
        groups.append(current)

    missions: list[Mission] = []
    for index, group in enumerate(groups, start=1):
        points, length, duration, elevations = _route(
            group, home, settings, terrain
        )
        missions.append(
            Mission(
                id=index,
                profiles=group,
                route_points=points,
                route_length_m=length,
                estimated_time_min=duration / 60.0,
                zone_id=zone_id,
                home_id=home_id,
                terrain_elevations_m=elevations,
            )
        )
    if terrain is None:
        _assign_zones(polygon, missions, settings.azimuth_deg)
    else:
        _assign_mission_partitions(polygon, missions)
    return missions


def _route(
    profiles: Sequence[Profile],
    home: Point,
    settings: PlannerSettings,
    terrain: TerrainModel | None,
) -> tuple[list[Point], float, float, list[float]]:
    if terrain is None:
        points, length = build_route(
            profiles,
            home,
            settings.azimuth_deg,
            settings.route_mode,
            settings.waypoint_step_m,
        )
        return points, length, length / settings.speed_mps, []
    points, _ = build_terrain_route(
        profiles,
        home,
        settings.azimuth_deg,
        settings.route_mode,
        terrain,
        settings.altitude_m,
        settings.speed_mps,
        settings.climb_speed_mps,
        settings.descent_speed_mps,
        settings.terrain_adjust_tolerance_m,
    )
    terrain_length, duration, elevations = terrain_route_metrics(
        home, points, settings, terrain
    )
    return points, terrain_length, duration, elevations


def _fits(
    length: float,
    duration: float,
    settings: PlannerSettings,
    terrain: TerrainModel | None,
) -> bool:
    if terrain is None:
        return length <= settings.max_route_length_m + 1e-7
    return duration <= settings.usable_time_sec + 1e-7


def _assign_mission_partitions(
    polygon: Polygon | MultiPolygon, missions: list[Mission]
) -> None:
    """Partition an operational zone completely between its battery missions."""
    if not missions:
        return
    if len(missions) == 1:
        missions[0].zone = polygon
        return
    seeds = [
        unary_union(
            [profile.geometry for profile in mission.profiles]
        ).representative_point()
        for mission in missions
    ]
    # Coincident centroids are rare, but a deterministic millimetre offset keeps
    # the Voronoi operation valid for symmetric/duplicated work geometries.
    unique_seeds: list[Point] = []
    occupied: set[tuple[int, int]] = set()
    for index, seed in enumerate(seeds):
        key = (round(seed.x * 1000), round(seed.y * 1000))
        if key in occupied:
            seed = Point(seed.x + (index + 1) * 0.001, seed.y)
            key = (round(seed.x * 1000), round(seed.y * 1000))
        occupied.add(key)
        unique_seeds.append(seed)
    cells = list(
        voronoi_polygons(
            MultiPoint(unique_seeds),
            extend_to=polygon.envelope.buffer(1),
        ).geoms
    )
    unused = set(range(len(cells)))
    for mission, seed in zip(missions, unique_seeds):
        containing = [
            index for index in unused if cells[index].covers(seed)
        ]
        if containing:
            cell_index = max(
                containing,
                key=lambda index: cells[index].boundary.distance(seed),
            )
        else:
            cell_index = min(
                unused, key=lambda index: cells[index].distance(seed)
            )
        unused.remove(cell_index)
        mission.zone = normalize_polygon(cells[cell_index].intersection(polygon))


def _assign_zones(
    polygon: Polygon | MultiPolygon,
    missions: list[Mission],
    azimuth_deg: float,
) -> None:
    direction, normal = direction_vectors(azimuth_deg)
    vertices = list(polygon_vertices(polygon))
    along_values = [dot(vertex, direction) for vertex in vertices]
    normal_values = [dot(vertex, normal) for vertex in vertices]
    margin = max(
        max(along_values) - min(along_values),
        max(normal_values) - min(normal_values),
    ) + 1.0

    for index, mission in enumerate(missions):
        low = min(normal_values) - margin
        high = max(normal_values) + margin
        if index > 0:
            low = (
                missions[index - 1].profiles[-1].offset_m
                + mission.profiles[0].offset_m
            ) / 2.0
        if index < len(missions) - 1:
            high = (
                mission.profiles[-1].offset_m
                + missions[index + 1].profiles[0].offset_m
            ) / 2.0
        mission.zone = polygon.intersection(
            _oriented_slab(
                low,
                high,
                min(along_values) - margin,
                max(along_values) + margin,
                direction,
                normal,
            )
        )


def _oriented_slab(
    low: float,
    high: float,
    along_min: float,
    along_max: float,
    direction: tuple[float, float],
    normal: tuple[float, float],
) -> Polygon:
    def coordinate(along: float, across: float) -> tuple[float, float]:
        return (
            direction[0] * along + normal[0] * across,
            direction[1] * along + normal[1] * across,
        )

    return Polygon(
        [
            coordinate(along_min, low),
            coordinate(along_max, low),
            coordinate(along_max, high),
            coordinate(along_min, high),
        ]
    )
