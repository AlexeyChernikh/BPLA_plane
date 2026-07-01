from __future__ import annotations

from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, box

from app.core.exporter import export_mission
from app.core.models import (
    LoadedPolygon,
    Mission,
    OperationalZone,
    PlannerSettings,
    PlanningResult,
    Profile,
    TakeoffSite,
)


def _result_with_two_missions(tmp_path: Path) -> PlanningResult:
    crs = CRS.from_epsg(32647)
    home = TakeoffSite(1, Point(500000, 5900000), 1000.0, 0.0, 0.0)
    missions = []
    profiles = []
    for identifier, x_offset in ((1, 100.0), (2, 300.0)):
        route = [
            Point(home.point.x + x_offset, home.point.y),
            Point(home.point.x + x_offset + 100, home.point.y),
        ]
        profile = Profile(
            identifier,
            x_offset,
            LineString(route),
        )
        profiles.append(profile)
        missions.append(
            Mission(
                id=identifier,
                profiles=[profile],
                route_points=route,
                route_length_m=400.0,
                estimated_time_min=2.0,
                status=(
                    "OK"
                    if identifier == 1
                    else "Недопустимо: превышен лимит батареи"
                ),
                zone=box(
                    home.point.x + x_offset - 10,
                    home.point.y - 20,
                    home.point.x + x_offset + 110,
                    home.point.y + 20,
                ),
                zone_id=1,
                home_id=1,
                terrain_elevations_m=[1000.0, 1002.0],
            )
        )
    zone_geometry = missions[0].zone.union(missions[1].zone)
    zone = OperationalZone(
        id=1,
        home=home,
        geometry=zone_geometry,
        profiles=profiles,
        missions=missions,
        status="Недопустимо: одна миссия превышает лимит",
    )
    return PlanningResult(
        polygon=LoadedPolygon(
            tmp_path / "polygon.geojson",
            crs,
            crs,
            zone_geometry,
        ),
        home_working=home.point,
        settings=PlannerSettings(working_crs=crs),
        profiles=profiles,
        missions=missions,
        takeoff_sites=[home],
        zones=[zone],
        valid=False,
        errors=["Зона 1, миссия 2: превышен лимит"],
    )


def test_single_valid_mission_exports_only_its_five_files(
    tmp_path: Path,
) -> None:
    result = _result_with_two_missions(tmp_path)

    created = export_mission(result, 1, 1, tmp_path / "output")

    mission_directory = tmp_path / "output" / "zone_001_mission_001"
    assert len(created) == 5
    assert all(path.parent == mission_directory for path in created)
    assert {path.suffix for path in created} == {".plan", ".kml", ".csv"}
    assert len(list(mission_directory.glob("*.kml"))) == 3
    assert not list((tmp_path / "output").rglob("*mission_002*"))
    assert not (tmp_path / "output" / "summary").exists()


def test_single_invalid_mission_is_not_exported(tmp_path: Path) -> None:
    result = _result_with_two_missions(tmp_path)

    with pytest.raises(ValueError, match="Экспорт миссии 1.2 запрещён"):
        export_mission(result, 1, 2, tmp_path / "output")

    assert not (tmp_path / "output").exists()
