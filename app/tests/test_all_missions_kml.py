from __future__ import annotations

from xml.etree import ElementTree as ET

from pyproj import CRS
from shapely.geometry import MultiPolygon, Point, box

from app.core.kml_exporter import KML_NS, export_all_mission_polygons_kml
from app.core.mission_colors import kml_color, mission_color
from app.core.models import Mission, OperationalZone, TakeoffSite


def test_all_mission_polygons_kml_uses_application_colors(tmp_path) -> None:
    missions = [
        Mission(1, [], [], 0, 0, zone=box(0, 0, 10, 10), zone_id=2),
        Mission(
            2,
            [],
            [],
            0,
            0,
            zone=MultiPolygon([box(20, 0, 30, 10), box(40, 0, 50, 10)]),
            zone_id=2,
        ),
    ]
    zone = OperationalZone(
        2,
        TakeoffSite(1, Point(0, 0), 0, 0, 0),
        box(0, 0, 50, 10),
        missions=missions,
    )

    path = export_all_mission_polygons_kml(
        tmp_path / "missions.kml", [zone], CRS.from_epsg(3857)
    )

    root = ET.parse(path).getroot()
    namespace = {"k": KML_NS}
    names = [
        item.text for item in root.findall(".//k:Placemark/k:name", namespace)
    ]
    colors = [
        item.text
        for item in root.findall(
            ".//k:Placemark/k:Style/k:LineStyle/k:color", namespace
        )
    ]
    assert names == [
        "Зона 002 / Миссия 001",
        "Зона 002 / Миссия 002 / Часть 1",
        "Зона 002 / Миссия 002 / Часть 2",
    ]
    assert colors == [
        kml_color(mission_color(2, 1)),
        kml_color(mission_color(2, 2)),
        kml_color(mission_color(2, 2)),
    ]
    assert len(root.findall(".//k:Polygon", namespace)) == 3
