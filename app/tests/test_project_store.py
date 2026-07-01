from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, box

from app.core.models import (
    LoadedPolygon,
    Mission,
    OperationalZone,
    PlannerSettings,
    PlanningResult,
    Profile,
    TakeoffSite,
)
from app.core.exporter import export_all
from app.core.polygon_loader import load_polygon
from app.core.project_store import (
    LEGACY_PROJECT_VERSION,
    PROJECT_VERSION,
    load_project,
    save_project,
)


def test_project_round_trip_contains_complete_recalculation_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    crs = CRS.from_epsg(32647)
    geometry = box(500000, 5800000, 501000, 5801000)
    settings = PlannerSettings(
        working_crs=crs,
        azimuth_deg=37.0,
        profile_spacing_m=42.0,
        altitude_m=125.0,
    )
    home = TakeoffSite(1, Point(499900, 5800500), 800.0, 1.0, 0.2)
    profile = Profile(
        1,
        12.5,
        LineString([(500000, 5800100), (501000, 5800100)]),
    )
    mission = Mission(
        id=1,
        profiles=[profile],
        route_points=[Point(500000, 5800100), Point(501000, 5800100)],
        route_length_m=1234.5,
        estimated_time_min=6.7,
        status="Возможен выход за пределы лимита батареи",
        zone=geometry,
        zone_id=1,
        home_id=1,
        terrain_elevations_m=[810.0, 812.0],
        grid_row=2,
        grid_col=3,
        nominal_side_m=250.0,
        edge_clipped=True,
    )
    zone = OperationalZone(
        id=1,
        home=home,
        geometry=geometry,
        profiles=[profile],
        missions=[mission],
        relief_m=12.0,
        status="Предупреждение",
        nominal_side_m=750.0,
    )
    dem = tmp_path / "source.tif"
    dem.write_bytes(b"test-dem-content")
    result = PlanningResult(
        polygon=LoadedPolygon(tmp_path / "source.geojson", crs, crs, geometry),
        home_working=home.point,
        settings=settings,
        profiles=[profile],
        missions=[mission],
        takeoff_sites=[home],
        zones=[zone],
        dem_path=dem,
        valid=False,
        errors=["Проверочная ошибка"],
    )

    project = save_project(tmp_path / "survey", result, dem)
    session = load_project(project, tmp_path / "opened")

    assert project.suffix == ".bpla"
    assert session.dem_path.read_bytes() == b"test-dem-content"
    assert session.settings.azimuth_deg == 37.0
    assert session.settings.profile_spacing_m == 42.0
    assert session.settings.working_crs == crs
    assert session.forced_grid_sizes == (750.0, 250.0)
    assert session.homes_wgs84 is not None
    assert len(session.homes_wgs84) == 1
    restored = load_polygon(session.polygon_path, crs)
    assert restored.geometry.symmetric_difference(geometry).area == pytest.approx(
        0.0, abs=0.01
    )
    snapshot = session.result
    assert snapshot is not None
    assert snapshot.dem_path == session.dem_path
    assert snapshot.valid is False
    assert snapshot.errors == ["Проверочная ошибка"]
    assert snapshot.missions[0].route_length_m == 1234.5
    assert snapshot.missions[0].terrain_elevations_m == [810.0, 812.0]
    assert snapshot.missions[0].status == mission.status
    assert snapshot.profiles[0] is snapshot.missions[0].profiles[0]
    assert snapshot.profiles[0] is snapshot.zones[0].profiles[0]
    assert snapshot.missions[0] is snapshot.zones[0].missions[0]
    assert snapshot.takeoff_sites[0] is snapshot.zones[0].home
    snapshot.valid = True
    snapshot.errors = []
    created = export_all(snapshot, tmp_path / "exported")
    assert created

    from app.ui import main_window

    class FakeTerrain:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def preview_overlay(self):
            return None

    monkeypatch.setattr(main_window, "TerrainModel", FakeTerrain)
    monkeypatch.setattr(
        main_window,
        "plan_terrain_missions",
        lambda *args, **kwargs: pytest.fail(
            "Проект v2 не должен пересчитывать миссии"
        ),
    )
    worker = main_window.ProjectLoadWorker(project)
    completed = []
    failures = []
    worker.signals.finished.connect(completed.append)
    worker.signals.failed.connect(failures.append)

    worker.run()

    assert not failures
    assert completed
    assert completed[0][1].missions[0].route_length_m == 1234.5


def test_legacy_project_loads_without_result_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "legacy.bpla"
    manifest = {
        "format": "BPLA terrain mission project",
        "version": LEGACY_PROJECT_VERSION,
        "dem_file": "terrain.tif",
        "settings": {
            "working_crs": "EPSG:32647",
            "azimuth_deg": 0.0,
            "profile_spacing_m": 75.0,
            "altitude_m": 110.0,
            "speed_mps": 5.0,
            "max_flight_time_min": 30.0,
            "battery_reserve_percent": 20.0,
            "waypoint_step_m": 100.0,
            "profile_extension_m": 0.0,
            "route_mode": "snake",
            "home_search_buffer_m": 500.0,
            "climb_speed_mps": 3.0,
            "descent_speed_mps": 2.0,
            "terrain_adjust_tolerance_m": 10.0,
            "terrain_warning_m": 300.0,
            "altitude_mode": "calc_above_terrain",
            "mission_mode": "survey",
            "max_zone_side_m": 0.0,
            "max_mission_side_m": 0.0,
        },
        "homes_wgs84": None,
        "forced_grid_sizes": None,
    }
    polygon = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                },
            }
        ],
    }
    with ZipFile(project, "w") as archive:
        archive.writestr(
            "project.json",
            json.dumps(manifest),
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "polygon.geojson",
            json.dumps(polygon),
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr("terrain.tif", b"legacy-dem", compress_type=ZIP_STORED)

    session = load_project(project, tmp_path / "legacy-opened")

    assert session.result is None

    from app.ui import main_window

    recalculated = SimpleNamespace(
        polygon=SimpleNamespace(geometry=box(0, 0, 1, 1))
    )
    calls = []

    class FakeTerrain:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def preview_overlay(self):
            return None

    monkeypatch.setattr(main_window, "load_project", lambda path: session)
    monkeypatch.setattr(main_window, "TerrainModel", FakeTerrain)
    monkeypatch.setattr(
        main_window,
        "plan_terrain_missions",
        lambda *args: calls.append(args) or recalculated,
    )
    worker = main_window.ProjectLoadWorker(project)
    completed = []
    worker.signals.finished.connect(completed.append)

    worker.run()

    assert len(calls) == 1
    assert completed[0][1] is recalculated


def test_version_two_project_requires_valid_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "broken.bpla"
    manifest = {
        "version": PROJECT_VERSION,
        "dem_file": "terrain.tif",
        "result_file": "result.json",
        "settings": {"working_crs": "EPSG:32647"},
    }
    with ZipFile(project, "w") as archive:
        archive.writestr("project.json", json.dumps(manifest))
        archive.writestr("polygon.geojson", "{}")
        archive.writestr("terrain.tif", b"dem")
        archive.writestr("result.json", "{}")

    with pytest.raises(ValueError, match="Снимок расчёта"):
        load_project(project, tmp_path / "broken-opened")
