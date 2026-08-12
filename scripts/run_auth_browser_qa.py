from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parent.parent
raise SystemExit(
    subprocess.call(
        [sys.executable, "-m", "pytest", "-q", "tests/test_auth_browser.py"],
        cwd=root,
    )
)
