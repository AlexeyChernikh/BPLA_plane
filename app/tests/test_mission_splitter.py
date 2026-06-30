from __future__ import annotations

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, Polygon

from app.core.mission_splitter import split_missions
from app.core.models import PlannerSettings, Profile


def _settings(max_length: float) -> PlannerSettings:
    return PlannerSettings(
        working_crs=CRS.from_epsg(32647),
        speed_mps=1,
        max_flight_time_min=max_length / 60,
        battery_reserve_percent=0,
        waypoint_step_m=1000,
    )


def test_split_respects_full_home_route_limit_and_zones_cover_polygon() -> None:
    polygon = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    profiles = [
        Profile(1, 0, LineString([(0, 0), (0, 100)])),
        Profile(2, 50, LineString([(50, 0), (50, 100)])),
        Profile(3, 100, LineString([(100, 0), (100, 100)])),
    ]
    missions = split_missions(polygon, profiles, Point(0, 0), _settings(350))
    assert len(missions) >= 2
    assert all(mission.route_length_m <= 350 for mission in missions)
    union = missions[0].zone
    for mission in missions[1:]:
        union = union.union(mission.zone)
    assert union.symmetric_difference(polygon).area == pytest.approx(0)


def test_single_profile_over_limit_is_error() -> None:
    polygon = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    profiles = [Profile(1, 0, LineString([(0, 0), (0, 100)]))]
    with pytest.raises(ValueError, match="не помещается"):
        split_missions(polygon, profiles, Point(1000, 1000), _settings(100))
