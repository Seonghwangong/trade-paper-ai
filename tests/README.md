# Developer verification

Run from the project root:

```bash
python3 -m pip install pytest
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q app tests
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -c "import app.main as m; print(m.audit_route_registrations(m.app))"
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_application.py
```

The session-scoped `real_data_write_guard` hashes every real JSON file and verifies that tests create no real backup or temporary artifacts. Mutation tests must use `temporary_data` or another `tmp_path` fixture.
