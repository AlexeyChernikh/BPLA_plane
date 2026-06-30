from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree as ET

from pyproj import CRS, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)

from .models import Profile

KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)


def export_polygon_kml(
    path: str | Path,
    geometry: Polygon | MultiPolygon,
    working_crs: CRS | str | int,
    name: str,
) -> Path:
    document = _document(name)
    transformer = _transformer(working_crs)
    polygons = [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)
    for index, polygon in enumerate(polygons, start=1):
        placemark = ET.SubElement(document, _tag("Placemark"))
        ET.SubElement(placemark, _tag("name")).text = (
            name if len(polygons) == 1 else f"{name} {index}"
        )
        style = ET.SubElement(placemark, _tag("Style"))
        line_style = ET.SubElement(style, _tag("LineStyle"))
        ET.SubElement(line_style, _tag("color")).text = "ff0000ff"
        ET.SubElement(line_style, _tag("width")).text = "2"
        poly_style = ET.SubElement(style, _tag("PolyStyle"))
        ET.SubElement(poly_style, _tag("color")).text = "5533aaff"
        _append_polygon(placemark, polygon, transformer)
    return _write(path, document)


def export_profiles_kml(
    path: str | Path,
    profiles: Sequence[Profile],
    working_crs: CRS | str | int,
    name: str,
) -> Path:
    document = _document(name)
    transformer = _transformer(working_crs)
    for profile in profiles:
        placemark = ET.SubElement(document, _tag("Placemark"))
        ET.SubElement(placemark, _tag("name")).text = f"Profile {profile.id}"
        style = ET.SubElement(placemark, _tag("Style"))
        line_style = ET.SubElement(style, _tag("LineStyle"))
        ET.SubElement(line_style, _tag("color")).text = "ffff0000"
        ET.SubElement(line_style, _tag("width")).text = "2"
        _append_line(placemark, profile.geometry, transformer)
    return _write(path, document)


def export_route_kml(
    path: str | Path,
    points: Sequence[Point],
    elevations_amsl_m: Sequence[float],
    working_crs: CRS | str | int,
    name: str,
) -> Path:
    if len(points) != len(elevations_amsl_m):
        raise ValueError("Число высот KML не совпадает с числом точек маршрута.")
    document = _document(name)
    placemark = ET.SubElement(document, _tag("Placemark"))
    ET.SubElement(placemark, _tag("name")).text = name
    style = ET.SubElement(placemark, _tag("Style"))
    line_style = ET.SubElement(style, _tag("LineStyle"))
    ET.SubElement(line_style, _tag("color")).text = "ff00ffff"
    ET.SubElement(line_style, _tag("width")).text = "3"
    line = ET.SubElement(placemark, _tag("LineString"))
    ET.SubElement(line, _tag("altitudeMode")).text = "absolute"
    ET.SubElement(line, _tag("tessellate")).text = "1"
    transformer = _transformer(working_crs)
    coordinates = []
    for point, altitude in zip(points, elevations_amsl_m):
        longitude, latitude = transformer.transform(point.x, point.y)
        coordinates.append(f"{longitude:.10f},{latitude:.10f},{altitude:.3f}")
    ET.SubElement(line, _tag("coordinates")).text = " ".join(coordinates)
    return _write(path, document)


def export_home_kml(
    path: str | Path,
    point: Point,
    elevation_m: float,
    working_crs: CRS | str | int,
    name: str,
) -> Path:
    document = _document(name)
    placemark = ET.SubElement(document, _tag("Placemark"))
    ET.SubElement(placemark, _tag("name")).text = name
    kml_point = ET.SubElement(placemark, _tag("Point"))
    ET.SubElement(kml_point, _tag("altitudeMode")).text = "absolute"
    longitude, latitude = _transformer(working_crs).transform(point.x, point.y)
    ET.SubElement(kml_point, _tag("coordinates")).text = (
        f"{longitude:.10f},{latitude:.10f},{elevation_m:.3f}"
    )
    return _write(path, document)


def _document(name: str) -> ET.Element:
    root = ET.Element(_tag("kml"))
    document = ET.SubElement(root, _tag("Document"))
    ET.SubElement(document, _tag("name")).text = name
    return document


def _append_polygon(
    parent: ET.Element, polygon: Polygon, transformer: Transformer
) -> None:
    element = ET.SubElement(parent, _tag("Polygon"))
    ET.SubElement(element, _tag("tessellate")).text = "1"
    outer = ET.SubElement(element, _tag("outerBoundaryIs"))
    _append_ring(outer, polygon.exterior.coords, transformer)
    for interior in polygon.interiors:
        inner = ET.SubElement(element, _tag("innerBoundaryIs"))
        _append_ring(inner, interior.coords, transformer)


def _append_ring(parent, coordinates, transformer: Transformer) -> None:
    ring = ET.SubElement(parent, _tag("LinearRing"))
    values = []
    for x, y in coordinates:
        longitude, latitude = transformer.transform(x, y)
        values.append(f"{longitude:.10f},{latitude:.10f},0")
    ET.SubElement(ring, _tag("coordinates")).text = " ".join(values)


def _append_line(
    parent: ET.Element, line: LineString, transformer: Transformer
) -> None:
    element = ET.SubElement(parent, _tag("LineString"))
    ET.SubElement(element, _tag("tessellate")).text = "1"
    values = []
    for x, y in line.coords:
        longitude, latitude = transformer.transform(x, y)
        values.append(f"{longitude:.10f},{latitude:.10f},0")
    ET.SubElement(element, _tag("coordinates")).text = " ".join(values)


def _transformer(working_crs: CRS | str | int) -> Transformer:
    return Transformer.from_crs(
        CRS.from_user_input(working_crs), CRS.from_epsg(4326), always_xy=True
    )


def _write(path: str | Path, document: ET.Element) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    root = document.getroottree().getroot() if hasattr(document, "getroottree") else None
    if root is None:
        # xml.etree does not expose parents; Document is always a child of kml.
        root = ET.Element(_tag("kml"))
        root.append(document)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output


def _tag(name: str) -> str:
    return f"{{{KML_NS}}}{name}"
