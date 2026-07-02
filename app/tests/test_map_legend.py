from app.ui.map_view import MapView, _HTML_TEMPLATE


def test_map_contains_hierarchical_zone_and_mission_legend() -> None:
    assert "legend-tree" in _HTML_TEMPLATE
    assert "zoneEntries" in _HTML_TEMPLATE
    assert "missionEntry" in _HTML_TEMPLATE
    assert "Крупные зоны" in _HTML_TEMPLATE
    assert "Миссия " in _HTML_TEMPLATE
    assert "globalState.profiles" in _HTML_TEMPLATE
    assert "globalState.routes" in _HTML_TEMPLATE


def test_map_requests_context_menu_for_every_mission_layer() -> None:
    assert hasattr(MapView, "missionContextRequested")
    assert "layer.on('contextmenu'" in _HTML_TEMPLATE
    assert "['zone','profile','route'].includes(p.kind)" in _HTML_TEMPLATE
    assert "missioncontext:" in _HTML_TEMPLATE
    assert "p.zone_id" in _HTML_TEMPLATE
    assert "p.mission_id" in _HTML_TEMPLATE


def test_map_has_clean_full_export_mode_with_mission_labels() -> None:
    assert "prepareMissionExport" in _HTML_TEMPLATE
    assert "restoreMissionExport" in _HTML_TEMPLATE
    assert "mission-export-label" in _HTML_TEMPLATE
    assert "label_lat" in _HTML_TEMPLATE
    assert "Зона " in _HTML_TEMPLATE
    assert "Миссия " in _HTML_TEMPLATE
