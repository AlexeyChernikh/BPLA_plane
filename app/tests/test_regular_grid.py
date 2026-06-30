from __future__ import annotations

import pytest
from pyproj import CRS, Geod, Transformer
from shapely import affinity
from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.core.regular_grid import automatic_grid_sizes, build_regular_grid
from app.core.geometry_utils import direction_vectors, true_to_grid_azimuth


def test_regular_grid_builds_equal_squares_without_gaps() -> None:
    polygon = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
    cells = build_regular_grid(polygon, 250, azimuth_deg=0)
    assert len(cells) == 16
    assert all(cell.geometry.area == pytest.approx(250**2) for cell in cells)
    assert all(not cell.edge_clipped for cell in cells)
    assert unary_union([cell.geometry for cell in cells]).equals(polygon)


@pytest.mark.parametrize("azimuth", [0, 45, 90])
def test_regular_grid_is_aligned_with_azimuth_and_clipped_at_edges(
    azimuth: float,
) -> None:
    base = Polygon([(-500, -500), (500, -500), (500, 500), (-500, 500)])
    polygon = affinity.rotate(base, -azimuth, origin=(0, 0))
    cells = build_regular_grid(polygon, 300, azimuth)
    union = unary_union([cell.geometry for cell in cells])
    assert union.symmetric_difference(polygon).area < 1e-6
    assert any(cell.edge_clipped for cell in cells)
    assert all(cell.geometry.area <= 300**2 + 1e-6 for cell in cells)


def test_automatic_sizes_respect_manual_limits_and_multiplicity() -> None:
    mission, zone, multiplier = automatic_grid_sizes(
        profile_spacing_m=75,
        speed_mps=5,
        usable_time_sec=1800,
        agl_m=110,
        climb_speed_mps=3,
        descent_speed_mps=2,
        max_zone_side_m=1200,
        max_mission_side_m=500,
    )
    assert mission <= 500
    assert zone <= 1200
    assert zone == pytest.approx(mission * multiplier)


def test_true_north_azimuth_removes_projected_grid_convergence() -> None:
    crs = CRS.from_epsg(28417)
    to_working = Transformer.from_crs(4326, crs, always_xy=True)
    to_wgs = Transformer.from_crs(crs, 4326, always_xy=True)
    x, y = to_working.transform(96.56, 53.44)
    grid_azimuth = true_to_grid_azimuth(0, crs, x, y)
    assert grid_azimuth == pytest.approx(1.96, abs=0.05)
    direction, _ = direction_vectors(grid_azimuth)
    start_lon, start_lat = to_wgs.transform(x, y)
    end_lon, end_lat = to_wgs.transform(
        x + direction[0] * 1000,
        y + direction[1] * 1000,
    )
    bearing, _, _ = Geod(ellps="WGS84").inv(
        start_lon, start_lat, end_lon, end_lat
    )
    assert min(bearing % 360, 360 - bearing % 360) < 0.02
