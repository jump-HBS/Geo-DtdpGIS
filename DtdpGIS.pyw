"""Double-click launcher for DtdpGIS."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dtdpgis.gui.main import run_app

raise SystemExit(run_app())
