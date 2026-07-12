# Orca Core — Untersuchungsstand (zum späteren Weitermachen)

Ziel: **Daten aus dem Orca Core** (`192.168.9.100`) in saillog holen.
B&G bleibt die Hauptquelle; der Orca wird *nicht* ersetzt. Stand: geparkt,
aber gut vorbereitet — die Diagnosewerkzeuge sind fertig, wir wissen genau,
wo die Daten liegen und was der nächste Schritt wäre.

Werkzeug für alles unten: **`orca_probe.py`** (im Repo-Root).

## Diensteverzeichnis (bestätigt)

| Port | Dienst | Antwort | Nutzen |
|---|---|---|---|
| **8080** | JSON-API (C++/eigener Server) | `/` → Version; `/info` → Netz inkl. **`can0` @ 250 kBit/s** (NMEA-2000-CAN-Bus) | nur Verwaltung |
| **8085** | ORCA Watchdog | Versionstext | nur Status |
| **8090** | Mongoose-Webserver | **SWUpdate** — Firmware-Upload-Portal (Drag & Drop) | keine Daten |
| **9001** | **Flask** (Werkzeug/Python 3.13) | `/status` → `{"aligned":1,"calibrated":1,"calibration_active":0,"calibration_enabled":0,"heading_offset":178}` | **IMU-/Kompass-Dienst** (nur Status, keine Live-Daten) |
| **9000** | **WebSocket** | nach Connect: `{"event":"imuBegin"}`, danach sporadisch **Binärframes** | **einziger Live-Datenpfad** |

Nicht-WebSocket-Ports 8089/9089: kein WS-Upgrade.

## Was wir gelernt haben

- **REST-APIs liefern KEINE Live-Daten.** 87 gängige Pfade (`/nav`, `/gps`,
  `/imu`, `/attitude`, `/heading`, `/nmea`, `/n2k`, `/data`, `/api/*` …) auf
  8080/9001/8085 durchprobiert → nur `/` (Version), `/info` (Netz/can0) und
  `9001/status` (IMU-Kalibrierung) antworten mit 200. Alles andere 404.
- **Live-Daten laufen nur über das WebSocket 9000** — und zwar **binär**
  (nicht JSON). Die einzige Textnachricht ist `{"event":"imuBegin"}`.
- Der Orca schickt **WebSocket-Ping-Frames**; ohne **Pong** kappt er die
  Verbindung nach Sekunden („keepalive ping timeout"). Das ist im Prober
  jetzt behoben (Ping→Pong), die Verbindung bleibt offen.
- Trotz offener Verbindung kam über 30 s **nur `imuBegin`** und (in einem
  frühen Lauf) **ein** Binärframe. Der Datenstrom fließt also **nicht von
  selbst** los — oder nur ereignisgesteuert.

## Werkzeug-Kommandos (orca_probe.py)

```bash
python orca_probe.py 192.168.9.100            # HTTP-Ports + WS kurz anschauen
python orca_probe.py 192.168.9.100 --api      # 87 REST-Pfade durchprobieren
python orca_probe.py 192.168.9.100 --fetch 8090//   # einen Pfad holen (PORT//pfad)
python orca_probe.py 192.168.9.100 --deep     # WS: subscribe-Versuche + lauschen
python orca_probe.py 192.168.9.100 --listen --seconds 60   # WS: NUR lauschen (kein subscribe)
```

Der Prober kann inzwischen: HTTP-GET (bis 64 kB), API-Pfad-Scan, echtes
WebSocket-Framing mit **Opcodes** (`_ws_frame` → (opcode, payload)),
**Ping→Pong-Keepalive**, Text-als-Text und **Binär-als-Hex**, plus
Zusammenfassung (Text-Ereignisse, Binär-Framegrößen).

## Nächste Schritte (wenn wir weitermachen)

1. **`--listen --seconds 60` laufen lassen, dabei das Boot bewegen** (Lage
   ändern). Kommen dann viele Binärframes → der Orca streamt IMU von selbst,
   und unsere subscribe-Versuche haben ihn evtl. nur gestört. Dann:
   Binärformat aus Hex + Bewegung ableiten (Heading/Krängung/Trimm/RoT).
2. **Kommt weiter nichts:** Der Stream braucht das App-Kommando. Definitiver
   Weg → **Traffic der Orca-Display-App (Tablet) ↔ Core mitschneiden**, z. B.
   mit **PCAPdroid** (Android, ohne Root). `ws://…:9000` ist unverschlüsselt,
   also sind die Frames (inkl. des echten `subscribe`) im PCAP lesbar. Daraus
   das Kommando + Format übernehmen.
3. Danach Integration wie bei B&G/Maretron: eigene Quelle, die den Orca
   pollt/streamt und Heading/Lage in den `LiveData`-Datensatz einspeist.

## Kosten/Nutzen-Einordnung (bewusst dokumentiert)

Der Kern-Mehrwert des Orca ist **IMU: Heading/Lage/Rate-of-Turn**.
**Krängung, Trimm und Ruderlage liefert B&G bereits** (XDR), ebenso Heading
(HDG/VHW). Der Zusatznutzen des Orca ist damit gering (evtl. präziseres/
schnelleres Heading, Rate-of-Turn), der Aufwand (App-Traffic mitschneiden +
Binärformat entschlüsseln) hoch. Deshalb **geparkt** — die Vorarbeit ist
gesichert, das Weitermachen ist jederzeit möglich.

## GoFree / B&G-Vertiefung — bewusst nicht weiterverfolgt

Der Nutzer will B&G/GoFree **nicht** weiter vertiefen (mittelfristig bleibt
B&G, wird nicht ersetzt). Die GoFree-Discovery (Multicast `239.2.1.1:2052`)
bleibt als „Gerät finden" nützlich (`discover.py --gofree`, siehe
STATUS.md), aber der GoFree-Datenpfad `navico-nav-ws:2053` wird **nicht**
erschlossen — die NMEA-Daten kommen über `TCP 192.168.9.224:10110` (bereits
genutzt) und Maretron. `gofree_probe.py` bleibt für eine spätere Neubewertung
im Repo.
