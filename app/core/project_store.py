from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from pyproj import CRS, Transformer
from shapely.errors import GEOSException
from shapely.geometry import mapping
from shapely.ops import transform
from shapely.wkb import dumps as dump_wkb
from shapely.wkb import loads as load_wkb

from .models import (
    LoadedPolygon,
    Mission,
    OperationalZone,
    PlannerSettings,
    PlanningResult,
    Profile,
    TakeoffSite,
)

PROJECT_VERSION = 2
LEGACY_PROJECT_VERSION = 1
PROJECT_SUFFIX = ".bpla"
_MANIFEST_NAME = "project.json"
_POLYGON_NAME = "polygon.geojson"
_RESULT_NAME = "result.json"
_DEM_STEM = "terrain"


@dataclass(frozen=True)
class ProjectSession:
    polygon_path: Path
    dem_path: Path
    settings: PlannerSettings
    homes_wgs84: list[tuple[float, float]] | None
    forced_grid_sizes: tuple[float, float] | None
    project_path: Path
    result: PlanningResult | None = None


def save_project(
    project_path: str | Path,
    result: PlanningResult,
    dem_path: str | Path,
) -> Path:
    destination = Path(project_path)
    if destination.suffix.lower() != PROJECT_SUFFIX:
        destination = destination.with_suffix(PROJECT_SUFFIX)
    terrain_path = Path(dem_path)
    if not terrain_path.is_file():
        raise FileNotFoundError(terrain_path)

    destination.parent.mkdir(parents=True, exist_ok=True)
    dem_name = f"{_DEM_STEM}{terrain_path.suffix.lower()}"
    manifest = {
        "format": "BPLA terrain mission project",
        "version": PROJECT_VERSION,
        "dem_file": dem_name,
        "settings": _settings_to_dict(result.settings),
        "homes_wgs84": _homes_wgs84(result),
        "forced_grid_sizes": _grid_sizes(result),
        "result_file": _RESULT_NAME,
    }
    polygon_geojson = _polygon_geojson(result)
    result_snapshot = _result_to_dict(result)

    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with ZipFile(temporary, "w") as archive:
            archive.writestr(
                _MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2),
                compress_type=ZIP_DEFLATED,
            )
            archive.writestr(
                _POLYGON_NAME,
                json.dumps(polygon_geojson, ensure_ascii=False),
                compress_type=ZIP_DEFLATED,
            )
            archive.writestr(
                _RESULT_NAME,
                json.dumps(result_snapshot, ensure_ascii=False),
                compress_type=ZIP_DEFLATED,
            )
            archive.write(terrain_path, dem_name, compress_type=ZIP_STORED)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_project(
    project_path: str | Path,
    work_directory: str | Path | None = None,
) -> ProjectSession:
    source = Path(project_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    target = (
        Path(work_directory)
        if work_directory is not None
        else Path(tempfile.mkdtemp(prefix="bpla_project_"))
    )
    target.mkdir(parents=True, exist_ok=True)

    try:
        with ZipFile(source, "r") as archive:
            manifest = json.loads(archive.read(_MANIFEST_NAME))
            _validate_manifest(manifest)
            dem_name = manifest["dem_file"]
            if Path(dem_name).name != dem_name or not dem_name.startswith(_DEM_STEM):
                raise ValueError("Некорректное имя DEM в проекте.")
            polygon_path = target / _POLYGON_NAME
            dem_path = target / dem_name
            polygon_path.write_bytes(archive.read(_POLYGON_NAME))
            with archive.open(dem_name) as source_dem, dem_path.open("wb") as target_dem:
                shutil.copyfileobj(source_dem, target_dem)
            result = None
            if manifest["version"] == PROJECT_VERSION:
                result_name = manifest.get("result_file")
                if result_name != _RESULT_NAME:
                    raise ValueError("В проекте отсутствует снимок расчёта.")
                snapshot = json.loads(archive.read(_RESULT_NAME))
                result = _result_from_dict(
                    snapshot,
                    polygon_path,
                    dem_path,
                )
    except Exception as error:
        if work_directory is None:
            shutil.rmtree(target, ignore_errors=True)
        if isinstance(error, (BadZipFile, KeyError, json.JSONDecodeError)):
            raise ValueError(
                "Файл проекта повреждён или имеет неверный формат."
            ) from error
        raise

    homes = manifest.get("homes_wgs84")
    sizes = manifest.get("forced_grid_sizes")
    return ProjectSession(
        polygon_path=polygon_path,
        dem_path=dem_path,
        settings=_settings_from_dict(manifest["settings"]),
        homes_wgs84=(
            [(float(item[0]), float(item[1])) for item in homes] if homes else None
        ),
        forced_grid_sizes=(
            (float(sizes[0]), float(sizes[1])) if sizes else None
        ),
        project_path=source.resolve(),
        result=result,
    )


def _settings_to_dict(settings: PlannerSettings) -> dict[str, object]:
    data = asdict(settings)
    data["working_crs"] = settings.working_crs.to_string()
    return data


def _settings_from_dict(data: dict[str, object]) -> PlannerSettings:
    values = dict(data)
    values["working_crs"] = CRS.from_user_input(values["working_crs"])
    return PlannerSettings(**values)  # type: ignore[arg-type]


def _homes_wgs84(result: PlanningResult) -> list[list[float]]:
    converter = Transformer.from_crs(
        result.settings.working_crs, CRS.from_epsg(4326), always_xy=True
    )
    homes = []
    for site in result.takeoff_sites:
        longitude, latitude = converter.transform(site.point.x, site.point.y)
        homes.append([latitude, longitude])
    return homes


def _grid_sizes(result: PlanningResult) -> list[float] | None:
    if not result.zones or not result.missions:
        return None
    return [
        result.zones[0].nominal_side_m,
        result.missions[0].nominal_side_m,
    ]


def _polygon_geojson(result: PlanningResult) -> dict[str, object]:
    converter = Transformer.from_crs(
        result.settings.working_crs, CRS.from_epsg(4326), always_xy=True
    )
    geometry = transform(converter.transform, result.polygon.geometry)
    return {
        "type": "FeatureCollection",
        "name": "project_polygon",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": mapping(geometry),
            }
        ],
    }


def _validate_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("Некорректный manifest проекта.")
    if manifest.get("version") not in {
        LEGACY_PROJECT_VERSION,
        PROJECT_VERSION,
    }:
        raise ValueError(
            f"Версия проекта не поддерживается: {manifest.get('version')}."
        )
    if not isinstance(manifest.get("settings"), dict):
        raise ValueError("В проекте отсутствуют настройки.")


def _result_to_dict(result: PlanningResult) -> dict[str, object]:
    profiles = {
        profile.id: {
            "id": profile.id,
            "offset_m": profile.offset_m,
            "geometry": _geometry_to_text(profile.geometry),
        }
        for profile in result.profiles
    }
    homes = {
        site.id: {
            "id": site.id,
            "point": _geometry_to_text(site.point),
            "elevation_m": site.elevation_m,
            "slope_deg": site.slope_deg,
            "roughness_m": site.roughness_m,
        }
        for site in result.takeoff_sites
    }
    missions = {
        _mission_key(mission): {
            "id": mission.id,
            "profile_ids": [profile.id for profile in mission.profiles],
            "route_points": [
                _geometry_to_text(point) for point in mission.route_points
            ],
            "route_length_m": mission.route_length_m,
            "estimated_time_min": mission.estimated_time_min,
            "status": mission.status,
            "zone": (
                _geometry_to_text(mission.zone)
                if mission.zone is not None
                else None
            ),
            "zone_id": mission.zone_id,
            "home_id": mission.home_id,
            "terrain_elevations_m": mission.terrain_elevations_m,
            "grid_row": mission.grid_row,
            "grid_col": mission.grid_col,
            "nominal_side_m": mission.nominal_side_m,
            "edge_clipped": mission.edge_clipped,
        }
        for mission in result.missions
    }
    zones = [
        {
            "id": zone.id,
            "home_id": zone.home.id,
            "geometry": _geometry_to_text(zone.geometry),
            "profile_ids": [profile.id for profile in zone.profiles],
            "mission_keys": [
                _mission_key(mission) for mission in zone.missions
            ],
            "relief_m": zone.relief_m,
            "status": zone.status,
            "grid_row": zone.grid_row,
            "grid_col": zone.grid_col,
            "nominal_side_m": zone.nominal_side_m,
            "edge_clipped": zone.edge_clipped,
        }
        for zone in result.zones
    ]
    return {
        "settings": _settings_to_dict(result.settings),
        "polygon": {
            "source_crs": result.polygon.source_crs.to_string(),
            "working_crs": result.polygon.working_crs.to_string(),
            "geometry": _geometry_to_text(result.polygon.geometry),
        },
        "home_working": _geometry_to_text(result.home_working),
        "profiles": list(profiles.values()),
        "missions": list(missions.values()),
        "takeoff_sites": list(homes.values()),
        "zones": zones,
        "valid": result.valid,
        "errors": list(result.errors),
    }


def _result_from_dict(
    data: object,
    polygon_path: Path,
    dem_path: Path,
) -> PlanningResult:
    if not isinstance(data, dict):
        raise ValueError("Некорректный снимок расчёта.")
    try:
        settings = _settings_from_dict(data["settings"])
        polygon_data = data["polygon"]
        profiles = {
            int(item["id"]): Profile(
                id=int(item["id"]),
                offset_m=float(item["offset_m"]),
                geometry=_geometry_from_text(item["geometry"]),
            )
            for item in data["profiles"]
        }
        homes = {
            int(item["id"]): TakeoffSite(
                id=int(item["id"]),
                point=_geometry_from_text(item["point"]),
                elevation_m=float(item["elevation_m"]),
                slope_deg=float(item["slope_deg"]),
                roughness_m=float(item["roughness_m"]),
            )
            for item in data["takeoff_sites"]
        }
        missions: dict[str, Mission] = {}
        for item in data["missions"]:
            mission = Mission(
                id=int(item["id"]),
                profiles=[profiles[int(value)] for value in item["profile_ids"]],
                route_points=[
                    _geometry_from_text(value) for value in item["route_points"]
                ],
                route_length_m=float(item["route_length_m"]),
                estimated_time_min=float(item["estimated_time_min"]),
                status=str(item["status"]),
                zone=(
                    _geometry_from_text(item["zone"])
                    if item["zone"] is not None
                    else None
                ),
                zone_id=int(item["zone_id"]),
                home_id=int(item["home_id"]),
                terrain_elevations_m=[
                    float(value) for value in item["terrain_elevations_m"]
                ],
                grid_row=int(item["grid_row"]),
                grid_col=int(item["grid_col"]),
                nominal_side_m=float(item["nominal_side_m"]),
                edge_clipped=bool(item["edge_clipped"]),
            )
            missions[_mission_key(mission)] = mission
        zones = [
            OperationalZone(
                id=int(item["id"]),
                home=homes[int(item["home_id"])],
                geometry=_geometry_from_text(item["geometry"]),
                profiles=[
                    profiles[int(value)] for value in item["profile_ids"]
                ],
                missions=[
                    missions[str(value)] for value in item["mission_keys"]
                ],
                relief_m=float(item["relief_m"]),
                status=str(item["status"]),
                grid_row=int(item["grid_row"]),
                grid_col=int(item["grid_col"]),
                nominal_side_m=float(item["nominal_side_m"]),
                edge_clipped=bool(item["edge_clipped"]),
            )
            for item in data["zones"]
        ]
        polygon = LoadedPolygon(
            source_path=polygon_path,
            source_crs=CRS.from_user_input(polygon_data["source_crs"]),
            working_crs=CRS.from_user_input(polygon_data["working_crs"]),
            geometry=_geometry_from_text(polygon_data["geometry"]),
        )
        ordered_profiles = [
            profiles[int(item["id"])] for item in data["profiles"]
        ]
        ordered_missions = [
            missions[
                f"{int(item['zone_id'])}:{int(item['id'])}"
            ]
            for item in data["missions"]
        ]
        ordered_homes = [
            homes[int(item["id"])] for item in data["takeoff_sites"]
        ]
        return PlanningResult(
            polygon=polygon,
            home_working=_geometry_from_text(data["home_working"]),
            settings=settings,
            profiles=ordered_profiles,
            missions=ordered_missions,
            takeoff_sites=ordered_homes,
            zones=zones,
            dem_path=dem_path,
            valid=bool(data["valid"]),
            errors=[str(value) for value in data["errors"]],
        )
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ValueError("Снимок расчёта повреждён или неполон.") from error


def _geometry_to_text(geometry) -> str:
    return base64.b64encode(dump_wkb(geometry, hex=False)).decode("ascii")


def _geometry_from_text(value: str):
    try:
        return load_wkb(base64.b64decode(value, validate=True))
    except (binascii.Error, GEOSException, TypeError, ValueError) as error:
        raise ValueError("Некорректная геометрия в снимке расчёта.") from error


def _mission_key(mission: Mission) -> str:
    return f"{mission.zone_id}:{mission.id}"
