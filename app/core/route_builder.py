from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby

from shapely.geometry import LineString, Point

from .geometry_utils import direction_vectors, orient_line
from .models import Profile, RouteMode
from .terrain import TerrainModel


def build_route(
    profiles: Sequence[Profile],
    home: Point,
    azimuth_deg: float,
    mode: RouteMode,
    waypoint_step_m: float,
) -> tuple[list[Point], float]:
    if not profiles:
        return [], 0.0
    if waypoint_step_m <= 0:
        raise ValueError("Шаг waypoint должен быть больше нуля.")
    direction, _ = direction_vectors(azimuth_deg)
    ordered = sorted(profiles, key=lambda profile: (profile.offset_m, profile.id))
    profile_groups = [
        list(group)
        for _, group in groupby(ordered, key=lambda profile: profile.offset_m)
    ]

    variants: list[list[Point]] = []
    for reverse_order in (False, True):
        sequence = (
            list(reversed(profile_groups)) if reverse_order else profile_groups
        )
        for first_positive in (True, False):
            points: list[Point] = []
            for index, same_line_profiles in enumerate(sequence):
                positive = first_positive
                if mode == "snake" and index % 2:
                    positive = not positive
                same_line_profiles = sorted(
                    same_line_profiles,
                    key=lambda profile: (
                        profile.geometry.centroid.x * direction[0]
                        + profile.geometry.centroid.y * direction[1]
                    ),
                    reverse=not positive,
                )
                for profile in same_line_profiles:
                    line = orient_line(
                        profile.geometry, direction, positive=positive
                    )
                    profile_points = densify_line(line, waypoint_step_m)
                    if points and points[-1].equals(profile_points[0]):
                        profile_points = profile_points[1:]
                    points.extend(profile_points)
            variants.append(points)

    best = min(variants, key=lambda points: full_route_length(home, points))
    return best, full_route_length(home, best)


def build_terrain_route(
    profiles: Sequence[Profile],
    home: Point,
    azimuth_deg: float,
    mode: RouteMode,
    terrain: TerrainModel,
    agl_m: float,
    speed_mps: float,
    climb_speed_mps: float,
    descent_speed_mps: float,
    tolerance_m: float,
) -> tuple[list[Point], float]:
    if not profiles:
        return [], 0.0
    if speed_mps <= 0:
        raise ValueError("Скорость должна быть больше нуля.")
    if tolerance_m < 0:
        raise ValueError("Допуск рельефа не может быть отрицательным.")
    direction, _ = direction_vectors(azimuth_deg)
    ordered = sorted(profiles, key=lambda profile: (profile.offset_m, profile.id))
    profile_groups = [
        list(group)
        for _, group in groupby(ordered, key=lambda profile: profile.offset_m)
    ]

    variants: list[list[Point]] = []
    for reverse_order in (False, True):
        sequence = (
            list(reversed(profile_groups)) if reverse_order else profile_groups
        )
        for first_positive in (True, False):
            points: list[Point] = []
            for index, same_line_profiles in enumerate(sequence):
                positive = first_positive
                if mode == "snake" and index % 2:
                    positive = not positive
                same_line_profiles = sorted(
                    same_line_profiles,
                    key=lambda profile: (
                        profile.geometry.centroid.x * direction[0]
                        + profile.geometry.centroid.y * direction[1]
                    ),
                    reverse=not positive,
                )
                for profile in same_line_profiles:
                    line = orient_line(
                        profile.geometry, direction, positive=positive
                    )
                    profile_points = terrain_adjusted_line_points(
                        line,
                        terrain,
                        agl_m,
                        speed_mps,
                        climb_speed_mps,
                        descent_speed_mps,
                        tolerance_m,
                    )
                    if points and points[-1].equals(profile_points[0]):
                        profile_points = profile_points[1:]
                    points.extend(profile_points)
            variants.append(points)

    best = min(variants, key=lambda points: full_route_length(home, points))
    return best, full_route_length(home, best)


def terrain_adjusted_line_points(
    line: LineString,
    terrain: TerrainModel,
    agl_m: float,
    speed_mps: float,
    climb_speed_mps: float,
    descent_speed_mps: float,
    tolerance_m: float,
) -> list[Point]:
    samples, terrain_added = _terrain_line_samples(line, terrain)
    altitudes = [terrain.sample(point).elevation_m + agl_m for point in samples]
    _adjust_altitudes_for_max_rates(
        samples,
        altitudes,
        speed_mps,
        climb_speed_mps,
        descent_speed_mps,
    )
    return _adjust_points_for_tolerance(samples, altitudes, terrain_added, tolerance_m)


def densify_line(line: LineString, step_m: float) -> list[Point]:
    if step_m <= 0:
        raise ValueError("Шаг waypoint должен быть больше нуля.")
    result: list[Point] = []
    coordinates = list(line.coords)
    for start, end in zip(coordinates, coordinates[1:]):
        segment = LineString([start, end])
        if not result:
            result.append(Point(start))
        distance = step_m
        while distance < segment.length:
            result.append(segment.interpolate(distance))
            distance += step_m
        endpoint = Point(end)
        if not result[-1].equals(endpoint):
            result.append(endpoint)
    return result or [Point(coordinates[0])]


def _terrain_line_samples(
    line: LineString, terrain: TerrainModel
) -> tuple[list[Point], list[bool]]:
    spacing = max(terrain.resolution)
    count = max(1, int(line.length / spacing + 0.999999))
    points = [line.interpolate(line.length * index / count) for index in range(count + 1)]
    terrain_added = [False] + [True] * max(0, len(points) - 2) + ([False] if len(points) > 1 else [])
    return points, terrain_added


def _adjust_altitudes_for_max_rates(
    points: list[Point],
    altitudes: list[float],
    speed_mps: float,
    climb_speed_mps: float,
    descent_speed_mps: float,
) -> None:
    if speed_mps <= 0 or (climb_speed_mps <= 0 and descent_speed_mps <= 0):
        return
    if climb_speed_mps > 0:
        adjusted = True
        while adjusted:
            adjusted = False
            for index in range(len(points) - 1):
                seconds = points[index].distance(points[index + 1]) / speed_mps
                if seconds <= 0:
                    continue
                climb_rate = (altitudes[index + 1] - altitudes[index]) / seconds
                if climb_rate > 0 and climb_rate - climb_speed_mps > 0.1:
                    altitudes[index] = altitudes[index + 1] - climb_speed_mps * seconds
                    adjusted = True
    if descent_speed_mps > 0:
        max_descent_rate = -descent_speed_mps
        adjusted = True
        while adjusted:
            adjusted = False
            for index in range(len(points) - 1):
                seconds = points[index].distance(points[index + 1]) / speed_mps
                if seconds <= 0:
                    continue
                descent_rate = (altitudes[index + 1] - altitudes[index]) / seconds
                if descent_rate < 0 and descent_rate - max_descent_rate < -0.1:
                    altitudes[index + 1] = altitudes[index] + max_descent_rate * seconds
                    adjusted = True


def _adjust_points_for_tolerance(
    points: list[Point],
    altitudes: list[float],
    terrain_added: list[bool],
    tolerance_m: float,
) -> list[Point]:
    if not points:
        return []
    adjusted = [points[0]]
    last_altitude = altitudes[0]
    for point, altitude, added in zip(points[1:], altitudes[1:], terrain_added[1:]):
        if not added or abs(last_altitude - altitude) > tolerance_m:
            adjusted.append(point)
            last_altitude = altitude
    return adjusted


def full_route_length(home: Point, route_points: Sequence[Point]) -> float:
    if not route_points:
        return 0.0
    coordinates = [(home.x, home.y)]
    coordinates.extend((point.x, point.y) for point in route_points)
    coordinates.append((home.x, home.y))
    return LineString(coordinates).length
