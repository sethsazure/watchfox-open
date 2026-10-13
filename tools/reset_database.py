#!/usr/bin/env python3
"""Empty a WatchFox PostgreSQL database before launch: removes every row in every table and keeps the tables themselves.

    DATABASE_URL='postgresql://...' python tools/reset_database.py          # only counts what is there (changes nothing)
    DATABASE_URL='postgresql://...' python tools/reset_database.py --wipe   # saves a backup file, asks you to type the server name, then deletes

The backup is a gzip file of every row (written next to where you run this, readable only by you). Azure also keeps its own
automatic restore points for 7 days. After a wipe, restart the app so nothing stays in memory:
    az webapp restart -g <resource-group> -n <webapp>
"""
import base64
import gzip
import json
import os
import stat
import sys
import time
from urllib.parse import urlparse

import psycopg


def main() -> int:
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgres"):
        print("Set DATABASE_URL to the PostgreSQL address first.")
        return 2
    wipe = "--wipe" in sys.argv
    host = (urlparse(url).hostname or "").split(".")[0]
    with psycopg.connect(url, connect_timeout=20) as c:
        tables = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1")]
        counts = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        print(f"Server: {host}\n")
        for t in tables:
            print(f"  {t:24} {counts[t]:>8}")
        users = c.execute("SELECT email FROM users ORDER BY created").fetchall() if "users" in tables else []
        if "orgs" in tables:
            month = time.strftime("%Y-%m", time.gmtime())
            print("\nOrganizations (tier, who is in it):")
            for o in c.execute("""SELECT o.id, o.name, o.plan, o.billing, o.billing_status, o.billing_next, COALESCE(u.runs, 0),
                                         (o.helcim_subscription IS NOT NULL), to_char(to_timestamp(o.created), 'YYYY-MM-DD')
                                  FROM orgs o LEFT JOIN usage u ON u.workspace_id = o.id AND u.month = %s ORDER BY o.created""", (month,)).fetchall():
                oid, name, plan, billing, status, nxt, runs, sub, created = o
                members = [f"{e} ({r})" for e, r in c.execute("SELECT u.email, m.role FROM members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = %s ORDER BY m.created", (oid,)).fetchall()]
                print(f"  {name}\n    tier: {plan} ({billing}){'  subscription: ' + str(status or 'active') + ', next ' + str(nxt) if sub else ''}   runs this month: {runs}   created {created}\n    people: {', '.join(members)}")
        print(f"\n{len(users)} account(s) in total")
        if not wipe:
            print("\nNothing was changed. Add --wipe to delete all of this.")
            return 0
        if input(f'\nThis deletes EVERY row above. Type the server name ("{host}") to continue: ').strip() != host:
            print("Not confirmed. Nothing was changed.")
            return 1
        name = f"watchfox-backup-{host}-{time.strftime('%Y%m%d-%H%M%S')}.json.gz"
        with gzip.open(name, "wt", encoding="utf-8") as f:
            for t in tables:
                cur = c.execute(f'SELECT * FROM "{t}"')
                cols = [d.name for d in cur.description]
                for row in cur.fetchall():
                    f.write(json.dumps({"table": t, "row": {k: (base64.b64encode(v).decode() if isinstance(v, (bytes, memoryview)) else v) for k, v in zip(cols, row)}}, default=str) + "\n")
        os.chmod(name, stat.S_IRUSR | stat.S_IWUSR)
        print(f"Backup saved: {name}")
        c.execute("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
        c.commit()
        left = sum(c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)
        print(f"Done. Rows left in all tables: {left}")
        print("Now restart the app:  az webapp restart -g <resource-group> -n <webapp>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
