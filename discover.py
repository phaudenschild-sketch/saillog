#!/usr/bin/env python3
"""saillog — Quellen-Scanner. Bequemer Start ohne Installation.

    python discover.py 192.168.9.113 --full
    python discover.py --udp
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from saillog.discover import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
