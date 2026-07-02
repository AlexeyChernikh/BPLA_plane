from __future__ import annotations

import numpy as np
import pytest

from app.core import terrain as terrain_module
from app.core.terrain import TerrainModel

import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon

def _write_dem(path, data: np.ndarray, nodata: float = -9999) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype="float32",
        crs="EPSG:32647",
        transform=from_origin(0, 1000, 10, 10),
        nodata=nodata,
    ) as dataset:
        dataset.write(data.astype("float32"), 1)


def test_dem_bilinear_sampling_and_relief(tmp_path) -> None:
    rows, columns = np.mgrid[0:100, 0:100]
    data = 100 + columns * 2 + rows
    path = tmp_path / "dem.tif"
    _write_dem(path, data)
    polygon = Polygon([(100, 100), (900, 100), (900, 900), (100, 900)])
    terrain = TerrainModel(path, CRS.from_epsg(32647), polygon)
    sample = terrain.sample(Point(205, 795))
    assert sample.elevation_m == pytest.approx(161.5, abs=0.1)
    assert sample.slope_deg > 0
    assert terrain.relief(polygon) > 150


def test_dem_reuses_exact_point_sample(tmp_path, monkeypatch) -> None:
    rows, columns = np.mgrid[0:100, 0:100]
    path = tmp_path / "dem.tif"
    _write_dem(path, 100 + columns * 2 + rows)
    terrain = TerrainModel(path, CRS.from_epsg(32647))
    point = Point(205, 795)
    gradient_calls = 0
    original_gradient = terrain_module.np.gradient

    def counted_gradient(*args, **kwargs):
        nonlocal gradient_calls
        gradient_calls += 1
        return original_gradient(*args, **kwargs)

    monkeypatch.setattr(terrain_module.np, "gradient", counted_gradient)

    first = terrain.sample(point)
    second = terrain.sample(Point(point.x, point.y))

    assert second is first
    assert gradient_calls == 1


def test_dem_rejects_missing_polygon_coverage(tmp_path) -> None:
    path = tmp_path / "dem.tif"
    _write_dem(path, np.ones((100, 100), dtype=np.float32))
    outside = Polygon([(900, 900), (1100, 900), (1100, 1100), (900, 1100)])
    with pytest.raises(ValueError, match="не покрывает"):
        TerrainModel(path, "EPSG:32647", outside)


def test_dem_rejects_nodata_inside_polygon(tmp_path) -> None:
    data = np.ones((100, 100), dtype=np.float32)
    data[40:60, 40:60] = -9999
    path = tmp_path / "dem.tif"
    _write_dem(path, data)
    polygon = Polygon([(300, 300), (700, 300), (700, 700), (300, 700)])
    with pytest.raises(ValueError, match="NoData"):
        TerrainModel(path, "EPSG:32647", polygon)
