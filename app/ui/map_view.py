from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView


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
const colors = ['#e41a1c','#377eb8','#4daf4a','#984ea3','#ff7f00',
                '#a65628','#f781bf','#999999','#66c2a5','#e6ab02'];
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
  return L.geoJSON(feature, {
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
</script>
</body>
</html>
"""
