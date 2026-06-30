from app.ui.map_view import _HTML_TEMPLATE


def test_map_contains_hierarchical_zone_and_mission_legend() -> None:
    assert "legend-tree" in _HTML_TEMPLATE
    assert "zoneEntries" in _HTML_TEMPLATE
    assert "missionEntry" in _HTML_TEMPLATE
    assert "Крупные зоны" in _HTML_TEMPLATE
    assert "Миссия " in _HTML_TEMPLATE
    assert "globalState.profiles" in _HTML_TEMPLATE
    assert "globalState.routes" in _HTML_TEMPLATE
