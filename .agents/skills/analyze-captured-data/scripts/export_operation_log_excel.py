# -*- coding: utf-8 -*-
"""Run the project's operation-log exporter from the analysis skill."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROJECT_SCRIPT = PROJECT_ROOT / "app" / "excel" / "export_operation_log_excel.py"

if not PROJECT_SCRIPT.is_file():
    raise SystemExit(f"Project exporter not found: {PROJECT_SCRIPT}")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

runpy.run_path(str(PROJECT_SCRIPT), run_name="__main__")
