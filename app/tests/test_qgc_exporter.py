from __future__ import annotations

import pytest
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point

from app.core.models import Mission, Profile
from app.core.qgc_exporter import (
    MAV_CMD_DO_CHANGE_SPEED,
    MAV_CMD_NAV_WAYPOINT,
    _terrain_following_altitudes,
    build_qgc_plan,
)


def test_qgc_plan_contains_speed_home_and_waypoints() -> None:
    crs = CRS.from_epsg(32647)
    to_working = Transformer.from_crs(4326, crs, always_xy=True)
    home_x, home_y = to_working.transform(96.56, 53.43)
    home = Point(home_x, home_y)
    route_point = Point(home_x + 100, home_y)
    mission = Mission(
        1,
        [Profile(1, 0, LineString([home, route_point]))],
        [route_point],
        200,
        1,
    )
    plan = build_qgc_plan(mission, home, crs, 110, 5)
    assert plan["fileType"] == "Plan"
    assert plan["version"] == 1
    mission_json = plan["mission"]
    assert mission_json["version"] == 2
    assert mission_json["firmwareType"] == 12
    items = mission_json["items"]
    assert items[0]["command"] == MAV_CMD_DO_CHANGE_SPEED
    assert all(item["command"] == MAV_CMD_NAV_WAYPOINT for item in items[1:])
    assert [item["doJumpId"] for item in items] == list(range(1, len(items) + 1))
    assert items[1]["params"][4:6] == items[-1]["params"][4:6]
    assert all(item["autoContinue"] is True for item in items)


def test_qgc_terrain_elevations_support_all_altitude_modes() -> None:
    crs = CRS.from_epsg(32647)
    home = Point(500000, 5900000)
    mission = Mission(
        1,
        [Profile(1, 0, LineString([(500000, 5900000), (500100, 5900000)]))],
        [Point(500100, 5900000)],
        200,
        1,
        terrain_elevations_m=[1250],
    )
    expected = {
        "amsl": (0, 0, 1360),
        "relative": (3, 1, 160),
        "terrain": (10, 2, 110),
        "calc_above_terrain": (0, 3, 1360),
    }
    for mode, (frame, altitude_mode, altitude) in expected.items():
        plan = build_qgc_plan(
            mission,
            home,
            crs,
            110,
            5,
            altitude_mode=mode,
            home_elevation_m=1200,
        )
        route_item = plan["mission"]["items"][2]
        assert route_item["frame"] == frame
        assert route_item["AltitudeMode"] == altitude_mode
        assert route_item["params"][6] == pytest.approx(altitude)
        assert plan["mission"]["globalPlanAltitudeMode"] == altitude_mode


def test_waypoint_calc_above_terrain_exports_agl_and_safe_amsl() -> None:
    crs = CRS.from_epsg(32647)
    home = Point(500000, 5900000)
    route = [Point(500100, 5900000), Point(500200, 5900000)]
    mission = Mission(
        1,
        [Profile(1, 0, LineString(route))],
        route,
        400,
        2,
        terrain_elevations_m=[1200, 900],
    )
    plan = build_qgc_plan(
        mission,
        home,
        crs,
        110,
        5,
        altitude_mode="calc_above_terrain",
        home_elevation_m=1000,
        climb_speed_mps=3,
        descent_speed_mps=2,
    )
    waypoints = plan["mission"]["items"][1:]

    assert plan["mission"]["globalPlanAltitudeMode"] == 3
    assert all(item["frame"] == 0 for item in waypoints)
    assert all(item["AltitudeMode"] == 3 for item in waypoints)
    assert [item["params"][6] for item in waypoints] == pytest.approx(
        [1250, 1310, 1270, 1190]
    )
    assert [item["Altitude"] for item in waypoints] == pytest.approx(
        [110, 110, 110, 110]
    )
    assert [item["AMSLAltAboveTerrain"] for item in waypoints] == pytest.approx(
        [1250, 1310, 1270, 1190]
    )


def test_survey_plan_matches_qgc_complex_item_structure() -> None:
    crs = CRS.from_epsg(32647)
    home = Point(500000, 5900000)
    route = [Point(500000, 5900100), Point(500100, 5900100)]
    mission = Mission(
        1,
        [Profile(1, 0, LineString(route))],
        route,
        400,
        2,
        zone=LineString(route).buffer(40, cap_style="flat"),
        terrain_elevations_m=[1200, 1210],
    )
    plan = build_qgc_plan(
        mission,
        home,
        crs,
        110,
        5,
        altitude_mode="calc_above_terrain",
        home_elevation_m=1190,
        mission_mode="survey",
        profile_spacing_m=75,
        azimuth_deg=0,
    )
    items = plan["mission"]["items"]
    assert [item.get("command") for item in items] == [530, 22, None, 20]
    survey = items[2]
    assert survey["type"] == "ComplexItem"
    assert survey["complexItemType"] == "survey"
    assert survey["version"] == 5
    assert survey["angle"] == pytest.approx(90, abs=0.01)
    transect = survey["TransectStyleComplexItem"]
    assert transect["CameraCalc"]["AdjustedFootprintSide"] == 75
    assert transect["CameraCalc"]["DistanceMode"] == 3
    assert transect["CameraCalc"]["DistanceToSurface"] == 110
    assert transect["TerrainAdjustTolerance"] == 10
    assert len(transect["Items"]) == len(route)
    assert all(item["command"] == 16 for item in transect["Items"])
    assert all(item["frame"] == 0 for item in transect["Items"])
    assert [item["params"][6] for item in transect["Items"]] == pytest.approx(
        [1310, 1320]
    )
    assert plan["mission"]["globalPlanAltitudeMode"] == 3


def test_calc_above_terrain_respects_climb_and_descent_rates() -> None:
    points = [Point(0, 0), Point(100, 0), Point(200, 0)]
    altitudes = _terrain_following_altitudes(
        points,
        [1000, 1200, 900],
        agl_m=110,
        speed_mps=5,
        climb_speed_mps=3,
        descent_speed_mps=2,
    )
    assert altitudes == pytest.approx([1250, 1310, 1270])
    assert all(
        altitude >= terrain + 110
        for altitude, terrain in zip(altitudes, [1000, 1200, 900])
    )
