from __future__ import annotations

import pytest
from shapely.geometry import MultiPolygon, Polygon

from app.core.grid_generator import generate_profiles
from app.core.takeoff_optimizer import profiles_for_zone


def test_profiles_use_exact_common_grid_spacing() -> None:
    polygon = Polygon([(10, 10), (1010, 10), (1010, 1010), (10, 1010)])
    profiles = generate_profiles(polygon, azimuth_deg=0, spacing_m=75)
    offsets = sorted({profile.offset_m for profile in profiles})
    assert len(offsets) > 10
    assert all(
        current - previous == pytest.approx(75)
        for previous, current in zip(offsets, offsets[1:])
    )
    assert all(offset / 75 == pytest.approx(round(offset / 75)) for offset in offsets)


def test_extension_is_added_to_both_ends() -> None:
    polygon = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    original = generate_profiles(polygon, 0, 50, 0)
    extended = generate_profiles(polygon, 0, 50, 10)
    original_by_offset = {profile.offset_m: profile for profile in original}
    extended_by_offset = {profile.offset_m: profile for profile in extended}
    for offset in original_by_offset:
        assert extended_by_offset[offset].geometry.length == pytest.approx(
            original_by_offset[offset].geometry.length + 20
        )


def test_extension_is_added_after_clipping_to_each_mission() -> None:
    base_profiles = generate_profiles(
        Polygon([(0, 0), (100, 0), (100, 200), (0, 200)]),
        azimuth_deg=0,
        spacing_m=50,
        extension_m=10,
    )
    mission = Polygon([(0, 50), (100, 50), (100, 150), (0, 150)])

    profiles = profiles_for_zone(base_profiles, mission, 1e12, 1, extension_m=10)

    assert profiles
    assert all(profile.geometry.length == pytest.approx(120) for profile in profiles)
    assert all(profile.geometry.bounds[1] == pytest.approx(40) for profile in profiles)
    assert all(profile.geometry.bounds[3] == pytest.approx(160) for profile in profiles)


def test_multipolygon_keeps_all_components() -> None:
    polygon = MultiPolygon(
        [
            Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]),
            Polygon([(200, 0), (300, 0), (300, 100), (200, 100)]),
        ]
    )
    profiles = generate_profiles(polygon, 0, 50)
    assert any(profile.geometry.centroid.x < 150 for profile in profiles)
    assert any(profile.geometry.centroid.x > 150 for profile in profiles)
