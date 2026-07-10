#!/usr/bin/env python3
"""masarasi — NMEA-/GoFree-Datenquellen im Netz suchen (Starter ohne PYTHONPATH).

Ruft nur `masarasi.discover` auf, damit man es direkt starten kann:

    # GoFree-Ankündigungen abhören (an Bord, GoFree am MFD aktiv)
    python find_sources.py --gofree --iface 192.168.9.50 --raw --seconds 20

    # einen Host auf offene NMEA-Ports prüfen
    python find_sources.py 192.168.9.224

    # alle offenen Ports eines Geräts finden
    python find_sources.py 192.168.9.100 --sweep

Die lokale IP (--iface) ist die Adresse DEINES Laptops im jeweiligen Netz
(mit `ipconfig` ablesen), nicht die des Plotters.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from masarasi.discover import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
