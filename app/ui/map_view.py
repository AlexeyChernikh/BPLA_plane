from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.core.mission_colors import MISSION_COLORS


class MapView(QWebEngineView):
    homeSelected = Signal(float, float)
    homeMoved = Signal(int, float, float)
    missionContextRequested = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_html_path: Path | None = None
        self._obsolete_html_paths: list[Path] = []
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.titleChanged.connect(self._handle_title)
        self.loadFinished.connect(self._cleanup_obsolete_html)
        self.show_geojson({"type": "FeatureCollection", "features": []})

    def show_geojson(
        self,
        feature_collection: dict[str, object],
        home: tuple[float, float] | None = None,
        terrain_overlay: tuple[str, list[list[float]]] | None = None,
    ) -> None:
        document = (
            _HTML_TEMPLATE.replace(
                "__GEOJSON__", json.dumps(feature_collection, ensure_ascii=False)
            )
            .replace("__MISSION_COLORS__", json.dumps(MISSION_COLORS))
            .replace("__HOME__", json.dumps(home))
            .replace(
                "__TERRAIN__",
                json.dumps(
                    {
                        "uri": terrain_overlay[0],
                        "bounds": terrain_overlay[1],
                    }
                    if terrain_overlay
                    else None
                ),
            )
        )
        descriptor, filename = tempfile.mkstemp(
            prefix="qgc_mission_map_", suffix=".html"
        )
        os.close(descriptor)
        html_path = Path(filename)
        html_path.write_text(document, encoding="utf-8")
        if self._current_html_path is not None:
            self._obsolete_html_paths.append(self._current_html_path)
        self._current_html_path = html_path
        self.setUrl(QUrl.fromLocalFile(str(html_path)))

    def export_missions_png(
        self,
        path: str | Path,
        finished: Callable[[Path | None, str | None], None],
        size: QSize = QSize(1920, 1080),
    ) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        old_size = self.size()
        old_minimum = self.minimumSize()
        old_maximum = self.maximumSize()
        self.setFixedSize(size)

        def capture(_result=None) -> None:
            pixmap = self.grab()
            saved = pixmap.save(str(output), "PNG")

            def restored(_restore_result=None) -> None:
                self.setMinimumSize(old_minimum)
                self.setMaximumSize(old_maximum)
                self.resize(old_size)
                if saved:
                    finished(output, None)
                else:
                    finished(None, "Не удалось сохранить PNG карты.")

            self.page().runJavaScript("restoreMissionExport()", restored)

        def prepared(_result=None) -> None:
            # Give Leaflet and remote map tiles time to redraw at Full HD size.
            QTimer.singleShot(2500, capture)

        self.page().runJavaScript("prepareMissionExport()", prepared)

    def _handle_title(self, title: str) -> None:
        if title.startswith("movehome:"):
            try:
                identifier, latitude, longitude, *_ = title.removeprefix(
                    "movehome:"
                ).split(",")
                self.homeMoved.emit(
                    int(identifier), float(latitude), float(longitude)
                )
            except (TypeError, ValueError):
                pass
            return
        if title.startswith("home:"):
            try:
                latitude, longitude, *_ = title.removeprefix("home:").split(",")
                self.homeSelected.emit(float(latitude), float(longitude))
            except (TypeError, ValueError):
                pass
            return
        if title.startswith("missioncontext:"):
            try:
                zone_id, mission_id, *_ = title.removeprefix(
                    "missioncontext:"
                ).split(",")
                self.missionContextRequested.emit(
                    int(zone_id), int(mission_id)
                )
            except (TypeError, ValueError):
                pass

    def _cleanup_obsolete_html(self, _success: bool) -> None:
        for path in self._obsolete_html_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._obsolete_html_paths.clear()

    def closeEvent(self, event) -> None:
        paths = [*self._obsolete_html_paths]
        if self._current_html_path is not None:
            paths.append(self._current_html_path)
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        super().closeEvent(event)


_HTML_TEMPLATE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Карта миссий</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map { width: 100%; height: 100%; margin: 0; }
    .hint { background: white; padding: 4px 8px; border-radius: 3px; }
    .legend-tree { background:rgba(255,255,255,.96); padding:8px; border-radius:4px;
      min-width:245px; max-width:340px; max-height:72vh; overflow:auto;
      font:13px/1.35 Arial,sans-serif; box-shadow:0 1px 5px #777; }
    .legend-tree label { display:block; white-space:nowrap; margin:2px 0; }
    .legend-tree details { margin-left:4px; }
    .legend-tree .missions { margin-left:18px; }
    .legend-tree .global { border-bottom:1px solid #bbb; padding-bottom:5px; margin-bottom:5px; }
    .mission-export-label { background:rgba(255,255,255,.92); border:1px solid #555;
      border-radius:3px; box-shadow:none; color:#111; font:bold 15px Arial,sans-serif;
      padding:3px 6px; white-space:nowrap; }
    body.export-mode .leaflet-control-container { display:none; }
  </style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map = L.map('map').setView([53.43, 96.56], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}).addTo(map);
const colors = __MISSION_COLORS__;
const data = __GEOJSON__;
const terrain = __TERRAIN__;
const terrainLayer = terrain ? L.imageOverlay(
  terrain.uri, terrain.bounds, {opacity:0.62}
) : null;
function style(feature) {
  const p = feature.properties || {};
  const id = p.kind === 'zone'
    ? Number((p.zone_id || 1) * 37 + (p.mission_id || 1))
    : Number(p.zone_id || p.mission_id || 1);
  const color = colors[(id - 1) % colors.length];
  if (p.kind === 'operational_zone') {
    const invalid = String(p.status || '').startsWith('Недопустимо');
    return {color:invalid ? '#ff0000' : color, fillColor:color,
            fillOpacity:invalid ? .1 : .2, weight:invalid ? 4 : 2};
  }
  if (p.kind === 'zone') {
    return {color:color, fillColor:color, fillOpacity:.34, weight:2};
  }
  if (p.kind === 'profile') return {color:'#222', opacity:.7, weight:1};
  return {color:color, opacity:.95, weight:3};
}
const globalState = {
  dem:true, polygon:true, homes:true, zones:true,
  missions:true, profiles:true, routes:true
};
const sourceLayers = [];
const zoneEntries = {};
const allBounds = L.latLngBounds();
function zoneEntry(id) {
  if (!zoneEntries[id]) {
    zoneEntries[id] = {enabled:true, zoneLayers:[], homeLayers:[], missions:{}};
  }
  return zoneEntries[id];
}
function missionEntry(zoneId, missionId) {
  const zone = zoneEntry(zoneId);
  if (!zone.missions[missionId]) {
    zone.missions[missionId] = {
      enabled:true, polygonLayers:[], profileLayers:[], routeLayers:[]
    };
  }
  return zone.missions[missionId];
}
function makeFeatureLayer(feature) {
  const featureLayer = L.geoJSON(feature, {
    style:style,
    onEachFeature:(f, layer) => {
      const p = f.properties || {};
      if (p.kind === 'operational_zone') {
        layer.bindTooltip('Крупная зона ' + p.zone_id + ' / Home ' + p.home_id);
      } else if (p.kind === 'zone') {
        layer.bindTooltip('Зона ' + p.zone_id + ' / миссия ' + p.mission_id);
      }
      if (['zone','profile','route'].includes(p.kind)) {
        layer.on('contextmenu', event => {
          L.DomEvent.preventDefault(event.originalEvent);
          L.DomEvent.stopPropagation(event.originalEvent);
          document.title = 'missioncontext:' + p.zone_id + ',' +
            p.mission_id + ',' + Date.now();
        });
      }
    },
    pointToLayer:(f, latlng) => {
      if (f.properties && f.properties.kind === 'home') {
        const marker = L.marker(latlng, {draggable:true});
        marker.bindTooltip('Home ' + f.properties.home_id +
                           '<br>H=' + f.properties.elevation_m + ' м');
        marker.on('dragend', event => {
          const p = event.target.getLatLng();
          document.title = 'movehome:' + f.properties.home_id + ',' +
            p.lat.toFixed(9) + ',' + p.lng.toFixed(9) + ',' + Date.now();
        });
        return marker;
      }
      return L.circleMarker(latlng);
    }
  });
  featureLayer._missionProperties = feature.properties || {};
  return featureLayer;
}
for (const feature of data.features || []) {
  const p = feature.properties || {};
  const layer = makeFeatureLayer(feature);
  if (layer.getBounds && layer.getBounds().isValid()) allBounds.extend(layer.getBounds());
  if (p.kind === 'polygon') sourceLayers.push(layer);
  else if (p.kind === 'operational_zone') zoneEntry(p.zone_id).zoneLayers.push(layer);
  else if (p.kind === 'home') zoneEntry(p.home_id).homeLayers.push(layer);
  else if (p.kind === 'zone') missionEntry(p.zone_id,p.mission_id).polygonLayers.push(layer);
  else if (p.kind === 'profile') missionEntry(p.zone_id,p.mission_id).profileLayers.push(layer);
  else if (p.kind === 'route') missionEntry(p.zone_id,p.mission_id).routeLayers.push(layer);
}
function showLayer(layer, visible) {
  if (visible && !map.hasLayer(layer)) layer.addTo(map);
  if (!visible && map.hasLayer(layer)) map.removeLayer(layer);
}
function refreshLayers() {
  if (terrainLayer) showLayer(terrainLayer, globalState.dem);
  for (const layer of sourceLayers) showLayer(layer, globalState.polygon);
  for (const zone of Object.values(zoneEntries)) {
    for (const layer of zone.zoneLayers) showLayer(layer, globalState.zones && zone.enabled);
    for (const layer of zone.homeLayers) showLayer(layer, globalState.homes && zone.enabled);
    for (const mission of Object.values(zone.missions)) {
      const enabled = zone.enabled && mission.enabled;
      for (const layer of mission.polygonLayers) showLayer(layer, globalState.missions && enabled);
      for (const layer of mission.profileLayers) showLayer(layer, globalState.profiles && enabled);
      for (const layer of mission.routeLayers) showLayer(layer, globalState.routes && enabled);
    }
  }
}
refreshLayers();
if (allBounds.isValid()) map.fitBounds(allBounds, {padding:[20,20]});
const legend = L.control({position:'topright'});
legend.onAdd = () => {
  const div = L.DomUtil.create('div','legend-tree');
  L.DomEvent.disableClickPropagation(div);
  const global = document.createElement('div');
  global.className = 'global';
  const names = {dem:'DEM',polygon:'Исходный полигон',homes:'Home',
    zones:'Крупные зоны',missions:'Миссии',profiles:'Профили',routes:'Маршруты'};
  for (const [key,title] of Object.entries(names)) {
    const label = document.createElement('label');
    label.innerHTML = '<input type="checkbox" checked> ' + title;
    label.querySelector('input').addEventListener('change', event => {
      globalState[key] = event.target.checked; refreshLayers();
    });
    global.appendChild(label);
  }
  div.appendChild(global);
  for (const zoneId of Object.keys(zoneEntries).map(Number).sort((a,b)=>a-b)) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.innerHTML = '<input type="checkbox" checked> Зона ' +
      String(zoneId).padStart(3,'0');
    summary.querySelector('input').addEventListener('click', event => {
      event.stopPropagation(); zoneEntries[zoneId].enabled = event.target.checked;
      refreshLayers();
    });
    details.appendChild(summary);
    const missions = document.createElement('div');
    missions.className = 'missions';
    const ids = Object.keys(zoneEntries[zoneId].missions).map(Number).sort((a,b)=>a-b);
    for (const missionId of ids) {
      const label = document.createElement('label');
      label.innerHTML = '<input type="checkbox" checked> Миссия ' +
        String(missionId).padStart(3,'0');
      label.querySelector('input').addEventListener('change', event => {
        zoneEntries[zoneId].missions[missionId].enabled = event.target.checked;
        refreshLayers();
      });
      missions.appendChild(label);
    }
    details.appendChild(missions);
    div.appendChild(details);
  }
  return div;
};
legend.addTo(map);
const initialHome = __HOME__;
if (initialHome) L.marker(initialHome).addTo(map).bindTooltip('Home');
const hint = L.control({position:'bottomright'});
hint.onAdd = () => {
  const div = L.DomUtil.create('div','hint');
  div.innerHTML = 'Home можно перетаскивать';
  return div;
};
hint.addTo(map);
let exportSnapshot = null;
let exportLabels = [];
function prepareMissionExport() {
  exportSnapshot = {
    globalState: JSON.parse(JSON.stringify(globalState)),
    zones: {}
  };
  for (const [zoneId,zone] of Object.entries(zoneEntries)) {
    exportSnapshot.zones[zoneId] = {enabled:zone.enabled, missions:{}};
    for (const [missionId,mission] of Object.entries(zone.missions)) {
      exportSnapshot.zones[zoneId].missions[missionId] = mission.enabled;
    }
  }
  document.body.classList.add('export-mode');
  globalState.dem = false;
  globalState.polygon = false;
  globalState.homes = false;
  globalState.zones = false;
  globalState.missions = true;
  globalState.profiles = false;
  globalState.routes = false;
  const missionBounds = L.latLngBounds();
  for (const zone of Object.values(zoneEntries)) {
    zone.enabled = true;
    for (const mission of Object.values(zone.missions)) {
      mission.enabled = true;
      for (const layer of mission.polygonLayers) {
        if (layer.getBounds && layer.getBounds().isValid()) {
          missionBounds.extend(layer.getBounds());
        }
        const p = layer._missionProperties || {};
        if (p.label_lat !== undefined && p.label_lon !== undefined) {
          const label = L.tooltip({
            permanent:true, direction:'top', className:'mission-export-label'
          }).setLatLng([p.label_lat,p.label_lon]).setContent(
            'Зона ' + String(p.zone_id).padStart(3,'0') +
            ' / Миссия ' + String(p.mission_id).padStart(3,'0')
          ).addTo(map);
          exportLabels.push(label);
        }
      }
    }
  }
  refreshLayers();
  if (missionBounds.isValid()) map.fitBounds(missionBounds, {padding:[70,70]});
  map.invalidateSize();
  return true;
}
function restoreMissionExport() {
  for (const label of exportLabels) map.removeLayer(label);
  exportLabels = [];
  if (exportSnapshot) {
    Object.assign(globalState, exportSnapshot.globalState);
    for (const [zoneId,state] of Object.entries(exportSnapshot.zones)) {
      zoneEntries[zoneId].enabled = state.enabled;
      for (const [missionId,enabled] of Object.entries(state.missions)) {
        zoneEntries[zoneId].missions[missionId].enabled = enabled;
      }
    }
  }
  exportSnapshot = null;
  document.body.classList.remove('export-mode');
  refreshLayers();
  if (allBounds.isValid()) map.fitBounds(allBounds, {padding:[20,20]});
  map.invalidateSize();
  return true;
}
</script>
</body>
</html>
"""
