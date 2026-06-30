from __future__ import annotations

from pyproj import CRS
from shapely.geometry import LineString, Point, box

from app.core import planner
from app.core.models import (
    Mission,
    OperationalZone,
    PlannerSettings,
    Profile,
    TakeoffSite,
)


def test_small_mission_merge_warns_about_possible_battery_overrun(
    monkeypatch,
) -> None:
    settings = PlannerSettings(
        working_crs=CRS.from_epsg(32647),
        max_flight_time_min=1.0,
        battery_reserve_percent=0.0,
    )
    site = TakeoffSite(1, Point(-100, 50), 0.0, 0.0, 0.0)
    main_profile = Profile(1, 0.0, LineString([(0, 20), (100, 20)]))
    small_profile = Profile(2, 10.0, LineString([(100, 30), (110, 30)]))
    main = Mission(
        id=1,
        profiles=[main_profile],
        route_points=[],
        route_length_m=0.0,
        estimated_time_min=0.0,
        zone=box(0, 0, 100, 100),
        nominal_side_m=100.0,
    )
    small = Mission(
        id=2,
        profiles=[small_profile],
        route_points=[],
        route_length_m=0.0,
        estimated_time_min=0.0,
        zone=box(100, 0, 110, 10),
        nominal_side_m=100.0,
    )
    zone = OperationalZone(
        id=1,
        home=site,
        geometry=main.zone.union(small.zone),
        missions=[main, small],
        profiles=[main_profile, small_profile],
    )

    monkeypatch.setattr(
        planner,
        "build_terrain_route",
        lambda profiles, *args: ([Point(0, 0), Point(len(profiles), 0)], None),
    )
    monkeypatch.setattr(
        planner,
        "terrain_route_metrics",
        lambda *args: (500.0, 90.0, [0.0, 0.0]),
    )

    planner._merge_small_missions(
        zone,
        site,
        settings,
        terrain=object(),
        grid_azimuth=0.0,
        base_status="OK",
    )

    assert zone.missions == [main]
    assert main.zone.equals(zone.geometry)
    assert main.profiles == [main_profile, small_profile]
    assert main.status.startswith(planner.BATTERY_WARNING_STATUS)
    assert not main.status.startswith("Недопустимо")
