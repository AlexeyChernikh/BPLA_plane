from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import geopandas as gpd
from pyproj import CRS
from shapely.ops import unary_union

from app.core.models import PlannerSettings
from app.core.planner import SMALL_MISSION_AREA_RATIO, plan_terrain_missions
from app.core.exporter import export_all


@pytest.mark.integration
def test_large_kml_is_fully_covered_with_dem(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    polygon = next((root / "данные для разработки").glob("*.kml"))
    dem = root / "данные для разработки" / "DEM_01.tif"
    settings = PlannerSettings(
        working_crs=CRS.from_epsg(28417),
        profile_spacing_m=1000,
        altitude_m=110,
        speed_mps=15,
        max_flight_time_min=45,
        battery_reserve_percent=20,
        waypoint_step_m=250,
    )
    result = plan_terrain_missions(polygon, dem, settings)
    assert result.valid
    assert result.takeoff_sites
    assert result.missions
    assert all(
        zone.nominal_side_m % result.missions[0].nominal_side_m
        == pytest.approx(0)
        for zone in result.zones
    )
    assert all(
        mission.edge_clipped
        or mission.zone.area == pytest.approx(mission.nominal_side_m**2)
        for mission in result.missions
    )
    assert unary_union([zone.geometry for zone in result.zones]).symmetric_difference(
        result.polygon.geometry
    ).area < 0.01
    assert all(
        mission.estimated_time_min <= settings.usable_time_sec / 60 + 1e-7
        for mission in result.missions
    )
    for zone in result.zones:
        assert not zone.geometry.covers(zone.home.point)
        assert all(
            mission.zone is None or not mission.zone.covers(zone.home.point)
            for mission in zone.missions
        )
    for zone in result.zones:
        mission_polygons = [
            mission.zone for mission in zone.missions if mission.zone is not None
        ]
        if len(mission_polygons) > 1:
            assert all(
                mission.zone.area
                >= mission.nominal_side_m**2 * SMALL_MISSION_AREA_RATIO
                for mission in zone.missions
                if mission.zone is not None
            )
        partition = unary_union(mission_polygons)
        assert partition.symmetric_difference(zone.geometry).area < 0.01
        assert (
            sum(geometry.area for geometry in mission_polygons) - partition.area
            < 0.01
        )
    export_all(result, tmp_path)
    mission_directory = tmp_path / "zones" / "zone_001" / "mission_001"
    assert (tmp_path / "zones" / "zone_001" / "zone_polygon.kml").exists()
    assert (tmp_path / "zones" / "zone_001" / "home.kml").exists()
    assert list(mission_directory.glob("*.plan"))
    assert list(mission_directory.glob("*_summary.csv"))
    kml_files = list(mission_directory.glob("*.kml"))
    assert {path.name.rsplit("_", 1)[-1] for path in kml_files} == {
        "grid.kml",
        "polygon.kml",
        "route.kml",
    }
    for path in kml_files:
        ET.parse(path)
    assert (tmp_path / "summary" / "all_profiles.gpkg").exists()
    zone_frame = gpd.read_file(
        tmp_path / "summary" / "operational_zones.gpkg"
    )
    mission_frame = gpd.read_file(
        tmp_path / "summary" / "mission_zones.gpkg"
    )
    assert {"grid_row", "grid_col", "side_m", "edge", "area_m2"} <= set(
        zone_frame.columns
    )
    assert {"grid_row", "grid_col", "side_m", "edge", "area_m2"} <= set(
        mission_frame.columns
    )
