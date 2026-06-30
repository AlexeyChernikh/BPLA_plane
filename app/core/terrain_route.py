from __future__ import annotations

from collections.abc import Sequence

from shapely.geometry import LineString, Point

from .models import PlannerSettings
from .terrain import TerrainModel


def terrain_route_metrics(
    home: Point,
    route_points: Sequence[Point],
    settings: PlannerSettings,
    terrain: TerrainModel,
) -> tuple[float, float, list[float]]:
    """Return horizontal length, conservative duration and route elevations."""
    if not route_points:
        return 0.0, 0.0, []
    points = list(route_points)
    elevations = terrain.elevations(points)
    home_elevation = terrain.sample(home).elevation_m
    coordinates = [(home.x, home.y)]
    coordinates.extend((point.x, point.y) for point in points)
    coordinates.append((home.x, home.y))
    horizontal_length = LineString(coordinates).length
    terrain_levels = [home_elevation, *elevations, home_elevation]
    climb = sum(
        max(0.0, current - previous)
        for previous, current in zip(terrain_levels, terrain_levels[1:])
    )
    descent = sum(
        max(0.0, previous - current)
        for previous, current in zip(terrain_levels, terrain_levels[1:])
    )
    duration = (
        horizontal_length / settings.speed_mps
        + settings.altitude_m / settings.climb_speed_mps
        + settings.altitude_m / settings.descent_speed_mps
        + climb / settings.climb_speed_mps
        + descent / settings.descent_speed_mps
    )
    return horizontal_length, duration, elevations


def approximate_segment_duration(
    home: Point,
    home_elevation: float,
    start: Point,
    end: Point,
    start_elevation: float,
    end_elevation: float,
    settings: PlannerSettings,
) -> float:
    horizontal = min(
        home.distance(start) + start.distance(end) + end.distance(home),
        home.distance(end) + end.distance(start) + start.distance(home),
    )
    forward_levels = [home_elevation, start_elevation, end_elevation, home_elevation]
    reverse_levels = [home_elevation, end_elevation, start_elevation, home_elevation]

    def vertical_time(levels: list[float]) -> float:
        result = settings.altitude_m / settings.climb_speed_mps
        result += settings.altitude_m / settings.descent_speed_mps
        for previous, current in zip(levels, levels[1:]):
            if current >= previous:
                result += (current - previous) / settings.climb_speed_mps
            else:
                result += (previous - current) / settings.descent_speed_mps
        return result

    return horizontal / settings.speed_mps + min(
        vertical_time(forward_levels), vertical_time(reverse_levels)
    )
