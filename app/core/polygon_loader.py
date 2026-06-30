from __future__ import annotations

import json
from pathlib import Path

import pyogrio
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from .geometry_utils import normalize_polygon, validate_metric_crs
from .models import LoadedPolygon


SUPPORTED_EXTENSIONS = {".shp", ".geojson", ".json", ".gpkg", ".kml"}


def load_polygon(path: str | Path, working_crs: CRS | str | int) -> LoadedPolygon:
    source_path = Path(path)
    if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый формат: {source_path.suffix}")
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    target_crs = validate_metric_crs(working_crs)
    source_crs: CRS
    geometries: list[object]

    if source_path.suffix.lower() in {".geojson", ".json"}:
        geometries, source_crs = _read_geojson(source_path)
    else:
        frame = pyogrio.read_dataframe(source_path)
        geometries = [geometry for geometry in frame.geometry if geometry is not None]
        default = "EPSG:4326" if source_path.suffix.lower() == ".kml" else None
        if frame.crs is None and default is None:
            raise ValueError("Во входном файле не указана CRS.")
        source_crs = CRS.from_user_input(frame.crs or default)

    if not geometries:
        raise ValueError("В файле нет геометрий.")
    merged = normalize_polygon(unary_union(geometries))
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    projected = normalize_polygon(transform(transformer.transform, merged))
    return LoadedPolygon(source_path, source_crs, target_crs, projected)


def _read_geojson(path: Path) -> tuple[list[object], CRS]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    crs_value = (
        document.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
    )
    source_crs = CRS.from_user_input(crs_value)
    if document.get("type") == "FeatureCollection":
        geometries = [
            shape(feature["geometry"])
            for feature in document.get("features", [])
            if feature.get("geometry")
        ]
    elif document.get("type") == "Feature":
        geometries = [shape(document["geometry"])]
    else:
        geometries = [shape(document)]
    return geometries, source_crs
