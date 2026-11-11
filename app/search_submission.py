"""Public IndexNow ownership token; not an account/API authentication secret."""
from pathlib import Path

KEY_FILE = Path(__file__).parent / "static" / "indexnow-key.txt"
INDEXNOW_KEY = KEY_FILE.read_text(encoding="utf-8").strip()
INDEXNOW_PATH = "/" + INDEXNOW_KEY + ".txt"
