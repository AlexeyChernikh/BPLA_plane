from __future__ import annotations

MISSION_COLORS = (
    "#e41a1c",
    "#377eb8",
    "#4daf4a",
    "#984ea3",
    "#ff7f00",
    "#a65628",
    "#f781bf",
    "#999999",
    "#66c2a5",
    "#e6ab02",
)


def mission_color(zone_id: int, mission_id: int) -> str:
    """Return the color used by the application for a battery mission."""
    identifier = zone_id * 37 + mission_id
    return MISSION_COLORS[(identifier - 1) % len(MISSION_COLORS)]


def kml_color(rgb: str, alpha: str = "ff") -> str:
    """Convert #RRGGBB to KML's AABBGGRR representation."""
    value = rgb.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"Некорректный цвет RGB: {rgb}")
    return f"{alpha}{value[4:6]}{value[2:4]}{value[0:2]}".lower()
