"""Pytest setup shared by the whole suite.

flight_service calls fleet_store.init_db() at import, which opens (and creates)
the SQLite file at FLEET_DB_PATH. Redirect that to a temp location before any
test imports the app, so importing it never drops a fleet.db into the repo.
Per-test isolation is handled by an autouse fixture in test_flight_service.py.
"""

import os
import tempfile

os.environ.setdefault("FLEET_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="fleet_test_"), "fleet.db"))
