#!/usr/bin/env python3
"""SailLog — Segel-Logbuch. Bequemer Start ohne Installation.

    python main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from saillog.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
