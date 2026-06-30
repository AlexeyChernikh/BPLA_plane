from __future__ import annotations

import math

from shapely.geometry import LineString, MultiPolygon, Polygon

from .geometry_utils import (
    collect_lines,
    direction_vectors,
    dot,
    orient_line,
    polygon_vertices,
)
from .models import Profile


def generate_profiles(
    polygon: Polygon | MultiPolygon,
    azimuth_deg: float,
    spacing_m: float,
    extension_m: float = 0.0,
) -> list[Profile]:
    if spacing_m <= 0:
        raise ValueError("Шаг профилей должен быть больше нуля.")
    if extension_m < 0:
        raise ValueError("Вылет профиля не может быть отрицательным.")

    direction, normal = direction_vectors(azimuth_deg)
    vertices = list(polygon_vertices(polygon))
    along = [dot(vertex, direction) for vertex in vertices]
    across = [dot(vertex, normal) for vertex in vertices]
    margin = max(max(along) - min(along), max(across) - min(across)) + spacing_m
    first_index = math.ceil(min(across) / spacing_m)
    last_index = math.floor(max(across) / spacing_m)

    candidates: list[tuple[float, LineString]] = []
    for index in range(first_index, last_index + 1):
        offset = index * spacing_m
        start = (
            normal[0] * offset + direction[0] * (min(along) - margin),
            normal[1] * offset + direction[1] * (min(along) - margin),
        )
        end = (
            normal[0] * offset + direction[0] * (max(along) + margin),
            normal[1] * offset + direction[1] * (max(along) + margin),
        )
        for segment in collect_lines(polygon.intersection(LineString([start, end]))):
            oriented = orient_line(segment, direction, positive=True)
            if extension_m:
                coordinates = list(oriented.coords)
                coordinates[0] = (
                    coordinates[0][0] - direction[0] * extension_m,
                    coordinates[0][1] - direction[1] * extension_m,
                )
                coordinates[-1] = (
                    coordinates[-1][0] + direction[0] * extension_m,
                    coordinates[-1][1] + direction[1] * extension_m,
                )
                oriented = LineString(coordinates)
            candidates.append((offset, oriented))

    candidates.sort(key=lambda item: (item[0], dot(item[1].coords[0], direction)))
    return [
        Profile(id=index, offset_m=offset, geometry=line)
        for index, (offset, line) in enumerate(candidates, start=1)
    ]
