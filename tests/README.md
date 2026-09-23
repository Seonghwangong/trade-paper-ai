# Developer verification

Run from the project root:

```bash
python3 -m pip install pytest
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q app tests
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -m "not browser"
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -c "import app.main as m; print(m.audit_route_registrations(m.app))"
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_application.py
```

Authentication browser QA starts and stops its own temporary-storage server:

```bash
venv/bin/python scripts/run_auth_browser_qa.py
```

Install browser binaries once with `venv/bin/python -m playwright install chromium webkit`.

The session-scoped `real_data_write_guard` hashes every real JSON file and verifies that tests create no real backup or temporary artifacts. Mutation tests must use `temporary_data` or another `tmp_path` fixture.

## Separating browser checks

Ordinary validation must use `-m "not browser"`. Do not use `-k "not browser"`: that filters test names and misses browser tests whose names do not contain the word.

Use `venv/bin/python -m pytest --collect-only -q -m browser` to list browser checks without launching a browser. On a machine where Playwright browsers work, explicitly run `venv/bin/python -m pytest -q -m browser`. The default full pytest command still includes every test; no tests are silently skipped.

If a Playwright process crashes on macOS, stop retrying that suite. Validate the affected screens through the connected browser and record that this does not replace the complete automated browser suite.
