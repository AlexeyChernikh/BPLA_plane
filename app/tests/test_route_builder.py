from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point

from app.core.models import Profile, TerrainSample
from app.core.route_builder import (
    _adjust_altitudes_for_max_rates,
    build_route,
    densify_line,
    terrain_adjusted_line_points,
)


class _Terrain:
    resolution = (10.0, 10.0)

    def __init__(self, elevation) -> None:
        self._elevation = elevation

    def sample(self, point: Point) -> TerrainSample:
        return TerrainSample(float(self._elevation(point)), 0.0, 0.0)


def _profiles() -> list[Profile]:
    return [
        Profile(1, 0, LineString([(0, 0), (0, 100)])),
        Profile(2, 20, LineString([(20, 0), (20, 100)])),
        Profile(3, 40, LineString([(40, 0), (40, 100)])),
    ]


def test_densify_preserves_ends_and_step() -> None:
    points = densify_line(LineString([(0, 0), (0, 105)]), 25)
    assert [(point.x, point.y) for point in points] == [
        (0, 0),
        (0, 25),
        (0, 50),
        (0, 75),
        (0, 100),
        (0, 105),
    ]


def test_snake_alternates_profile_direction() -> None:
    points, length = build_route(_profiles(), Point(0, 0), 0, "snake", 1000)
    profile_ends = [(point.x, point.y) for point in points]
    assert profile_ends[:4] == [(0, 0), (0, 100), (20, 100), (20, 0)]
    assert length == pytest.approx(340 + (40**2 + 100**2) ** 0.5)


def test_one_way_uses_same_direction() -> None:
    points, _ = build_route(_profiles(), Point(0, 0), 0, "one-way", 1000)
    vertical_pairs = list(zip(points[::2], points[1::2]))
    signs = [end.y - start.y for start, end in vertical_pairs]
    assert all(sign > 0 for sign in signs) or all(sign < 0 for sign in signs)


def test_terrain_adjustment_keeps_only_profile_ends_on_flat_dem() -> None:
    line = LineString([(0, 0), (100, 0)])
    points = terrain_adjusted_line_points(
        line,
        _Terrain(lambda point: 100),
        agl_m=110,
        speed_mps=5,
        climb_speed_mps=3,
        descent_speed_mps=2,
        tolerance_m=10,
    )
    assert [(point.x, point.y) for point in points] == [(0, 0), (100, 0)]


def test_terrain_adjustment_adds_points_for_height_changes() -> None:
    line = LineString([(0, 0), (100, 0)])
    points = terrain_adjusted_line_points(
        line,
        _Terrain(lambda point: point.x / 2),
        agl_m=110,
        speed_mps=5,
        climb_speed_mps=0,
        descent_speed_mps=0,
        tolerance_m=10,
    )
    assert [(point.x, point.y) for point in points] == [
        (0, 0),
        (30, 0),
        (60, 0),
        (90, 0),
        (100, 0),
    ]


def test_qgc_rate_adjustment_limits_climb_and_descent() -> None:
    points = [Point(0, 0), Point(10, 0), Point(20, 0)]
    altitudes = [100.0, 130.0, 100.0]
    _adjust_altitudes_for_max_rates(
        points,
        altitudes,
        speed_mps=5,
        climb_speed_mps=3,
        descent_speed_mps=2,
    )
    rates = [
        (end - start) / (points[index].distance(points[index + 1]) / 5)
        for index, (start, end) in enumerate(zip(altitudes, altitudes[1:]))
    ]
    assert all(rate <= 3.1 for rate in rates)
    assert all(rate >= -2.1 for rate in rates)
