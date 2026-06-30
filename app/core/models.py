from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pyproj import CRS
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

RouteMode = Literal["snake", "one-way"]
AltitudeMode = Literal["amsl", "relative", "terrain", "calc_above_terrain"]
MissionMode = Literal["survey", "waypoint"]


@dataclass(frozen=True)
class PlannerSettings:
    working_crs: CRS
    azimuth_deg: float = 0.0
    profile_spacing_m: float = 75.0
    altitude_m: float = 110.0
    speed_mps: float = 5.0
    max_flight_time_min: float = 30.0
    battery_reserve_percent: float = 20.0
    waypoint_step_m: float = 100.0
    profile_extension_m: float = 0.0
    route_mode: RouteMode = "snake"
    home_search_buffer_m: float = 500.0
    climb_speed_mps: float = 3.0
    descent_speed_mps: float = 2.0
    terrain_adjust_tolerance_m: float = 10.0
    terrain_warning_m: float = 300.0
    altitude_mode: AltitudeMode = "calc_above_terrain"
    mission_mode: MissionMode = "survey"
    max_zone_side_m: float = 0.0
    max_mission_side_m: float = 0.0

    @property
    def usable_time_sec(self) -> float:
        return self.max_flight_time_min * 60.0 * (
            1.0 - self.battery_reserve_percent / 100.0
        )

    @property
    def max_route_length_m(self) -> float:
        return self.speed_mps * self.usable_time_sec


@dataclass(frozen=True)
class LoadedPolygon:
    source_path: Path
    source_crs: CRS
    working_crs: CRS
    geometry: Polygon | MultiPolygon


@dataclass(frozen=True)
class Profile:
    id: int
    offset_m: float
    geometry: LineString


@dataclass
class Mission:
    id: int
    profiles: list[Profile]
    route_points: list[Point]
    route_length_m: float
    estimated_time_min: float
    status: str = "OK"
    zone: Polygon | MultiPolygon | None = None
    zone_id: int = 1
    home_id: int = 1
    terrain_elevations_m: list[float] = field(default_factory=list)
    grid_row: int = 0
    grid_col: int = 0
    nominal_side_m: float = 0.0
    edge_clipped: bool = False


@dataclass(frozen=True)
class TerrainSample:
    elevation_m: float
    slope_deg: float
    roughness_m: float


@dataclass(frozen=True)
class TakeoffSite:
    id: int
    point: Point
    elevation_m: float
    slope_deg: float
    roughness_m: float


@dataclass
class OperationalZone:
    id: int
    home: TakeoffSite
    geometry: Polygon | MultiPolygon
    profiles: list[Profile] = field(default_factory=list)
    missions: list[Mission] = field(default_factory=list)
    relief_m: float = 0.0
    status: str = "OK"
    grid_row: int = 0
    grid_col: int = 0
    nominal_side_m: float = 0.0
    edge_clipped: bool = False


@dataclass
class PlanningResult:
    polygon: LoadedPolygon
    home_working: Point
    settings: PlannerSettings
    profiles: list[Profile] = field(default_factory=list)
    missions: list[Mission] = field(default_factory=list)
    takeoff_sites: list[TakeoffSite] = field(default_factory=list)
    zones: list[OperationalZone] = field(default_factory=list)
    dem_path: Path | None = None
    valid: bool = True
    errors: list[str] = field(default_factory=list)
