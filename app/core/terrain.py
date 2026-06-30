from __future__ import annotations

import base64
import io
import math
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping
from shapely.ops import transform

from .models import TerrainSample


class TerrainModel:
    """In-memory DEM reprojected to the planner's metric CRS."""

    def __init__(
        self,
        path: str | Path,
        working_crs: CRS | str | int,
        required_geometry: Polygon | MultiPolygon | None = None,
    ) -> None:
        self.path = Path(path)
        if self.path.suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("DEM должен быть файлом GeoTIFF (.tif или .tiff).")
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.crs = CRS.from_user_input(working_crs)
        with rasterio.open(self.path) as source:
            if source.crs is None:
                raise ValueError("В DEM не указана CRS.")
            transform_out, width, height = calculate_default_transform(
                source.crs,
                self.crs,
                source.width,
                source.height,
                *source.bounds,
            )
            data = np.full((height, width), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(source, 1),
                destination=data,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata,
                dst_transform=transform_out,
                dst_crs=self.crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
        self.data = data
        self.transform = transform_out
        self.height, self.width = data.shape
        self.bounds = box(*array_bounds(self.height, self.width, self.transform))
        self.resolution = (
            abs(float(self.transform.a)),
            abs(float(self.transform.e)),
        )
        if required_geometry is not None:
            self.validate_coverage(required_geometry)

    def validate_coverage(self, geometry: Polygon | MultiPolygon) -> None:
        missing_area = geometry.difference(self.bounds).area
        tolerance = max(self.resolution) ** 2 * 4
        if missing_area > tolerance:
            raise ValueError("DEM не покрывает исходный полигон полностью.")
        inside = geometry_mask(
            [mapping(geometry)],
            out_shape=self.data.shape,
            transform=self.transform,
            invert=True,
        )
        if np.any(inside & ~np.isfinite(self.data)):
            raise ValueError("Внутри полигона DEM содержит NoData.")

    def sample(self, point: Point) -> TerrainSample:
        col_f, row_f = (~self.transform) * (point.x, point.y)
        col0, row0 = math.floor(col_f), math.floor(row_f)
        if row0 < 0 or col0 < 0 or row0 + 1 >= self.height or col0 + 1 >= self.width:
            raise ValueError("Точка находится за пределами DEM.")
        block = self.data[row0 : row0 + 2, col0 : col0 + 2]
        if not np.all(np.isfinite(block)):
            raise ValueError("Для точки отсутствуют данные высоты DEM.")
        dx, dy = col_f - col0, row_f - row0
        elevation = float(
            block[0, 0] * (1 - dx) * (1 - dy)
            + block[0, 1] * dx * (1 - dy)
            + block[1, 0] * (1 - dx) * dy
            + block[1, 1] * dx * dy
        )
        row = min(max(round(row_f), 1), self.height - 2)
        col = min(max(round(col_f), 1), self.width - 2)
        local = self.data[row - 1 : row + 2, col - 1 : col + 2]
        valid = local[np.isfinite(local)]
        if valid.size < 4:
            return TerrainSample(elevation, 90.0, float("inf"))
        dz_dy, dz_dx = np.gradient(
            np.where(np.isfinite(local), local, elevation),
            self.resolution[1],
            self.resolution[0],
        )
        slope = math.degrees(
            math.atan(float(np.nanmax(np.hypot(dz_dx, dz_dy))))
        )
        return TerrainSample(elevation, slope, float(valid.max() - valid.min()))

    def elevations(self, points: list[Point]) -> list[float]:
        return [self.sample(point).elevation_m for point in points]

    def relief(self, geometry: Polygon | MultiPolygon) -> float:
        mask = geometry_mask(
            [mapping(geometry)],
            out_shape=self.data.shape,
            transform=self.transform,
            invert=True,
        )
        values = self.data[mask & np.isfinite(self.data)]
        return float(values.max() - values.min()) if values.size else 0.0

    def preview_overlay(self, max_size: int = 1000) -> tuple[str, list[list[float]]]:
        scale = max(1, math.ceil(max(self.width, self.height) / max_size))
        values = self.data[::scale, ::scale]
        valid = np.isfinite(values)
        rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
        if np.any(valid):
            low, high = np.nanpercentile(values, [2, 98])
            normalized = np.clip((values - low) / max(high - low, 1), 0, 1)
            normalized = np.where(valid, normalized, 0)
            rgba[..., 0] = (70 + normalized * 185).astype(np.uint8)
            rgba[..., 1] = (
                170 - np.abs(normalized - 0.45) * 170
            ).clip(45, 190).astype(np.uint8)
            rgba[..., 2] = (45 + (1 - normalized) * 80).astype(np.uint8)
            rgba[..., 3] = np.where(valid, 150, 0).astype(np.uint8)
        image = Image.fromarray(rgba, "RGBA")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        uri = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
        minx, miny, maxx, maxy = self.bounds.bounds
        to_wgs = Transformer.from_crs(self.crs, 4326, always_xy=True)
        west, south = to_wgs.transform(minx, miny)
        east, north = to_wgs.transform(maxx, maxy)
        return uri, [[south, west], [north, east]]
