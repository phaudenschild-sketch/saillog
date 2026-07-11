"""Lokaler Kartenserver für masarasi — AIS-Ziele + Törn auf einer Karte.

Startet einen kleinen HTTP-Server (nur an 127.0.0.1 gebunden), der eine
Leaflet-Karte mit OpenFreeMap-Vektorkacheln ausliefert. Die Seite fragt
regelmäßig `/data.json` ab und zeichnet:

  * das eigene Schiff (Position, Kurs über Grund bzw. Heading),
  * alle AIS-Ziele (mit echter Richtung, Name, MMSI, SOG/COG),
  * den Track des ausgewählten Törns.

Der Server selbst ist reine Standardbibliothek. Die Karte lädt Leaflet und
die OpenFreeMap-Vektorkacheln aus dem Netz (an Bord über Starlink verfügbar);
ohne Internet bleibt der Kartenhintergrund leer, Schiffe und Track werden
aber trotzdem gezeichnet.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Signaturen der Datenlieferanten (werden von der GUI gestellt)
OwnProvider = Callable[[], Optional[Dict]]
TargetsProvider = Callable[[], List[Dict]]
TrackProvider = Callable[[], Dict]
EntriesProvider = Callable[[], List[Dict]]
InfoProvider = Callable[[], str]
# liefert (Bytes, MIME) zu einer Bild-id (oder None)
ImageProvider = Callable[[int], Optional[tuple]]


class _Handler(BaseHTTPRequestHandler):
    """Bedient „/" (Karte) und „/data.json" (Live-Daten)."""

    def log_message(self, *args) -> None:  # keine Konsolenausgabe
        pass

    def do_GET(self) -> None:  # noqa: N802 (von BaseHTTPRequestHandler vorgegeben)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/data.json":
            data = self.server.masarasi_data()  # type: ignore[attr-defined]
            self._send(json.dumps(data).encode("utf-8"), "application/json",
                       cache=False)
        elif path == "/entry_image":
            self._send_image()
        else:
            self.send_error(404, "not found")

    def _send_image(self) -> None:
        provider = getattr(self.server, "masarasi_image", None)
        query = parse_qs(urlparse(self.path).query)
        try:
            image_id = int(query.get("id", [""])[0])
        except (ValueError, TypeError):
            self.send_error(400, "bad id")
            return
        rec = provider(image_id) if provider else None
        if not rec:
            self.send_error(404, "no image")
            return
        data, mime = rec
        self._send(bytes(data), mime or "image/jpeg")

    def _send(self, body: bytes, content_type: str, cache: bool = True) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class MapServer:
    """Lokaler HTTP-Server für die AIS-/Törn-Karte."""

    def __init__(
        self,
        own_provider: OwnProvider,
        targets_provider: TargetsProvider,
        track_provider: TrackProvider,
        entries_provider: Optional[EntriesProvider] = None,
        info_provider: Optional[InfoProvider] = None,
        image_provider: Optional[ImageProvider] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._own_provider = own_provider
        self._targets_provider = targets_provider
        self._track_provider = track_provider
        self._entries_provider = entries_provider
        self._info_provider = info_provider
        self._image_provider = image_provider
        self._host = host
        self._port = port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        httpd.daemon_threads = True
        # Datenfunktion an den Server hängen, damit der Handler drankommt.
        httpd.masarasi_data = self._data  # type: ignore[attr-defined]
        httpd.masarasi_image = self._image_provider  # type: ignore[attr-defined]
        self._httpd = httpd
        self._port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}/"

    # --- Datenaufbereitung --------------------------------------------------

    def _data(self) -> Dict:
        return {
            "own": self._own_provider(),
            "targets": self._targets_view(),
            "track": self._track_provider(),
            "entries": self._entries_provider() if self._entries_provider else [],
            "info": self._info_provider() if self._info_provider else "",
        }

    def _targets_view(self) -> List[Dict]:
        out: List[Dict] = []
        for rec in self._targets_provider():
            lat, lon = rec.get("lat"), rec.get("lon")
            if lat is None or lon is None:
                continue
            out.append({
                "mmsi": rec.get("mmsi"),
                "name": rec.get("name", ""),
                "lat": lat,
                "lon": lon,
                "cog": rec.get("cog"),
                "heading": rec.get("heading"),
                "sog": rec.get("sog"),
            })
        return out


# --- HTML-Seite (Leaflet + OpenFreeMap) ------------------------------------

_PAGE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>masarasi — AIS-Karte</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<style>
  html, body { height: 100%; margin: 0; }
  #map { position: absolute; inset: 0; background: #a5c9e0; }
  #info {
    position: absolute; top: 10px; right: 10px; z-index: 500;
    background: rgba(255,255,255,.9); padding: 6px 10px; border-radius: 6px;
    font: 13px/1.4 system-ui, sans-serif; box-shadow: 0 1px 4px rgba(0,0,0,.3);
  }
  .lbl { font: 11px system-ui, sans-serif; color: #063; white-space: nowrap;
         text-shadow: 0 0 2px #fff, 0 0 2px #fff; }
</style>
</head>
<body>
<div id="map"></div>
<div id="info">AIS-Ziele: <span id="cnt">0</span>
  <div id="note" style="color:#b25000"></div>
  <div style="margin-top:4px;font-size:11px">Logbuch:
    <span style="color:#e8820c">●</span> Auto
    <span style="color:#d61e3c">●</span> Manuell
    <span style="color:#9aa0a6">●</span> Import
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.22/leaflet-maplibre-gl.js"></script>
<script>
const map = L.map('map', { center: [43.5, 16.0], zoom: 9 });
// OpenFreeMap-Vektorkacheln als Leaflet-Ebene (falls verfügbar).
try {
  L.maplibreGL({ style: 'https://tiles.openfreemap.org/styles/liberty' }).addTo(map);
} catch (e) {
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    { attribution: '© OpenStreetMap' }).addTo(map);
}

const trackLayer   = L.layerGroup().addTo(map);
const entriesLayer = L.layerGroup().addTo(map);
const shipLayer    = L.layerGroup().addTo(map);
const markers = {};       // mmsi -> Leaflet-Marker
let ownMarker = null;
let didFit = false;
let entriesSig = null;    // Signatur, um Logbuch-Marker nur bei Aenderung neu zu bauen

L.control.layers(null, {
  'Logbuch': entriesLayer, 'Törn-Track': trackLayer, 'AIS-Ziele': shipLayer
}, { collapsed: false }).addTo(map);

// Pfeil-Icon: zeigt nach oben (Norden), per CSS um `deg` gedreht.
function arrow(deg, color, size) {
  const r = (deg == null) ? 0 : deg;
  const html =
    '<div style="transform:rotate(' + r + 'deg);width:' + size + 'px;height:' + size + 'px">' +
    '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24">' +
    '<polygon points="12,1 20,22 12,17 4,22" fill="' + color +
    '" stroke="#003" stroke-width="1.3"/></svg></div>';
  return L.divIcon({ html: html, className: '', iconSize: [size, size],
                     iconAnchor: [size/2, size/2] });
}
function dot(color, size) {
  const html = '<div style="width:' + size + 'px;height:' + size +
    'px;border-radius:50%;background:' + color + ';border:1.3px solid #003"></div>';
  return L.divIcon({ html: html, className: '', iconSize: [size, size],
                     iconAnchor: [size/2, size/2] });
}

function dir(t) {                       // Ausrichtung des Pfeils
  // Kurs über Grund (COG) zuerst — das entspricht der COG-Spalte im Plotter/
  // AIS-Empfänger. Heading (Bugrichtung) nur als Ausweichwert.
  if (t.cog != null) return t.cog;
  if (t.heading != null && t.heading < 360) return t.heading;
  return null;
}
function mtUrl(mmsi) {
  return 'https://www.marinetraffic.com/en/ais/details/ships/mmsi:' + mmsi;
}
function vfUrl(mmsi) {   // VesselFinder öffnet die Schiffsseite per MMSI (auch für Gäste)
  return 'https://www.vesselfinder.com/?mmsi=' + mmsi;
}
function label(t) {
  const name = t.name || ('MMSI ' + (t.mmsi || '?'));
  const sog = (t.sog != null) ? t.sog.toFixed(1) + ' kn' : '–';
  const cog = (t.cog != null) ? Math.round(t.cog) + '°' : '–';
  const hdg = (t.heading != null && t.heading < 360) ? Math.round(t.heading) + '°' : '–';
  let h = '<b>' + name + '</b><br>MMSI ' + (t.mmsi || '?') +
          '<br>SOG ' + sog +
          '<br>COG (Kurs) ' + cog +
          '<br>Heading (Bug) ' + hdg;
  if (t.mmsi) {
    h += '<br>Details: ' +
         '<a href="https://www.vesselfinder.com/?mmsi=' + t.mmsi +
         '" target="_blank" rel="noopener">VesselFinder ↗</a> · ' +
         '<a href="https://www.myshiptracking.com/vessels/mmsi-' + t.mmsi +
         '" target="_blank" rel="noopener">MyShipTracking ↗</a> · ' +
         '<a href="' + mtUrl(t.mmsi) +
         '" target="_blank" rel="noopener">MarineTraffic ↗</a>' +
         '<br><span style="color:#888;font-size:11px">Doppelklick öffnet ' +
         'VesselFinder (Schiffsseite direkt)</span>';
  }
  return h;
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function entryColors(t) {                  // Farbe je Eintragstyp
  if (t === 'manual')  return { stroke: '#7a0012', fill: '#d61e3c' };  // rot
  if (t === 'tripcon') return { stroke: '#333333', fill: '#9aa0a6' };  // grau (Import)
  return { stroke: '#7a3d00', fill: '#e8820c' };                       // orange (auto)
}
function entryPopup(e) {
  let h = '<b>' + esc(e.time) + '</b>';
  if (e.type) h += ' <span style="color:#888">(' + esc(e.type) + ')</span>';
  if (e.anlass) h += '<br>Anlass: ' + esc(e.anlass);
  h += '<br>Pos: ' + e.lat.toFixed(4) + ', ' + e.lon.toFixed(4);
  const l2 = [];
  if (e.sog != null) l2.push('SOG ' + e.sog + ' kn');
  if (e.depth != null) l2.push('Tiefe ' + e.depth + ' m');
  if (l2.length) h += '<br>' + l2.join('  ·  ');
  if (e.wind) h += '<br>Wind ' + esc(e.wind);
  if (e.motor) h += '<br>Motor: ' + esc(e.motor);
  if (e.sails) h += '<br>Segel: ' + esc(e.sails);
  if (e.note) h += '<br><i>' + esc(e.note) + '</i>';
  const imgs = e.images || [];
  if (imgs.length) {
    h += '<br><a href="/entry_image?id=' + imgs[0] + '" target="_blank">'
       + '<img src="/entry_image?id=' + imgs[0] + '" '
       + 'style="max-width:240px;max-height:200px;margin-top:6px;border-radius:4px">'
       + '</a>';
    if (imgs.length > 1) {
      const links = imgs.map(function (id, i) {
        return '<a href="/entry_image?id=' + id + '" target="_blank">Bild '
             + (i + 1) + '</a>';
      });
      h += '<br><span style="color:#888">' + imgs.length + ' Bilder: </span>'
         + links.join(' · ');
    }
  }
  return h;
}

async function refresh() {
  let d;
  try { d = await (await fetch('data.json', { cache: 'no-store' })).json(); }
  catch (e) { return; }

  // AIS-Ziele
  const seen = {};
  (d.targets || []).forEach(function (t) {
    seen[t.mmsi] = true;
    const deg = dir(t);
    const icon = (deg == null) ? dot('#159c3f', 12) : arrow(deg, '#159c3f', 26);
    let m = markers[t.mmsi];
    if (!m) {
      m = L.marker([t.lat, t.lon], { icon: icon }).addTo(shipLayer);
      m.bindPopup('');
      m.bindTooltip('', { permanent: true, direction: 'right',
                          offset: [10, 0], className: 'lbl' });
      // Doppelklick öffnet die Schiffsseite (VesselFinder per MMSI — öffnet
      // auch ohne Login direkt das Schiff; MarineTraffic leitet Gäste nur auf
      // die Live-Karte um). Weitere Tracker als Links im Popup.
      const mmsi = t.mmsi;
      m.on('dblclick', function (ev) {
        L.DomEvent.stopPropagation(ev);          // keine Karten-Zoomstufe
        if (mmsi) window.open(vfUrl(mmsi), '_blank', 'noopener');
      });
      markers[t.mmsi] = m;
    } else {
      m.setLatLng([t.lat, t.lon]); m.setIcon(icon);
    }
    m.setPopupContent(label(t));
    m.setTooltipContent(t.name || String(t.mmsi || ''));
  });
  // verschwundene Ziele entfernen
  Object.keys(markers).forEach(function (k) {
    if (!seen[k]) { shipLayer.removeLayer(markers[k]); delete markers[k]; }
  });
  document.getElementById('cnt').textContent = (d.targets || []).length;
  document.getElementById('note').textContent = d.info || '';

  // Eigenes Schiff
  if (d.own && d.own.lat != null) {
    const deg = (d.own.heading != null) ? d.own.heading : d.own.cog;
    const icon = arrow(deg, '#1560d6', 30);
    if (!ownMarker) {
      ownMarker = L.marker([d.own.lat, d.own.lon], { icon: icon, zIndexOffset: 1000 })
        .addTo(shipLayer).bindPopup('Eigenes Schiff');
    } else {
      ownMarker.setLatLng([d.own.lat, d.own.lon]); ownMarker.setIcon(icon);
    }
  }

  // Törn-Track
  trackLayer.clearLayers();
  const pts = (d.track && d.track.points) || [];
  if (pts.length > 1) {
    L.polyline(pts, { color: '#d6156a', weight: 3, opacity: .85 }).addTo(trackLayer);
  }

  // Logbuch-Einträge als anklickbare Punkte (nur bei Änderung neu bauen)
  const entries = d.entries || [];
  const sig = entries.length + '|' + (entries.length ? entries[entries.length-1].time : '');
  if (sig !== entriesSig) {
    entriesSig = sig;
    entriesLayer.clearLayers();
    entries.forEach(function (e) {
      const c = entryColors(e.type);
      L.circleMarker([e.lat, e.lon], {
        radius: 5, color: c.stroke, weight: 1.3,
        fillColor: c.fill, fillOpacity: .95
      }).addTo(entriesLayer)
        .bindPopup(entryPopup(e))
        .bindTooltip(e.time, { direction: 'top' });
    });
  }

  // Beim ersten Datensatz auf alles einpassen
  if (!didFit) {
    const all = pts.slice();
    (d.targets || []).forEach(function (t) { all.push([t.lat, t.lon]); });
    if (d.own && d.own.lat != null) all.push([d.own.lat, d.own.lon]);
    if (all.length) { map.fitBounds(L.latLngBounds(all).pad(0.2)); didFit = true; }
  }
}

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>
"""
