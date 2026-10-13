"""Run the whole suite against PostgreSQL instead of SQLite:
    docker run -d --name wf-test-pg -e POSTGRES_PASSWORD=testpw -p 55432:5432 postgres:16-alpine
    WATCHFOX_TEST_PG=postgresql://postgres:testpw@localhost:55432 pytest
Each run gets its own fresh database."""
import os
import uuid

_base = os.getenv("WATCHFOX_TEST_PG", "").rstrip("/")
if _base:
    import psycopg

    _name = "wf_" + uuid.uuid4().hex[:8]
    with psycopg.connect(_base + "/postgres", autocommit=True) as _c:
        _c.execute(f"CREATE DATABASE {_name}")
    os.environ["DATABASE_URL"] = f"{_base}/{_name}"
    os.environ.setdefault("WATCHFOX_SECRET", "test-only-secret")      # hosted installs must set one

os.environ.setdefault("WATCHFOX_PASSWORD_LOGIN", "1")      # the suite signs in with passwords; production uses sign-in providers only
os.environ.setdefault("WATCHFOX_NEW_ACCOUNTS_PER_IP_DAY", "100000")
os.environ.setdefault("WATCHFOX_NEW_ACCOUNTS_PER_HOUR", "100000")
os.environ.setdefault("WATCHFOX_SIGNUPS_PER_HOUR", "1000")
