from __future__ import annotations

import math
from collections.abc import Iterable

from pyproj import CRS, Proj, Transformer
from shapely import make_valid
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.geometry.base import BaseGeometry


def validate_metric_crs(crs: CRS | str | int) -> CRS:
    result = CRS.from_user_input(crs)
    if not result.is_projected:
        raise ValueError("Рабочая CRS должна быть проекционной.")
    axis_units = {axis.unit_name.lower() for axis in result.axis_info if axis.unit_name}
    if not axis_units or not all("metre" in unit or "meter" in unit for unit in axis_units):
        raise ValueError("Единицы рабочей CRS должны быть метрами.")
    return result


def normalize_polygon(geometry: BaseGeometry) -> Polygon | MultiPolygon:
    if geometry.is_empty:
        raise ValueError("Геометрия полигона пуста.")
    fixed = make_valid(geometry) if not geometry.is_valid else geometry
    polygons = _collect_polygons(fixed)
    if not polygons:
        raise ValueError("Файл не содержит полигональной геометрии.")
    result: Polygon | MultiPolygon
    result = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    if result.is_empty or not result.is_valid:
        raise ValueError("Не удалось получить корректную геометрию полигона.")
    return result


def _collect_polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        result: list[Polygon] = []
        for part in geometry.geoms:
            result.extend(_collect_polygons(part))
        return result
    return []


def collect_lines(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 0 else []
    if isinstance(geometry, MultiLineString):
        return [line for line in geometry.geoms if line.length > 0]
    if isinstance(geometry, GeometryCollection):
        result: list[LineString] = []
        for part in geometry.geoms:
            result.extend(collect_lines(part))
        return result
    return []


def direction_vectors(azimuth_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    angle = math.radians(azimuth_deg % 360.0)
    direction = (math.sin(angle), math.cos(angle))
    normal = (-direction[1], direction[0])
    return direction, normal


def true_to_grid_azimuth(
    true_azimuth_deg: float,
    working_crs: CRS | str | int,
    reference_x: float,
    reference_y: float,
) -> float:
    """Convert a true-north bearing to the projected CRS grid bearing."""
    crs = CRS.from_user_input(working_crs)
    longitude, latitude = Transformer.from_crs(
        crs, CRS.from_epsg(4326), always_xy=True
    ).transform(reference_x, reference_y)
    convergence = Proj(crs).get_factors(
        longitude, latitude
    ).meridian_convergence
    return (true_azimuth_deg - convergence) % 360.0


def dot(point: tuple[float, float], vector: tuple[float, float]) -> float:
    return point[0] * vector[0] + point[1] * vector[1]


def orient_line(
    line: LineString, direction: tuple[float, float], positive: bool = True
) -> LineString:
    coordinates = list(line.coords)
    projection = dot(coordinates[-1], direction) - dot(coordinates[0], direction)
    should_reverse = (projection < 0) == positive
    return LineString(reversed(coordinates)) if should_reverse else line


def polygon_vertices(geometry: Polygon | MultiPolygon) -> Iterable[tuple[float, float]]:
    polygons = [geometry] if isinstance(geometry, Polygon) else geometry.geoms
    for polygon in polygons:
        yield from polygon.exterior.coords
