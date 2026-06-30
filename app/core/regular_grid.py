from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Polygon

from .geometry_utils import direction_vectors, dot, normalize_polygon, polygon_vertices


@dataclass(frozen=True)
class GridCell:
    row: int
    col: int
    nominal_side_m: float
    geometry: Polygon | MultiPolygon
    edge_clipped: bool


def build_regular_grid(
    polygon: Polygon | MultiPolygon,
    side_m: float,
    azimuth_deg: float,
) -> list[GridCell]:
    """Build globally anchored square cells aligned with the flight azimuth."""
    if side_m <= 0:
        raise ValueError("Сторона регулярной сетки должна быть больше нуля.")
    direction, normal = direction_vectors(azimuth_deg)
    vertices = list(polygon_vertices(polygon))
    along = [dot(vertex, direction) for vertex in vertices]
    across = [dot(vertex, normal) for vertex in vertices]
    row_min = math.floor(min(along) / side_m)
    row_max = math.ceil(max(along) / side_m) - 1
    col_min = math.floor(min(across) / side_m)
    col_max = math.ceil(max(across) / side_m) - 1
    cells: list[GridCell] = []

    for row in range(row_min, row_max + 1):
        for col in range(col_min, col_max + 1):
            square = _square(row, col, side_m, direction, normal)
            clipped = square.intersection(polygon)
            if clipped.is_empty or clipped.area <= 1e-6:
                continue
            geometry = normalize_polygon(clipped)
            cells.append(
                GridCell(
                    row=row,
                    col=col,
                    nominal_side_m=side_m,
                    geometry=geometry,
                    edge_clipped=geometry.area < side_m * side_m * (1 - 1e-7),
                )
            )
    return cells


def automatic_grid_sizes(
    profile_spacing_m: float,
    speed_mps: float,
    usable_time_sec: float,
    agl_m: float,
    climb_speed_mps: float,
    descent_speed_mps: float,
    max_zone_side_m: float = 0.0,
    max_mission_side_m: float = 0.0,
) -> tuple[float, float, int]:
    overhead = agl_m / climb_speed_mps + agl_m / descent_speed_mps
    horizontal_budget = speed_mps * max(usable_time_sec - overhead, 1.0)
    estimated_mission_side = math.sqrt(
        max(profile_spacing_m**2, horizontal_budget * profile_spacing_m * 0.55)
    )
    if max_mission_side_m > 0:
        estimated_mission_side = min(
            estimated_mission_side, max_mission_side_m
        )
    if max_zone_side_m > 0:
        estimated_mission_side = min(
            estimated_mission_side, max_zone_side_m
        )
    mission_side = max(
        profile_spacing_m,
        math.floor(estimated_mission_side / profile_spacing_m)
        * profile_spacing_m,
    )
    reachable_zone_side = horizontal_budget * 0.48
    if max_zone_side_m > 0:
        reachable_zone_side = min(reachable_zone_side, max_zone_side_m)
    multiplier = max(1, math.floor(reachable_zone_side / mission_side))
    return mission_side, mission_side * multiplier, multiplier


def _square(
    row: int,
    col: int,
    side_m: float,
    direction: tuple[float, float],
    normal: tuple[float, float],
) -> Polygon:
    def coordinate(along: float, across: float) -> tuple[float, float]:
        return (
            direction[0] * along + normal[0] * across,
            direction[1] * along + normal[1] * across,
        )

    low_along, high_along = row * side_m, (row + 1) * side_m
    low_across, high_across = col * side_m, (col + 1) * side_m
    return Polygon(
        [
            coordinate(low_along, low_across),
            coordinate(high_along, low_across),
            coordinate(high_along, high_across),
            coordinate(low_along, high_across),
        ]
    )
