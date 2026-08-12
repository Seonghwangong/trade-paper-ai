"""ASGI entry point used only by Playwright authentication QA."""

import os
from pathlib import Path


data_dir = os.environ.get("TRADE_PAPER_DATA_DIR", "").strip()
if not data_dir:
    raise RuntimeError("TRADE_PAPER_DATA_DIR is required for browser QA.")
Path(data_dir).mkdir(parents=True, exist_ok=True)

from app.main import app  # noqa: E402,F401
