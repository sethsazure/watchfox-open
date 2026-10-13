"""Copy everything from a SQLite WatchFox database into a PostgreSQL one (used once, when moving to production).

    watchfox copy-db --from ~/.watchfox/watchfox.db --to "postgresql://user:pass@host/watchfox?sslmode=require"

The encrypted mailbox credentials and webhook secrets only open with the same WATCHFOX_SECRET, so set the new server's
WATCHFOX_SECRET to the one this machine used (the file ~/.watchfox/secret), or start production with fresh accounts."""
import sqlite3
from typing import Dict, List

TABLES = ["users", "orgs", "members", "api_keys", "connections", "watches", "items", "deliveries", "subscriptions",
          "sandbox", "invites", "usage", "identities", "plan_requests", "types", "feedback"]


def copy(sqlite_path: str, pg_url: str, batch: int = 500) -> Dict[str, int]:
    import os

    os.environ["DATABASE_URL"] = pg_url
    from . import config

    config.DATABASE_URL = pg_url
    from . import store

    store.PG = True
    store._pool = None
    store.conn()                                            # creates the tables if they are missing
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    copied: Dict[str, int] = {}
    with store.conn().connection() as pg:
        for table in TABLES:
            if table not in have:
                continue
            cols: List[str] = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
            target = {r["column_name"] for r in pg.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,)).fetchall()}
            use = [c for c in cols if c in target]
            sql = f"INSERT INTO {table} ({', '.join(use)}) VALUES ({', '.join(['%s'] * len(use))}) ON CONFLICT DO NOTHING"
            n, rows = 0, src.execute(f"SELECT {', '.join(use)} FROM {table}")
            while True:
                chunk = rows.fetchmany(batch)
                if not chunk:
                    break
                with pg.cursor() as cur:
                    cur.executemany(sql, [tuple(store.clean(v) for v in r) for r in chunk])
                n += len(chunk)
            copied[table] = n
    src.close()
    return copied
