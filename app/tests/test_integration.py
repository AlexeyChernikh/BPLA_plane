from __future__ import annotations

from pathlib import Path

from pyproj import CRS

from app.core.models import PlannerSettings
from app.core.planner import plan_missions


def test_sample_geojson_can_be_planned() -> None:
    root = Path(__file__).resolve().parents[2]
    sample = root / "данные для разработки" / "P14.1.geojson"
    settings = PlannerSettings(
        working_crs=CRS.from_epsg(32647),
        azimuth_deg=0,
        profile_spacing_m=75,
        speed_mps=15,
        max_flight_time_min=30,
        battery_reserve_percent=20,
        waypoint_step_m=100,
    )
    result = plan_missions(sample, 53.4367, 96.5617, settings)
    assert result.profiles
    assert result.missions
    assert all(
        mission.route_length_m <= settings.max_route_length_m
        for mission in result.missions
    )
