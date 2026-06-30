from __future__ import annotations

import json
from pathlib import Path

from pyproj import CRS, Geod, Transformer
from shapely.geometry import MultiPolygon, Point, Polygon

from .models import Mission

MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_DO_CHANGE_SPEED = 178
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3
MAV_FRAME_GLOBAL = 0
MAV_FRAME_GLOBAL_TERRAIN_ALT = 10


def build_qgc_plan(
    mission: Mission,
    home_working: Point,
    working_crs: CRS | str | int,
    altitude_m: float,
    speed_mps: float,
    altitude_mode: str = "relative",
    home_elevation_m: float = 0.0,
    mission_mode: str = "waypoint",
    profile_spacing_m: float = 75.0,
    azimuth_deg: float = 0.0,
    climb_speed_mps: float = 3.0,
    descent_speed_mps: float = 2.0,
    terrain_adjust_tolerance_m: float = 10.0,
) -> dict[str, object]:
    if mission_mode == "survey":
        return build_qgc_survey_plan(
            mission,
            home_working,
            working_crs,
            altitude_m,
            speed_mps,
            altitude_mode,
            home_elevation_m,
            profile_spacing_m,
            azimuth_deg,
            climb_speed_mps,
            descent_speed_mps,
            terrain_adjust_tolerance_m,
        )
    if mission_mode != "waypoint":
        raise ValueError(f"Неизвестный режим миссии: {mission_mode}")
    transformer = Transformer.from_crs(
        CRS.from_user_input(working_crs), CRS.from_epsg(4326), always_xy=True
    )
    home_lon, home_lat = transformer.transform(home_working.x, home_working.y)
    items: list[dict[str, object]] = [
        {
            "autoContinue": True,
            "command": MAV_CMD_DO_CHANGE_SPEED,
            "doJumpId": 1,
            "frame": 2,
            "params": [1, speed_mps, -1, 0, None, None, None],
            "type": "SimpleItem",
        }
    ]
    route = [home_working, *mission.route_points, home_working]
    terrain_elevations = (
        [home_elevation_m, *mission.terrain_elevations_m, home_elevation_m]
        if mission.terrain_elevations_m
        else [home_elevation_m] * len(route)
    )
    frame, qgc_altitude_mode = _altitude_frame(altitude_mode)
    calc_above_terrain_altitudes = (
        _terrain_following_altitudes(
            route,
            terrain_elevations,
            altitude_m,
            speed_mps,
            climb_speed_mps,
            descent_speed_mps,
        )
        if altitude_mode == "calc_above_terrain"
        else []
    )
    for point_index, (point, terrain_elevation) in enumerate(
        zip(route, terrain_elevations)
    ):
        jump_id = point_index + 2
        longitude, latitude = transformer.transform(point.x, point.y)
        if altitude_mode == "calc_above_terrain":
            command_altitude = calc_above_terrain_altitudes[point_index]
            displayed_altitude = altitude_m
            amsl_alt_above_terrain: float | None = command_altitude
        else:
            command_altitude = _waypoint_altitude(
                altitude_mode,
                terrain_elevation,
                home_elevation_m,
                altitude_m,
            )
            displayed_altitude = command_altitude
            amsl_alt_above_terrain = None
        items.append(
            {
                "AMSLAltAboveTerrain": amsl_alt_above_terrain,
                "Altitude": displayed_altitude,
                "AltitudeMode": qgc_altitude_mode,
                "autoContinue": True,
                "command": MAV_CMD_NAV_WAYPOINT,
                "doJumpId": jump_id,
                "frame": frame,
                "params": [
                    0,
                    0,
                    0,
                    None,
                    latitude,
                    longitude,
                    command_altitude,
                ],
                "type": "SimpleItem",
            }
        )

    global_altitude_mode = {
        "amsl": 0,
        "relative": 1,
        "terrain": 2,
        "calc_above_terrain": 3,
    }[altitude_mode]
    return {
        "fileType": "Plan",
        "geoFence": {"circles": [], "polygons": [], "version": 2},
        "groundStation": "QGroundControl",
        "mission": {
            "cruiseSpeed": speed_mps,
            "firmwareType": 12,
            "globalPlanAltitudeMode": global_altitude_mode,
            "hoverSpeed": speed_mps,
            "items": items,
            "plannedHomePosition": [home_lat, home_lon, home_elevation_m],
            "vehicleType": 2,
            "version": 2,
        },
        "rallyPoints": {"points": [], "version": 2},
        "version": 1,
    }


def export_qgc_plan(
    path: str | Path,
    mission: Mission,
    home_working: Point,
    working_crs: CRS | str | int,
    altitude_m: float,
    speed_mps: float,
    altitude_mode: str = "relative",
    home_elevation_m: float = 0.0,
    mission_mode: str = "waypoint",
    profile_spacing_m: float = 75.0,
    azimuth_deg: float = 0.0,
    climb_speed_mps: float = 3.0,
    descent_speed_mps: float = 2.0,
    terrain_adjust_tolerance_m: float = 10.0,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = build_qgc_plan(
        mission,
        home_working,
        working_crs,
        altitude_m,
        speed_mps,
        altitude_mode,
        home_elevation_m,
        mission_mode,
        profile_spacing_m,
        azimuth_deg,
        climb_speed_mps,
        descent_speed_mps,
        terrain_adjust_tolerance_m,
    )
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output


def build_qgc_survey_plan(
    mission: Mission,
    home_working: Point,
    working_crs: CRS | str | int,
    altitude_m: float,
    speed_mps: float,
    altitude_mode: str,
    home_elevation_m: float,
    profile_spacing_m: float,
    azimuth_deg: float,
    climb_speed_mps: float,
    descent_speed_mps: float,
    terrain_adjust_tolerance_m: float,
) -> dict[str, object]:
    transformer = Transformer.from_crs(
        CRS.from_user_input(working_crs), CRS.from_epsg(4326), always_xy=True
    )
    home_lon, home_lat = transformer.transform(home_working.x, home_working.y)
    frame, qgc_altitude_mode = _altitude_frame(altitude_mode)
    route_elevations = (
        mission.terrain_elevations_m
        if mission.terrain_elevations_m
        else [home_elevation_m] * len(mission.route_points)
    )
    calc_above_terrain_altitudes = (
        _terrain_following_altitudes(
            mission.route_points,
            route_elevations,
            altitude_m,
            speed_mps,
            climb_speed_mps,
            descent_speed_mps,
        )
        if altitude_mode == "calc_above_terrain"
        else []
    )
    inner_items: list[dict[str, object]] = []
    visual_points: list[list[float]] = []
    for point_index, (point, terrain_elevation) in enumerate(
        zip(mission.route_points, route_elevations)
    ):
        jump_id = point_index + 3
        longitude, latitude = transformer.transform(point.x, point.y)
        waypoint_altitude = _waypoint_altitude(
            altitude_mode,
            terrain_elevation,
            home_elevation_m,
            altitude_m,
        )
        item: dict[str, object] = {
            "autoContinue": True,
            "command": MAV_CMD_NAV_WAYPOINT,
            "doJumpId": jump_id,
            "frame": (
                0 if altitude_mode == "calc_above_terrain" else frame
            ),
            "params": [
                0,
                0,
                0,
                None,
                latitude,
                longitude,
                calc_above_terrain_altitudes[point_index]
                if altitude_mode == "calc_above_terrain"
                else waypoint_altitude,
            ],
            "type": "SimpleItem",
        }
        if altitude_mode != "calc_above_terrain":
            item.update(
                {
                    "AMSLAltAboveTerrain": None,
                    "Altitude": waypoint_altitude,
                    "AltitudeMode": qgc_altitude_mode,
                }
            )
        inner_items.append(item)
        visual_points.append([latitude, longitude])

    polygon_points = _survey_polygon(mission, transformer)
    distance_mode = {
        "amsl": 0,
        "relative": 1,
        "terrain": 2,
        "calc_above_terrain": 3,
    }[altitude_mode]
    complex_item = {
        "TransectStyleComplexItem": {
            "CameraCalc": {
                "AdjustedFootprintFrontal": 0,
                "AdjustedFootprintSide": profile_spacing_m,
                "CameraName": "Manual (no camera specs)",
                "DistanceMode": distance_mode,
                "DistanceToSurface": altitude_m,
                "version": 2,
            },
            "CameraShots": 0,
            "CameraTriggerInTurnAround": True,
            "HoverAndCapture": False,
            "Items": inner_items,
            "Refly90Degrees": False,
            "TerrainAdjustMaxClimbRate": climb_speed_mps,
            "TerrainAdjustMaxDescentRate": descent_speed_mps,
            "TerrainAdjustTolerance": terrain_adjust_tolerance_m,
            "TerrainFlightSpeed": speed_mps,
            "TurnAroundDistance": 10.0,
            "VisualTransectPoints": visual_points,
            "version": 2,
        },
        "angle": _survey_true_angle(
            mission, transformer, azimuth_deg
        ),
        "complexItemType": "survey",
        "entryLocation": 0,
        "flyAlternateTransects": False,
        "polygon": polygon_points,
        "splitConcavePolygons": False,
        "type": "ComplexItem",
        "version": 5,
    }
    takeoff_altitude = (
        altitude_m
        if altitude_mode != "amsl"
        else home_elevation_m + altitude_m
    )
    items: list[dict[str, object]] = [
        {
            "autoContinue": True,
            "command": 530,
            "doJumpId": 1,
            "frame": 2,
            "params": [0, 2, None, None, None, None, None],
            "type": "SimpleItem",
        },
        {
            "AMSLAltAboveTerrain": None,
            "Altitude": takeoff_altitude,
            "AltitudeMode": 1 if altitude_mode != "amsl" else 0,
            "autoContinue": True,
            "command": 22,
            "doJumpId": 2,
            "frame": 3 if altitude_mode != "amsl" else 0,
            "params": [
                0,
                0,
                0,
                None,
                home_lat,
                home_lon,
                takeoff_altitude,
            ],
            "type": "SimpleItem",
        },
        complex_item,
        {
            "autoContinue": True,
            "command": 20,
            "doJumpId": len(inner_items) + 3,
            "frame": 2,
            "params": [0, 0, 0, 0, 0, 0, 0],
            "type": "SimpleItem",
        },
    ]
    global_altitude_mode = {
        "amsl": 0,
        "relative": 1,
        "terrain": 2,
        "calc_above_terrain": 3,
    }[altitude_mode]
    return {
        "fileType": "Plan",
        "geoFence": {"circles": [], "polygons": [], "version": 2},
        "groundStation": "QGroundControl",
        "mission": {
            "cruiseSpeed": speed_mps,
            "firmwareType": 12,
            "globalPlanAltitudeMode": global_altitude_mode,
            "hoverSpeed": speed_mps,
            "items": items,
            "plannedHomePosition": [home_lat, home_lon, home_elevation_m],
            "vehicleType": 2,
            "version": 2,
        },
        "rallyPoints": {"points": [], "version": 2},
        "version": 1,
    }


def _survey_polygon(
    mission: Mission, transformer: Transformer
) -> list[list[float]]:
    geometry = mission.zone
    if geometry is None or geometry.is_empty:
        from shapely.geometry import LineString

        geometry = LineString(
            [(point.x, point.y) for point in mission.route_points]
        ).buffer(1).convex_hull
    if isinstance(geometry, MultiPolygon):
        geometry = max(geometry.geoms, key=lambda polygon: polygon.area)
    if not isinstance(geometry, Polygon) or geometry.is_empty:
        geometry = Polygon(
            [(point.x, point.y) for point in mission.route_points]
        ).buffer(1).convex_hull
    coordinates: list[list[float]] = []
    for x, y in list(geometry.exterior.coords)[:-1]:
        longitude, latitude = transformer.transform(x, y)
        coordinates.append([latitude, longitude])
    return coordinates


def _survey_true_angle(
    mission: Mission,
    transformer: Transformer,
    fallback_azimuth_deg: float,
) -> float:
    if not mission.profiles:
        return fallback_azimuth_deg % 180.0
    profile = max(mission.profiles, key=lambda item: item.geometry.length)
    start = profile.geometry.coords[0]
    end = profile.geometry.coords[-1]
    start_lon, start_lat = transformer.transform(*start)
    end_lon, end_lat = transformer.transform(*end)
    forward_azimuth, _, _ = Geod(ellps="WGS84").inv(
        start_lon, start_lat, end_lon, end_lat
    )
    return forward_azimuth % 180.0


def _terrain_following_altitudes(
    points: list[Point],
    terrain_elevations_m: list[float],
    agl_m: float,
    speed_mps: float,
    climb_speed_mps: float,
    descent_speed_mps: float,
) -> list[float]:
    """Create safe AMSL heights while respecting vertical-rate constraints."""
    if len(points) != len(terrain_elevations_m):
        raise ValueError(
            "Число отметок DEM не совпадает с числом waypoint Survey."
        )
    altitudes = [elevation + agl_m for elevation in terrain_elevations_m]
    if len(altitudes) < 2:
        return altitudes

    # Propagate high terrain backwards so the vehicle starts climbing early.
    for index in range(len(altitudes) - 2, -1, -1):
        horizontal_time = points[index].distance(points[index + 1]) / speed_mps
        required = altitudes[index + 1] - climb_speed_mps * horizontal_time
        altitudes[index] = max(altitudes[index], required)

    # Keep the descent within the configured rate by extending it forward.
    for index in range(1, len(altitudes)):
        horizontal_time = points[index - 1].distance(points[index]) / speed_mps
        required = altitudes[index - 1] - descent_speed_mps * horizontal_time
        altitudes[index] = max(altitudes[index], required)
    return altitudes


def _altitude_frame(mode: str) -> tuple[int, int]:
    if mode == "amsl":
        return MAV_FRAME_GLOBAL, 0
    if mode == "relative":
        return MAV_FRAME_GLOBAL_RELATIVE_ALT, 1
    if mode == "terrain":
        return MAV_FRAME_GLOBAL_TERRAIN_ALT, 2
    if mode == "calc_above_terrain":
        return MAV_FRAME_GLOBAL, 3
    raise ValueError(f"Неизвестный режим высоты: {mode}")


def _waypoint_altitude(
    mode: str,
    terrain_elevation_m: float,
    home_elevation_m: float,
    agl_m: float,
) -> float:
    if mode == "amsl":
        return terrain_elevation_m + agl_m
    if mode == "relative":
        return terrain_elevation_m + agl_m - home_elevation_m
    if mode == "terrain":
        return agl_m
    if mode == "calc_above_terrain":
        return terrain_elevation_m + agl_m
    raise ValueError(f"Неизвестный режим высоты: {mode}")
