"""NMEA0183-Parser für masarasi.

Ein WLAN/LAN-Gateway (z.B. Yacht Devices YDWG-02, Actisense W2K-1,
Digital Yacht iKonvert) wandelt die NMEA2000-Daten vom Bus in
NMEA0183-Sätze um und sendet sie über TCP/UDP. Dieses Modul zerlegt
diese Sätze in normalisierte Messwerte.

Verwendung:
    parser = NmeaParser()
    values = parser.parse("$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A")
    # -> {"lat": 48.1173, "lon": 11.5167, "sog_kn": 22.4, "cog_deg": 84.4, ...}

Alle Winkel in Grad, Geschwindigkeiten in Knoten, Tiefe in Metern,
Temperaturen in Grad Celsius.
"""

from __future__ import annotations

from typing import Dict, Optional

# --- Kanonische Messwert-Schlüssel ------------------------------------------

LAT = "lat"                 # Breitengrad in Dezimalgrad (N positiv)
LON = "lon"                 # Längengrad in Dezimalgrad (E positiv)
SOG = "sog_kn"              # Speed over Ground (Knoten)
COG = "cog_deg"            # Course over Ground, rechtweisend (Grad)
STW = "stw_kn"              # Speed through Water (Knoten)
HDG_TRUE = "hdg_true_deg"   # Steuerkurs rechtweisend (Grad)
HDG_MAG = "hdg_mag_deg"     # Steuerkurs missweisend (Grad)
AWS = "aws_kn"              # Apparent Wind Speed / scheinbarer Wind (Knoten)
AWA = "awa_deg"            # Apparent Wind Angle relativ zum Bug (0-359 Grad)
TWS = "tws_kn"              # True Wind Speed / wahrer Wind (Knoten)
TWD = "twd_deg"            # True Wind Direction, rechtweisend (Grad)
TWA = "twa_deg"            # True Wind Angle relativ zum Bug (0-359 Grad)
DEPTH = "depth_m"          # Wassertiefe (Meter)
WATER_TEMP = "water_temp_c"  # Wassertemperatur (Grad Celsius)
ENGINE_RPM = "engine_rpm"   # Motordrehzahl (U/min) — für Motor-Erkennung
OIL_PRESSURE = "oil_pressure_bar"  # Öldruck (bar) — für Motor-Erkennung
ENGINE_HOURS = "engine_hours"  # Motorbetriebsstunden (h)
ENGINE_TEMP = "engine_temp_c"  # Motor-/Kühlwassertemperatur (°C)
ALT_VOLTAGE = "alternator_v"  # Lichtmaschinen-/Bordspannung (V) — Motor an/aus
LOG_TOTAL = "log_total_nm"   # Logstand / Gesamtdistanz (Nm)
AIR_TEMP = "air_temp_c"      # Lufttemperatur (°C)
BARO = "baro_mbar"           # Luftdruck (mbar)
HEEL = "heel_deg"            # Krängung (Grad, + = Steuerbord)
TRIM = "trim_deg"            # Trimm/Längsneigung (Grad)
RUDDER = "rudder_deg"        # Ruderlage (Grad)
UTC_TIME = "utc_time"       # Uhrzeit UTC als "hhmmss"

# Reihenfolge & Anzeigenamen für die GUI
FIELD_LABELS = [
    (LAT, "Breite", "°"),
    (LON, "Länge", "°"),
    (SOG, "SOG", "kn"),
    (COG, "COG", "°"),
    (STW, "Fahrt d. Wasser", "kn"),
    (HDG_TRUE, "Kurs (rw)", "°"),
    (HDG_MAG, "Kurs (mw)", "°"),
    (AWS, "Wind scheinbar", "kn"),
    (AWA, "Windwinkel", "°"),
    (TWS, "Wind wahr", "kn"),
    (TWD, "Windrichtung", "°"),
    (DEPTH, "Tiefe", "m"),
    (WATER_TEMP, "Wassertemp.", "°C"),
    (AIR_TEMP, "Lufttemperatur", "°C"),
    (BARO, "Luftdruck", "mbar"),
    (HEEL, "Krängung", "°"),
    (LOG_TOTAL, "Gesamtlog", "NM"),
    (ENGINE_RPM, "Motor-Drehzahl", "U/min"),
    (ENGINE_TEMP, "Motortemperatur", "°C"),
    (ALT_VOLTAGE, "Lichtmaschine", "V"),
    (ENGINE_HOURS, "Motorstunden", "h"),
]


# --- Einheiten-Umrechnung ---------------------------------------------------

def _speed_to_knots(value: float, unit: str) -> float:
    """Rechnet eine Geschwindigkeit in Knoten um.

    unit: N=Knoten, K=km/h, M=m/s, S=mph.
    """
    unit = (unit or "N").upper()
    if unit == "N":
        return value
    if unit == "K":
        return value / 1.852
    if unit == "M":
        return value * 1.943844
    if unit == "S":
        return value * 0.868976
    return value


def _to_float(text: str) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _coord(value: str, hemisphere: str) -> Optional[float]:
    """Wandelt NMEA-Koordinate (dddmm.mmmm) in Dezimalgrad um."""
    if not value:
        return None
    dot = value.find(".")
    if dot < 3:  # mindestens 1 Grad-Ziffer + 2 Minuten-Ziffern
        return None
    try:
        degrees = int(value[: dot - 2])
        minutes = float(value[dot - 2 :])
    except ValueError:
        return None
    decimal = degrees + minutes / 60.0
    if (hemisphere or "").upper() in ("S", "W"):
        decimal = -decimal
    return decimal


# --- Prüfsumme --------------------------------------------------------------

def valid_checksum(sentence: str) -> bool:
    """Prüft die NMEA-XOR-Prüfsumme. Fehlt sie, gilt der Satz als gültig."""
    star = sentence.rfind("*")
    if star == -1:
        return True  # keine Prüfsumme vorhanden -> tolerieren
    body = sentence[1:star]  # ohne führendes '$'/'!'
    given = sentence[star + 1 : star + 3]
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    try:
        return checksum == int(given, 16)
    except ValueError:
        return False


# --- Parser -----------------------------------------------------------------

class NmeaParser:
    """Zerlegt NMEA0183-Sätze in normalisierte Messwerte."""

    def parse(self, sentence: str) -> Dict[str, float]:
        """Parst einen einzelnen Satz.

        Gibt ein Dict der erkannten Messwerte zurück (leer, wenn der Satz
        ungültig oder unbekannt ist).
        """
        if not sentence:
            return {}
        sentence = sentence.strip()
        if not sentence or sentence[0] not in "$!":
            return {}
        if not valid_checksum(sentence):
            return {}

        star = sentence.rfind("*")
        body = sentence[1:star] if star != -1 else sentence[1:]
        fields = body.split(",")
        address = fields[0]
        if len(address) < 5:
            return {}
        sentence_type = address[-3:]

        handler = _HANDLERS.get(sentence_type)
        if handler is None:
            return {}
        try:
            result = handler(fields)
        except (IndexError, ValueError):
            return {}
        # None-Werte entfernen
        return {key: value for key, value in result.items() if value is not None}


# --- Satz-Handler -----------------------------------------------------------
# Jeder Handler bekommt die kommagetrennten Felder (fields[0] = Adresse).

def _rmc(f):
    return {
        UTC_TIME: f[1] or None,
        LAT: _coord(f[3], f[4]),
        LON: _coord(f[5], f[6]),
        SOG: _to_float(f[7]),
        COG: _to_float(f[8]),
    }


def _gga(f):
    return {
        UTC_TIME: f[1] or None,
        LAT: _coord(f[2], f[3]),
        LON: _coord(f[4], f[5]),
    }


def _gll(f):
    return {
        LAT: _coord(f[1], f[2]),
        LON: _coord(f[3], f[4]),
        UTC_TIME: (f[5] or None) if len(f) > 5 else None,
    }


def _vtg(f):
    return {
        COG: _to_float(f[1]),  # rechtweisend
        SOG: _to_float(f[5]),  # Knoten (Feld 5, Einheit N in Feld 6)
    }


def _mwv(f):
    # f[1]=Winkel, f[2]=Referenz (R=scheinbar/relativ, T=wahr), f[3]=Speed,
    # f[4]=Einheit, f[5]=Status A
    if len(f) > 5 and f[5] and f[5].upper() != "A":
        return {}
    angle = _to_float(f[1])
    reference = (f[2] or "").upper()
    speed = _to_float(f[3])
    if speed is not None:
        speed = _speed_to_knots(speed, f[4] if len(f) > 4 else "N")
    if reference == "T":
        return {TWA: angle, TWS: speed}
    return {AWA: angle, AWS: speed}


def _mwd(f):
    # f[1]=Windrichtung rw, f[2]=T, f[5]=Speed kn, f[6]=N, f[7]=Speed m/s
    twd = _to_float(f[1])
    tws = _to_float(f[5]) if len(f) > 5 else None
    if tws is None and len(f) > 7:
        ms = _to_float(f[7])
        tws = _speed_to_knots(ms, "M") if ms is not None else None
    return {TWD: twd, TWS: tws}


def _dpt(f):
    depth = _to_float(f[1])
    offset = _to_float(f[2]) if len(f) > 2 else None
    if depth is not None and offset is not None:
        depth += offset
    return {DEPTH: depth}


def _dbt(f):
    # f[3] = Tiefe in Metern (Einheit M in f[4])
    return {DEPTH: _to_float(f[3]) if len(f) > 3 else None}


def _mtw(f):
    return {WATER_TEMP: _to_float(f[1])}


def _hdg(f):
    # f[1]=missweisend, f[4]=Variation, f[5]=E/W
    mag = _to_float(f[1])
    result = {HDG_MAG: mag}
    variation = _to_float(f[4]) if len(f) > 4 else None
    if mag is not None and variation is not None and len(f) > 5:
        if (f[5] or "").upper() == "W":
            variation = -variation
        result[HDG_TRUE] = (mag + variation) % 360.0
    return result


def _hdt(f):
    return {HDG_TRUE: _to_float(f[1])}


def _hdm(f):
    return {HDG_MAG: _to_float(f[1])}


def _vhw(f):
    # f[1]=Kurs rw, f[3]=Kurs mw, f[5]=Fahrt Knoten (f[6]=N)
    return {
        HDG_TRUE: _to_float(f[1]),
        HDG_MAG: _to_float(f[3]),
        STW: _to_float(f[5]),
    }


def _vlw(f):
    # $--VLW,wasser_gesamt,N,wasser_reset,N,grund_gesamt,N,grund_reset,N
    # Manche Geräte (z.B. B&G) lassen die Wasser-Gesamtdistanz leer -> dann
    # die Grund-Gesamtdistanz (Feld 5) als Logstand nehmen.
    total_water = _to_float(f[1]) if len(f) > 1 else None
    total_ground = _to_float(f[5]) if len(f) > 5 else None
    value = total_water if total_water is not None else total_ground
    return {LOG_TOTAL: value}


def _rpm(f):
    # $--RPM,quelle,nummer,drehzahl,steigung,status  (S=Welle, E=Motor)
    if len(f) > 5 and f[5] and f[5].upper() != "A":
        return {}
    rpm = _to_float(f[3]) if len(f) > 3 else None
    source = (f[1] or "").upper() if len(f) > 1 else ""
    if rpm is None or source not in ("E", "S", ""):
        return {}
    return {ENGINE_RPM: rpm}


_ENGINE_HINTS = ("ENG", "MOTOR", "COOL", "EXH", "CYL")
_ALT_HINTS = ("ALT", "CHARGE", "CHRG", "ENG")


def _xdr(f):
    # $--XDR,typ,wert,einheit,id, typ,wert,einheit,id, …  (Gruppen zu 4)
    result = {}
    groups = f[1:]
    for i in range(0, len(groups) - 3, 4):
        ttype = (groups[i] or "").upper()
        value = _to_float(groups[i + 1])
        units = (groups[i + 2] or "").upper()
        tid = (groups[i + 3] or "").upper()
        if value is None:
            continue
        if ttype == "C":  # Temperatur
            if "AIR" in tid:
                result[AIR_TEMP] = value
            elif any(k in tid for k in _ENGINE_HINTS):
                result[ENGINE_TEMP] = value
        elif ttype == "P":  # Druck
            if "BARO" in tid or "ATMOS" in tid:
                # B = bar -> mbar (×1000); P = Pascal -> mbar (÷100)
                result[BARO] = value / 100.0 if units == "P" else value * 1000.0
            elif "OIL" in tid or "ENG" in tid:
                if units == "P":  # Pascal -> bar
                    value = value / 100000.0
                result[OIL_PRESSURE] = value
        elif ttype == "A":  # Winkel
            if "HEEL" in tid:
                result[HEEL] = value
            elif "TRIM" in tid:
                result[TRIM] = value
            elif "RUDD" in tid:
                result[RUDDER] = value
        elif ttype == "T":  # Tachometer -> Motordrehzahl
            result[ENGINE_RPM] = value
        elif ttype == "U":  # Spannung -> Lichtmaschine/Bordspannung
            if any(k in tid for k in _ALT_HINTS) or ALT_VOLTAGE not in result:
                result[ALT_VOLTAGE] = value
        elif ttype == "G":  # generischer Wert -> Motorbetriebsstunden
            if units == "H" or "HOUR" in tid or "HRS" in tid or "STUND" in tid:
                result[ENGINE_HOURS] = value
    return result


def _pmarepd(f):
    # Maretron proprietär: $PMAREPD,instanz,oeldruck,oeltemp,kuehlwassertemp,
    # spannung,verbrauch,motorstunden,kuehldruck,kraftstoffdruck,…,status
    # (Feldreihenfolge folgt NMEA2000-PGN 127489)
    if not f or not f[0].startswith("PMAR"):
        return {}
    result = {}
    temp = _to_float(f[4]) if len(f) > 4 else None
    volt = _to_float(f[5]) if len(f) > 5 else None
    hours = _to_float(f[7]) if len(f) > 7 else None
    oil = _to_float(f[2]) if len(f) > 2 else None
    if temp is not None:
        result[ENGINE_TEMP] = temp
    if volt is not None:
        result[ALT_VOLTAGE] = volt
    if hours is not None:
        result[ENGINE_HOURS] = hours
    if oil is not None:            # Öldruck (kPa) -> bar; oft leer (kein Sensor)
        result[OIL_PRESSURE] = oil / 100.0
    return result


_HANDLERS = {
    "RMC": _rmc,
    "GGA": _gga,
    "GLL": _gll,
    "VTG": _vtg,
    "MWV": _mwv,
    "MWD": _mwd,
    "DPT": _dpt,
    "DBT": _dbt,
    "MTW": _mtw,
    "HDG": _hdg,
    "HDT": _hdt,
    "HDM": _hdm,
    "VHW": _vhw,
    "VLW": _vlw,
    "RPM": _rpm,
    "XDR": _xdr,
    "EPD": _pmarepd,   # Maretron $PMAREPD (Motorparameter)
}


# Schwelle: über dieser Bordspannung lädt die Lichtmaschine -> Motor läuft
ENGINE_VOLTAGE_THRESHOLD = 13.0


def engine_running(snapshot: dict) -> Optional[int]:
    """Leitet Motor ein/aus aus den Live-Werten ab.

    Gibt 1 (läuft), 0 (aus) oder None (keine Motordaten) zurück.
    Reihenfolge: Drehzahl (direktestes Signal) > Lichtmaschinen-/Bordspannung
    (≥ ~13 V, Lichtmaschine lädt nur bei laufendem Motor) > Öldruck.
    """
    rpm = snapshot.get(ENGINE_RPM)
    if rpm is not None:
        return 1 if rpm > 0 else 0
    volts = snapshot.get(ALT_VOLTAGE)
    if volts is not None:
        return 1 if volts >= ENGINE_VOLTAGE_THRESHOLD else 0
    oil = snapshot.get(OIL_PRESSURE)
    if oil is not None:
        return 1 if oil > 0 else 0
    return None
