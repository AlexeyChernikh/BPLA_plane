from __future__ import annotations

import csv
import json
from pathlib import Path

import geopandas as gpd
from pyproj import CRS, Transformer
from shapely.geometry import LineString, mapping
from shapely.ops import transform

from .kml_exporter import (
    export_home_kml,
    export_polygon_kml,
    export_profiles_kml,
    export_route_kml,
)
from .models import Mission, OperationalZone, PlanningResult
from .qgc_exporter import export_qgc_plan


def export_all(result: PlanningResult, output_directory: str | Path) -> list[Path]:
    if not result.valid:
        raise ValueError(
            "Экспорт запрещён: " + "; ".join(result.errors or ["есть непокрытые зоны"])
        )
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    zones_root = output / "zones"
    summary_root = output / "summary"
    zones_root.mkdir(parents=True, exist_ok=True)
    summary_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    if result.zones:
        for zone in result.zones:
            zone_directory = zones_root / f"zone_{zone.id:03d}"
            zone_directory.mkdir(parents=True, exist_ok=True)
            created.append(
                export_polygon_kml(
                    zone_directory / "zone_polygon.kml",
                    zone.geometry,
                    result.settings.working_crs,
                    f"Zone {zone.id:03d}",
                )
            )
            created.append(
                export_home_kml(
                    zone_directory / "home.kml",
                    zone.home.point,
                    zone.home.elevation_m,
                    result.settings.working_crs,
                    f"Home {zone.home.id:03d}",
                )
            )
            for mission in zone.missions:
                mission_directory = (
                    zone_directory / f"mission_{mission.id:03d}"
                )
                created.extend(
                    _export_mission_files(
                        result,
                        zone,
                        mission,
                        mission_directory,
                    )
                )
    else:
        for mission in result.missions:
            mission_directory = zones_root / "zone_001" / f"mission_{mission.id:03d}"
            created.append(
                export_qgc_plan(
                    mission_directory / f"mission_{mission.id:03d}.plan",
                    mission,
                    result.home_working,
                    result.settings.working_crs,
                    result.settings.altitude_m,
                    result.settings.speed_mps,
                )
            )

    profiles_path = summary_root / "all_profiles.gpkg"
    profile_assignments = {
        profile.id: (
            mission.home_id,
            mission.zone_id,
            mission.id,
            mission.grid_row,
            mission.grid_col,
            mission.nominal_side_m,
            mission.edge_clipped,
        )
        for mission in result.missions
        for profile in mission.profiles
    }
    profile_frame = gpd.GeoDataFrame(
        {
            "profile_id": [profile.id for profile in result.profiles],
            "home_id": [
                profile_assignments.get(profile.id, (None,) * 7)[0]
                for profile in result.profiles
            ],
            "zone_id": [
                profile_assignments.get(profile.id, (None,) * 7)[1]
                for profile in result.profiles
            ],
            "mission_id": [
                profile_assignments.get(profile.id, (None,) * 7)[2]
                for profile in result.profiles
            ],
            "grid_row": [
                profile_assignments.get(profile.id, (None,) * 7)[3]
                for profile in result.profiles
            ],
            "grid_col": [
                profile_assignments.get(profile.id, (None,) * 7)[4]
                for profile in result.profiles
            ],
            "cell_side": [
                profile_assignments.get(profile.id, (None,) * 7)[5]
                for profile in result.profiles
            ],
            "edge": [
                profile_assignments.get(profile.id, (None,) * 7)[6]
                for profile in result.profiles
            ],
            "offset_m": [profile.offset_m for profile in result.profiles],
            "geometry": [profile.geometry for profile in result.profiles],
        },
        crs=result.settings.working_crs,
    )
    profile_frame.to_file(profiles_path, layer="profiles", driver="GPKG")
    created.append(profiles_path)

    if result.takeoff_sites:
        takeoff_path = summary_root / "takeoff_points.gpkg"
        gpd.GeoDataFrame(
            {
                "home_id": [site.id for site in result.takeoff_sites],
                "elevation_m": [
                    round(site.elevation_m, 2) for site in result.takeoff_sites
                ],
                "slope_deg": [
                    round(site.slope_deg, 2) for site in result.takeoff_sites
                ],
                "roughness_m": [
                    round(site.roughness_m, 2) for site in result.takeoff_sites
                ],
                "geometry": [site.point for site in result.takeoff_sites],
            },
            crs=result.settings.working_crs,
        ).to_file(takeoff_path, layer="takeoff_points", driver="GPKG")
        created.append(takeoff_path)

    if result.zones:
        zones_path = summary_root / "operational_zones.gpkg"
        gpd.GeoDataFrame(
            {
                "zone_id": [zone.id for zone in result.zones],
                "home_id": [zone.home.id for zone in result.zones],
                "relief_m": [round(zone.relief_m, 2) for zone in result.zones],
                "status": [zone.status for zone in result.zones],
                "grid_row": [zone.grid_row for zone in result.zones],
                "grid_col": [zone.grid_col for zone in result.zones],
                "side_m": [zone.nominal_side_m for zone in result.zones],
                "edge": [zone.edge_clipped for zone in result.zones],
                "area_m2": [round(zone.geometry.area, 2) for zone in result.zones],
                "geometry": [zone.geometry for zone in result.zones],
            },
            crs=result.settings.working_crs,
        ).to_file(zones_path, layer="operational_zones", driver="GPKG")
        created.append(zones_path)

        mission_zones_path = summary_root / "mission_zones.gpkg"
        mission_zones = [
            (zone, mission)
            for zone in result.zones
            for mission in zone.missions
            if mission.zone is not None and not mission.zone.is_empty
        ]
        gpd.GeoDataFrame(
            {
                "home_id": [zone.home.id for zone, _ in mission_zones],
                "zone_id": [zone.id for zone, _ in mission_zones],
                "mission_id": [
                    mission.id for _, mission in mission_zones
                ],
                "route_m": [
                    round(mission.route_length_m, 2)
                    for _, mission in mission_zones
                ],
                "time_min": [
                    round(mission.estimated_time_min, 2)
                    for _, mission in mission_zones
                ],
                "status": [
                    mission.status for _, mission in mission_zones
                ],
                "grid_row": [
                    mission.grid_row for _, mission in mission_zones
                ],
                "grid_col": [
                    mission.grid_col for _, mission in mission_zones
                ],
                "side_m": [
                    mission.nominal_side_m for _, mission in mission_zones
                ],
                "edge": [
                    mission.edge_clipped for _, mission in mission_zones
                ],
                "area_m2": [
                    round(mission.zone.area, 2)
                    for _, mission in mission_zones
                ],
                "geometry": [
                    mission.zone for _, mission in mission_zones
                ],
            },
            crs=result.settings.working_crs,
        ).to_file(
            mission_zones_path,
            layer="mission_zones",
            driver="GPKG",
        )
        created.append(mission_zones_path)

    preview_path = summary_root / "missions_preview.geojson"
    _write_preview_geojson(result, preview_path)
    created.append(preview_path)

    summary_path = summary_root / "mission_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Home ID",
                "Zone ID",
                "Mission ID",
                "Profiles",
                "Route length, m",
                "Estimated time, min",
                "Home elevation, m",
                "Zone relief, m",
                "Zone side, m",
                "Mission side, m",
                "Mission area, m2",
                "Grid row",
                "Grid col",
                "Edge clipped",
                "Status",
            ]
        )
        if result.zones:
            for zone in result.zones:
                for mission in zone.missions:
                    writer.writerow(
                        [
                            zone.home.id,
                            zone.id,
                            mission.id,
                            len(mission.profiles),
                            round(mission.route_length_m, 2),
                            round(mission.estimated_time_min, 2),
                            round(zone.home.elevation_m, 2),
                            round(zone.relief_m, 2),
                            zone.nominal_side_m,
                            mission.nominal_side_m,
                            round(mission.zone.area, 2),
                            mission.grid_row,
                            mission.grid_col,
                            mission.edge_clipped,
                            mission.status,
                        ]
                    )
        else:
            for mission in result.missions:
                writer.writerow(
                    [
                        mission.home_id,
                        mission.zone_id,
                        mission.id,
                        len(mission.profiles),
                        round(mission.route_length_m, 2),
                        round(mission.estimated_time_min, 2),
                        "",
                        "",
                        "",
                        "",
                        "",
                        mission.grid_row,
                        mission.grid_col,
                        mission.edge_clipped,
                        mission.status,
                    ]
                )
    created.append(summary_path)
    return created


def export_mission(
    result: PlanningResult,
    zone_id: int,
    mission_id: int,
    output_directory: str | Path,
) -> list[Path]:
    zone = next((item for item in result.zones if item.id == zone_id), None)
    if zone is None:
        raise ValueError(f"Зона {zone_id} не найдена.")
    mission = next(
        (item for item in zone.missions if item.id == mission_id),
        None,
    )
    if mission is None:
        raise ValueError(
            f"Миссия {zone_id}.{mission_id} не найдена."
        )
    if mission.status.startswith("Недопустимо"):
        raise ValueError(
            f"Экспорт миссии {zone_id}.{mission_id} запрещён: "
            f"{mission.status}"
        )
    if mission.zone is None or mission.zone.is_empty:
        raise ValueError(
            f"У миссии {zone_id}.{mission_id} отсутствует полигон."
        )
    if not mission.profiles:
        raise ValueError(
            f"У миссии {zone_id}.{mission_id} отсутствуют профили."
        )
    if not mission.route_points:
        raise ValueError(
            f"У миссии {zone_id}.{mission_id} отсутствует маршрут."
        )

    basename = f"zone_{zone.id:03d}_mission_{mission.id:03d}"
    mission_directory = Path(output_directory) / basename
    return _export_mission_files(
        result,
        zone,
        mission,
        mission_directory,
    )


def _export_mission_files(
    result: PlanningResult,
    zone: OperationalZone,
    mission: Mission,
    mission_directory: Path,
) -> list[Path]:
    mission_directory.mkdir(parents=True, exist_ok=True)
    basename = f"zone_{zone.id:03d}_mission_{mission.id:03d}"
    created = [
        export_qgc_plan(
            mission_directory / f"{basename}.plan",
            mission,
            zone.home.point,
            result.settings.working_crs,
            result.settings.altitude_m,
            result.settings.speed_mps,
            result.settings.altitude_mode,
            zone.home.elevation_m,
            result.settings.mission_mode,
            result.settings.profile_spacing_m,
            result.settings.azimuth_deg,
            result.settings.climb_speed_mps,
            result.settings.descent_speed_mps,
            result.settings.terrain_adjust_tolerance_m,
        ),
        export_profiles_kml(
            mission_directory / f"{basename}_grid.kml",
            mission.profiles,
            result.settings.working_crs,
            f"Grid {basename}",
        ),
    ]
    if mission.zone is not None and not mission.zone.is_empty:
        created.append(
            export_polygon_kml(
                mission_directory / f"{basename}_polygon.kml",
                mission.zone,
                result.settings.working_crs,
                f"Polygon {basename}",
            )
        )
    route_points = [
        zone.home.point,
        *mission.route_points,
        zone.home.point,
    ]
    route_terrain = (
        mission.terrain_elevations_m
        if mission.terrain_elevations_m
        else [zone.home.elevation_m] * len(mission.route_points)
    )
    route_altitudes = [
        zone.home.elevation_m + result.settings.altitude_m,
        *[
            elevation + result.settings.altitude_m
            for elevation in route_terrain
        ],
        zone.home.elevation_m + result.settings.altitude_m,
    ]
    created.append(
        export_route_kml(
            mission_directory / f"{basename}_route.kml",
            route_points,
            route_altitudes,
            result.settings.working_crs,
            f"Route {basename}",
        )
    )
    mission_summary = mission_directory / f"{basename}_summary.csv"
    _write_single_mission_summary(mission_summary, zone, mission, result)
    created.append(mission_summary)
    return created


def _write_single_mission_summary(
    path: Path, zone, mission, result: PlanningResult
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Parameter", "Value"])
        writer.writerows(
            [
                ["Home ID", zone.home.id],
                ["Zone ID", zone.id],
                ["Mission ID", mission.id],
                ["Profiles", len(mission.profiles)],
                ["Route length, m", round(mission.route_length_m, 2)],
                ["Estimated time, min", round(mission.estimated_time_min, 2)],
                ["Home elevation, m", round(zone.home.elevation_m, 2)],
                ["Zone relief, m", round(zone.relief_m, 2)],
                ["Zone nominal side, m", zone.nominal_side_m],
                ["Mission nominal side, m", mission.nominal_side_m],
                ["Mission area, m2", round(mission.zone.area, 2)],
                ["Grid row", mission.grid_row],
                ["Grid col", mission.grid_col],
                ["Edge clipped", mission.edge_clipped],
                ["AGL, m", result.settings.altitude_m],
                ["Speed, m/s", result.settings.speed_mps],
                ["QGC mode", result.settings.mission_mode],
                ["Altitude mode", result.settings.altitude_mode],
                ["Status", mission.status],
            ]
        )


def _write_preview_geojson(result: PlanningResult, path: Path) -> None:
    transformer = Transformer.from_crs(
        result.settings.working_crs, CRS.from_epsg(4326), always_xy=True
    )
    features: list[dict[str, object]] = []
    for zone in result.zones:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "operational_zone",
                    "zone_id": zone.id,
                    "home_id": zone.home.id,
                    "relief_m": round(zone.relief_m, 2),
                    "status": zone.status,
                    "grid_row": zone.grid_row,
                    "grid_col": zone.grid_col,
                    "side_m": zone.nominal_side_m,
                    "edge_clipped": zone.edge_clipped,
                },
                "geometry": mapping(transform(transformer.transform, zone.geometry)),
            }
        )
        home = transform(transformer.transform, zone.home.point)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "home",
                    "home_id": zone.home.id,
                    "elevation_m": round(zone.home.elevation_m, 2),
                },
                "geometry": mapping(home),
            }
        )
    for mission in result.missions:
        if mission.zone is not None and not mission.zone.is_empty:
            zone = transform(transformer.transform, mission.zone)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "zone",
                        "mission_id": mission.id,
                        "zone_id": mission.zone_id,
                        "home_id": mission.home_id,
                        "route_length_m": round(mission.route_length_m, 2),
                        "grid_row": mission.grid_row,
                        "grid_col": mission.grid_col,
                        "side_m": mission.nominal_side_m,
                        "edge_clipped": mission.edge_clipped,
                    },
                    "geometry": mapping(zone),
                }
            )
        route_coordinates = [
            (
                next(
                    (
                        zone.home.point.x,
                        zone.home.point.y,
                    )
                    for zone in result.zones
                    if zone.id == mission.zone_id
                )
                if result.zones
                else (result.home_working.x, result.home_working.y)
            ),
            *((point.x, point.y) for point in mission.route_points),
            (
                next(
                    (
                        zone.home.point.x,
                        zone.home.point.y,
                    )
                    for zone in result.zones
                    if zone.id == mission.zone_id
                )
                if result.zones
                else (result.home_working.x, result.home_working.y)
            ),
        ]
        route = transform(
            transformer.transform,
            LineString(route_coordinates),
        )
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "route",
                    "mission_id": mission.id,
                    "zone_id": mission.zone_id,
                    "home_id": mission.home_id,
                },
                "geometry": mapping(route),
            }
        )
    path.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
