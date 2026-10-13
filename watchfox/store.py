import json
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from . import config, vault
from .config import DB_PATH

# One code base, two databases: PostgreSQL when DATABASE_URL is set (production), a local SQLite file otherwise (development, tests).
PG = config.DATABASE_URL.startswith(("postgres://", "postgresql://"))

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_pool = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE, pw_hash TEXT, created REAL);
CREATE TABLE IF NOT EXISTS orgs (id TEXT PRIMARY KEY, name TEXT, plan TEXT DEFAULT 'kit', billing TEXT DEFAULT 'monthly', created REAL);
CREATE TABLE IF NOT EXISTS billing_sessions (token_hash TEXT PRIMARY KEY, workspace_id TEXT, secret TEXT, plan TEXT, billing TEXT, kind TEXT, customer_code TEXT, created REAL);
CREATE TABLE IF NOT EXISTS members (user_id TEXT PRIMARY KEY, workspace_id TEXT, role TEXT, created REAL);
CREATE INDEX IF NOT EXISTS idx_members_ws ON members(workspace_id);
CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, workspace_id TEXT, email TEXT, token_hash TEXT UNIQUE, invited_by TEXT, created REAL, expires REAL, accepted REAL);
CREATE TABLE IF NOT EXISTS admin_audit (id TEXT PRIMARY KEY, at REAL, admin TEXT, action TEXT, target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS signups (ip TEXT, created REAL);
CREATE INDEX IF NOT EXISTS idx_signups ON signups(ip, created);
CREATE TABLE IF NOT EXISTS mailbox_runs (email_hash TEXT, month TEXT, runs INTEGER DEFAULT 0, PRIMARY KEY (email_hash, month));
CREATE TABLE IF NOT EXISTS usage (workspace_id TEXT, month TEXT, runs INTEGER DEFAULT 0, PRIMARY KEY (workspace_id, month));
CREATE TABLE IF NOT EXISTS identities (provider TEXT, subject TEXT, user_id TEXT, PRIMARY KEY (provider, subject));
CREATE TABLE IF NOT EXISTS plan_requests (id TEXT PRIMARY KEY, workspace_id TEXT, plan TEXT, billing TEXT, email TEXT, created REAL);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, prefix TEXT, key_hash TEXT UNIQUE,
  created REAL, last_used REAL, revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS connections (
  id TEXT PRIMARY KEY, user_id TEXT, provider TEXT, email TEXT, secret TEXT, created REAL,
  UNIQUE (user_id, provider, email)
);
CREATE TABLE IF NOT EXISTS watches (
  id TEXT PRIMARY KEY, user_id TEXT, connection_id TEXT, what TEXT, webhook_url TEXT, webhook_secret TEXT,
  backfill_days INTEGER DEFAULT 0, created REAL, last_run REAL, last_error TEXT
);
CREATE TABLE IF NOT EXISTS items (
  key TEXT PRIMARY KEY, user_id TEXT, watch_id TEXT, item_id TEXT, title TEXT, sender TEXT, snippet TEXT,
  received REAL, attachments TEXT, results TEXT, matched INTEGER, processed REAL
);
CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id, processed DESC);
CREATE INDEX IF NOT EXISTS idx_items_processed ON items(processed);
CREATE INDEX IF NOT EXISTS idx_items_watch ON items(watch_id, processed DESC);
CREATE TABLE IF NOT EXISTS deliveries (
  id TEXT PRIMARY KEY, watch_id TEXT, event_id TEXT, url TEXT, status TEXT, code INTEGER, attempts INTEGER,
  error TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS types (id TEXT PRIMARY KEY, user_id TEXT, label TEXT, description TEXT, spec TEXT, created REAL);
CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, user_id TEXT, what_id TEXT, item_key TEXT, kind TEXT, subject TEXT, body TEXT, created REAL);
CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY, connection_id TEXT UNIQUE, client_state TEXT, expires REAL, created REAL, last_renewed REAL
);
CREATE TABLE IF NOT EXISTS sandbox (
  id TEXT PRIMARY KEY, connection_id TEXT, sender TEXT, subject TEXT, body TEXT, received REAL
);
"""


MIGRATIONS = [
    ("connections", "metadata", "TEXT DEFAULT '{}'"), ("connections", "status", "TEXT DEFAULT 'active'"),
    ("connections", "status_reason", "TEXT"), ("connections", "status_at", "REAL"),
    ("connections", "delivery", "TEXT DEFAULT 'polling'"), ("connections", "delivery_note", "TEXT"),
    ("connections", "fail_count", "INTEGER DEFAULT 0"), ("watches", "metadata", "TEXT DEFAULT '{}'"), ("watches", "include", "TEXT DEFAULT '[]'"),
    ("sandbox", "raw", "BLOB"), ("items", "event_id", "TEXT"),
    ("users", "canon", "TEXT"), ("orgs", "suspended", "INTEGER DEFAULT 0"), ("orgs", "suspended_reason", "TEXT"), ("users", "verified", "INTEGER DEFAULT 1"), ("users", "display_name", "TEXT"), ("orgs", "helcim_customer", "TEXT"), ("orgs", "helcim_subscription", "TEXT"),
    ("orgs", "billing_status", "TEXT"), ("orgs", "billing_next", "TEXT"), ("orgs", "cancel_at", "REAL"), ("orgs", "past_due_since", "REAL"), ("api_keys", "created_by", "TEXT"),
]


def _set_aside_old_database() -> None:
    """A database from the earlier single-user version has a different layout. Keep it as a backup and start fresh."""
    if not DB_PATH.exists():
        return
    old = sqlite3.connect(DB_PATH)
    try:
        tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in old.execute("PRAGMA table_info(items)")} if "items" in tables else set()
        legacy = bool(tables) and ("watchers" in tables or ("items" in tables and "user_id" not in cols))
    finally:
        old.close()
    if legacy:
        backup = DB_PATH.with_name(f"{DB_PATH.name}.old-{int(time.time())}")
        DB_PATH.rename(backup)
        print(f"Found a database from an earlier version. Saved it as {backup} and started a new one.", flush=True)


BACKFILL = ("INSERT INTO orgs (id, name, plan, created) SELECT id, 'My organization', 'kit', created FROM users WHERE 1=1 ON CONFLICT (id) DO NOTHING",
            "INSERT INTO members (user_id, workspace_id, role, created) SELECT id, id, 'owner', created FROM users WHERE 1=1 ON CONFLICT (user_id) DO NOTHING")


def _pg_type(t: str) -> str:
    return t.replace("REAL", "DOUBLE PRECISION").replace("BLOB", "BYTEA")


def clean(v: Any) -> Any:
    """Text the database cannot hold is removed: PostgreSQL refuses the NUL character (0x00), and a lone surrogate (a half of an
    emoji that arrives from a broken client) cannot be written as UTF-8 by either database."""
    if not isinstance(v, str):
        return v
    v = v.replace("\x00", "")
    return v if v.isascii() else v.encode("utf-8", "ignore").decode("utf-8")


def _pg_sql(sql: str) -> str:
    """Our SQL is written with SQLite's ? placeholders and case-insensitive LIKE; PostgreSQL wants %s and ILIKE."""
    return re.sub(r"\bLIKE\b", "ILIKE", sql.replace("%", "%%").replace("?", "%s"))


def _open_pg():
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=int(__import__("os").getenv("WATCHFOX_DB_POOL", "10")),
                          kwargs={"autocommit": True, "row_factory": dict_row}, open=True, timeout=30)
    with pool.connection() as c:
        c.execute("SELECT pg_advisory_lock(727274)")            # one instance creates the tables at a time
        try:
            for stmt in [x.strip() for x in _pg_type(SCHEMA).split(";") if x.strip()]:
                c.execute(stmt)
            for table, col, typ in MIGRATIONS:
                c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {_pg_type(typ)}")
            for stmt in BACKFILL:
                c.execute(stmt)
        finally:
            c.execute("SELECT pg_advisory_unlock(727274)")
    return pool


def conn():
    """Open the database (and create or upgrade the tables) on first use. Returns the SQLite connection or the PostgreSQL pool."""
    global _conn, _pool
    if PG:
        if _pool is None:
            with _lock:
                if _pool is None:
                    _pool = _open_pg()
        return _pool
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _set_aside_old_database()
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            try:   # databases created by earlier builds
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        for stmt in BACKFILL:
            _conn.execute(stmt)
        _conn.commit()
        try:
            DB_PATH.chmod(0o600)
        except OSError:
            pass
    return _conn


def _run(sql: str, args: tuple = (), mode: str = "none"):
    """mode: "all" returns rows as dicts, "count" returns how many rows were changed, "none" returns nothing."""
    if PG:
        args = tuple(clean(a) for a in args)
        with conn().connection() as c:
            cur = c.execute(_pg_sql(sql), args)
            return cur.fetchall() if mode == "all" else cur.rowcount if mode == "count" else None
    args = tuple(clean(a) for a in args)
    with _lock:
        cur = conn().execute(sql, args)
        res = [dict(r) for r in cur.fetchall()] if mode == "all" else cur.rowcount if mode == "count" else None
        conn().commit()
        return res


def q(sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in _run(sql, args, "all")]


def one(sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: tuple = ()) -> None:
    _run(sql, args)


# ---------------------------------------------------------------- users, organizations and keys
# Everything a customer owns (mailboxes, watches, types, keys, logs) is stored under a "workspace id". An account's
# workspace is the id of the user who created the organization, so every team member works on the same data.
def create_user(email: str, pw_hash: str, verified: bool = False, workspace: str = "", name: str = "") -> str:
    """A new account. Without `workspace` it starts its own organization; with one it joins that organization as a member."""
    uid = vault.new_id("usr")
    now = time.time()
    x("INSERT INTO users (id, email, pw_hash, created, verified, canon) VALUES (?,?,?,?,?,?)", (uid, email.lower(), pw_hash, now, int(verified), canonical_email(email)))
    if workspace:
        x("INSERT INTO members (user_id, workspace_id, role, created) VALUES (?,?,?,?)", (uid, workspace, "member", now))
    else:
        x("INSERT INTO orgs (id, name, plan, created) VALUES (?,?,?,?)", (uid, name or "My organization", "kit", now))
        x("INSERT INTO members (user_id, workspace_id, role, created) VALUES (?,?,?,?)", (uid, uid, "owner", now))
    return uid


def canonical_email(email: str) -> str:
    """The same inbox however it is written: lower case, no +tag, and for Gmail no dots (so s.eth+x@gmail.com is sam@gmail.com)."""
    local, _, domain = email.strip().lower().partition("@")
    local = local.split("+")[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def backfill_canon() -> None:
    """Give accounts made before this check a canonical address. Runs at start-up."""
    for r in q("SELECT id, email FROM users WHERE canon IS NULL"):
        x("UPDATE users SET canon=? WHERE id=?", (canonical_email(r["email"]), r["id"]))


# ---------------------------------------------------------------- admin console (see admin.py)
def has_identity(uid: str, provider: str) -> bool: return one("SELECT 1 AS n FROM identities WHERE user_id=? AND provider=?", (uid, provider)) is not None
def add_audit(admin: str, action: str, target: str, detail: str = "") -> None:
    x("INSERT INTO admin_audit (id, at, admin, action, target, detail) VALUES (?,?,?,?,?,?)", (vault.new_id("aud"), time.time(), admin, action, target, detail[:500]))
def audit(limit: int = 100): return q("SELECT at, admin, action, target, detail FROM admin_audit ORDER BY at DESC LIMIT ?", (limit,))


def set_suspended(wid: str, on: bool, reason: str = "") -> None:
    x("UPDATE orgs SET suspended=?, suspended_reason=? WHERE id=?", (1 if on else 0, reason[:200] if on else None, wid))


def reset_runs(wid: str, month: str) -> None: x("DELETE FROM usage WHERE workspace_id=? AND month=?", (wid, month))


def revoke_all_keys(wid: str) -> int:
    n = (one("SELECT COUNT(*) AS n FROM api_keys WHERE user_id=? AND revoked=0", (wid,)) or {"n": 0})["n"]
    x("UPDATE api_keys SET revoked=1 WHERE user_id=?", (wid,))
    return n


def admin_overview(month: str) -> Dict[str, Any]:
    def n(sql: str, args: tuple = ()) -> int: return int((one(sql, args) or {"n": 0})["n"] or 0)
    now = time.time()
    plans = {r["plan"] or "kit": r["n"] for r in q("SELECT plan, COUNT(*) AS n FROM orgs GROUP BY plan")}
    return {"orgs": n("SELECT COUNT(*) AS n FROM orgs"), "users": n("SELECT COUNT(*) AS n FROM users"), "plans": plans,
            "subscriptions": n("SELECT COUNT(*) AS n FROM orgs WHERE helcim_subscription IS NOT NULL AND helcim_subscription != ''"),
            "suspended": n("SELECT COUNT(*) AS n FROM orgs WHERE suspended=1"),
            "new_24h": n("SELECT COUNT(*) AS n FROM users WHERE created>=?", (now - 86400,)), "new_7d": n("SELECT COUNT(*) AS n FROM users WHERE created>=?", (now - 7 * 86400,)),
            "mailboxes": n("SELECT COUNT(*) AS n FROM connections WHERE provider!='sandbox'"), "sandboxes": n("SELECT COUNT(*) AS n FROM connections WHERE provider='sandbox'"),
            "needs_attention": n("SELECT COUNT(*) AS n FROM connections WHERE status='needs_attention'"), "watches": n("SELECT COUNT(*) AS n FROM watches"),
            "runs_this_month": n("SELECT COALESCE(SUM(runs),0) AS n FROM usage WHERE month=?", (month,)),
            "checked_24h": n("SELECT COUNT(*) AS n FROM items WHERE processed>=?", (now - 86400,)), "caught_24h": n("SELECT COUNT(*) AS n FROM items WHERE processed>=? AND matched=1", (now - 86400,))}


def admin_orgs(month: str, search: str = "", limit: int = 50, offset: int = 0):
    like = f"%{search.strip().lower()}%"
    return q("""SELECT o.id, o.name, o.plan, o.billing, o.billing_status, o.billing_next, o.suspended, o.created,
                  (o.helcim_subscription IS NOT NULL AND o.helcim_subscription != '') AS subscribed,
                  (SELECT COUNT(*) FROM members m WHERE m.workspace_id=o.id) AS members,
                  (SELECT COUNT(*) FROM connections c WHERE c.user_id=o.id) AS mailboxes,
                  (SELECT COUNT(*) FROM watches w WHERE w.user_id=o.id) AS watches,
                  COALESCE((SELECT u.runs FROM usage u WHERE u.workspace_id=o.id AND u.month=?), 0) AS runs,
                  (SELECT u2.email FROM members m2 JOIN users u2 ON u2.id=m2.user_id WHERE m2.workspace_id=o.id AND m2.role='owner' LIMIT 1) AS owner,
                  (SELECT MAX(i.processed) FROM items i WHERE i.user_id=o.id) AS last_activity
                FROM orgs o
                WHERE (?='' OR LOWER(o.name) LIKE ? OR LOWER(o.id) LIKE ?
                       OR EXISTS (SELECT 1 FROM members m3 JOIN users u3 ON u3.id=m3.user_id WHERE m3.workspace_id=o.id AND LOWER(u3.email) LIKE ?))
                ORDER BY o.created DESC LIMIT ? OFFSET ?""", (month, search.strip(), like, like, like, limit, offset))


def admin_users(search: str = "", limit: int = 100, offset: int = 0):
    like = f"%{search.strip().lower()}%"
    return q("""SELECT u.id, u.email, u.verified, u.created, m.workspace_id, m.role, o.name AS org, o.plan,
                  (SELECT COUNT(*) FROM identities i WHERE i.user_id=u.id AND i.provider='github') AS github,
                  (SELECT COUNT(*) FROM identities i WHERE i.user_id=u.id AND i.provider='google') AS google,
                  (SELECT COUNT(*) FROM identities i WHERE i.user_id=u.id AND i.provider='microsoft') AS microsoft
                FROM users u LEFT JOIN members m ON m.user_id=u.id LEFT JOIN orgs o ON o.id=m.workspace_id
                WHERE (?='' OR LOWER(u.email) LIKE ? OR LOWER(u.id) LIKE ?) ORDER BY u.created DESC LIMIT ? OFFSET ?""", (search.strip(), like, like, limit, offset))


def admin_org_detail(wid: str, months: list) -> Optional[Dict[str, Any]]:
    o = org(wid)
    if not o:
        return None
    o = {k: v for k, v in o.items() if k not in ("helcim_customer",)}
    return {"org": o, "members": team(wid), "invites": q("SELECT id, email, created, expires, accepted FROM invites WHERE workspace_id=? ORDER BY created DESC", (wid,)),
            "mailboxes": q("SELECT id, provider, email, status, status_reason, delivery, created FROM connections WHERE user_id=? ORDER BY created", (wid,)),
            "watches": q("SELECT id, connection_id, what, webhook_url, last_run, last_error, created FROM watches WHERE user_id=? ORDER BY created", (wid,)),
            "keys": q("SELECT id, name, prefix, created, last_used, revoked FROM api_keys WHERE user_id=? ORDER BY created", (wid,)),
            "types": (one("SELECT COUNT(*) AS n FROM types WHERE user_id=? AND id NOT LIKE 'override:%'", (wid,)) or {"n": 0})["n"],
            "usage": [{"month": m, "runs": runs_used(wid, m)} for m in months],
            "checked_total": (one("SELECT COUNT(*) AS n FROM items WHERE user_id=?", (wid,)) or {"n": 0})["n"]}


def delete_org(wid: str) -> Dict[str, int]:
    """Remove an organization and everything it owns: mailboxes (with their watches and logs), keys, types, flagged emails, invitations,
    counters, and every person in it. The caller stops running checks and cancels billing first."""
    out = {"mailboxes": 0, "people": 0}
    for c in connections(wid):
        delete_connection(wid, c["id"])
        out["mailboxes"] += 1
    for w in watches(wid):
        delete_watch(wid, w["id"])
    for t in ("types", "feedback", "api_keys"):
        x(f"DELETE FROM {t} WHERE user_id=?", (wid,))
    for t in ("invites", "usage", "billing_sessions", "plan_requests"):
        x(f"DELETE FROM {t} WHERE workspace_id=?", (wid,))
    for m in q("SELECT user_id FROM members WHERE workspace_id=?", (wid,)):
        x("DELETE FROM identities WHERE user_id=?", (m["user_id"],))
        x("DELETE FROM members WHERE user_id=?", (m["user_id"],))
        x("DELETE FROM users WHERE id=?", (m["user_id"],))
        out["people"] += 1
    x("DELETE FROM orgs WHERE id=?", (wid,))
    return out


def user_by_canon(canon: str): return one("SELECT id FROM users WHERE canon=?", (canon,))
def add_signup(ip: str) -> None: x("INSERT INTO signups (ip, created) VALUES (?,?)", (ip, time.time()))
def signups_since(ip: str, t: float) -> int: return (one("SELECT COUNT(*) AS n FROM signups WHERE ip=? AND created>=?", (ip, t)) or {"n": 0})["n"]
def signups_total_since(t: float) -> int: return (one("SELECT COUNT(*) AS n FROM signups WHERE created>=?", (t,)) or {"n": 0})["n"]
def mailbox_runs(h: str, month: str) -> int: return (one("SELECT runs FROM mailbox_runs WHERE email_hash=? AND month=?", (h, month)) or {"runs": 0})["runs"]


def add_mailbox_run(h: str, month: str) -> None:
    x("INSERT INTO mailbox_runs (email_hash, month, runs) VALUES (?,?,1) ON CONFLICT (email_hash, month) DO UPDATE SET runs = mailbox_runs.runs + 1", (h, month))


def user_by_email(email: str): return one("SELECT * FROM users WHERE email=?", (email.lower(),))
def user_by_email_id(uid: str): return one("SELECT * FROM users WHERE id=?", (uid,))
def user(uid: str): return one("SELECT id, email, created, verified, display_name FROM users WHERE id=?", (uid,))
def set_display_name(uid: str, name: str) -> None: x("UPDATE users SET display_name=? WHERE id=? AND (display_name IS NULL OR display_name='')", (name[:80], uid))
def set_verified(uid: str) -> None: x("UPDATE users SET verified=1 WHERE id=?", (uid,))
def set_password(uid: str, pw_hash: str) -> None: x("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash, uid))
def member(uid: str): return one("SELECT workspace_id, role FROM members WHERE user_id=?", (uid,))
def org(wid: str): return one("SELECT * FROM orgs WHERE id=?", (wid,))
def set_org_name(wid: str, name: str) -> None: x("UPDATE orgs SET name=? WHERE id=?", (name, wid))
def set_plan(wid: str, plan: str, billing: str = "monthly") -> None: x("UPDATE orgs SET plan=?, billing=? WHERE id=?", (plan, billing, wid))


def team(wid: str):
    return q("SELECT u.id, u.email, u.verified, m.role, m.created FROM members m JOIN users u ON u.id=m.user_id "
             "WHERE m.workspace_id=? ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END, m.created", (wid,))


def remove_member(wid: str, uid: str) -> bool:
    if one("SELECT 1 AS n FROM members WHERE user_id=? AND workspace_id=? AND role!='owner'", (uid, wid)) is None:
        return False
    x("UPDATE api_keys SET revoked=1 WHERE user_id=? AND created_by=?", (wid, uid))
    x("DELETE FROM members WHERE user_id=?", (uid,))
    x("DELETE FROM identities WHERE user_id=?", (uid,))
    x("DELETE FROM users WHERE id=?", (uid,))
    return True


def add_invite(wid: str, email: str, token_hash: str, by: str, ttl: int) -> str:
    iid = vault.new_id("inv")
    now = time.time()
    x("INSERT INTO invites (id, workspace_id, email, token_hash, invited_by, created, expires) VALUES (?,?,?,?,?,?,?)",
      (iid, wid, email.lower(), token_hash, by, now, now + ttl))
    return iid


def invites(wid: str):
    return q("SELECT id, email, created, expires FROM invites WHERE workspace_id=? AND accepted IS NULL AND expires>? ORDER BY created", (wid, time.time()))


def invite_by_token(token_hash: str):
    return one("SELECT i.*, o.name AS org_name FROM invites i JOIN orgs o ON o.id=i.workspace_id "
               "WHERE i.token_hash=? AND i.accepted IS NULL AND i.expires>?", (token_hash, time.time()))


def invite_by_id(iid: str):
    return one("SELECT i.*, o.name AS org_name FROM invites i JOIN orgs o ON o.id=i.workspace_id "
               "WHERE i.id=? AND i.accepted IS NULL AND i.expires>?", (iid, time.time()))


def invite_for_email(email: str):
    return one("SELECT * FROM invites WHERE email=? AND accepted IS NULL AND expires>? ORDER BY created DESC", (email.lower(), time.time()))


def accept_invite(iid: str) -> None: x("UPDATE invites SET accepted=? WHERE id=?", (time.time(), iid))
def revoke_invite(wid: str, iid: str) -> bool:
    ok = one("SELECT 1 AS n FROM invites WHERE id=? AND workspace_id=? AND accepted IS NULL", (iid, wid)) is not None
    x("DELETE FROM invites WHERE id=? AND workspace_id=?", (iid, wid))
    return ok


def identity(provider: str, subject: str): return one("SELECT user_id FROM identities WHERE provider=? AND subject=?", (provider, subject))
def add_identity(provider: str, subject: str, uid: str) -> None:
    x("INSERT INTO identities (provider, subject, user_id) VALUES (?,?,?) ON CONFLICT (provider, subject) DO UPDATE SET user_id=EXCLUDED.user_id", (provider, subject, uid))


def runs_used(wid: str, month: str) -> int:
    r = one("SELECT runs FROM usage WHERE workspace_id=? AND month=?", (wid, month))
    return int(r["runs"]) if r else 0


def add_runs(wid: str, month: str, n: int = 1) -> None:
    x("INSERT INTO usage (workspace_id, month, runs) VALUES (?,?,?) ON CONFLICT(workspace_id, month) DO UPDATE SET runs=usage.runs+?", (wid, month, n, n))


def add_plan_request(wid: str, plan: str, billing: str, email: str) -> str:
    rid = vault.new_id("req")
    x("INSERT INTO plan_requests (id, workspace_id, plan, billing, email, created) VALUES (?,?,?,?,?,?)", (rid, wid, plan, billing, email, time.time()))
    return rid


def add_key(uid: str, name: str, prefix: str, key_hash: str, created_by: str = "") -> str:
    kid = vault.new_id("key")
    x("INSERT INTO api_keys (id, user_id, name, prefix, key_hash, created, created_by) VALUES (?,?,?,?,?,?,?)",
      (kid, uid, name, prefix, key_hash, time.time(), created_by or None))
    return kid


def user_for_key(key_hash: str):
    row = one("SELECT u.id, u.email, k.id AS key_id FROM api_keys k JOIN users u ON u.id=k.user_id "
              "WHERE k.key_hash=? AND k.revoked=0", (key_hash,))
    if row:
        x("UPDATE api_keys SET last_used=? WHERE id=?", (time.time(), row["key_id"]))
    return row


def keys(uid: str):
    return q("SELECT k.id, k.name, k.prefix, k.created, k.last_used, u.email AS created_by FROM api_keys k "
             "LEFT JOIN users u ON u.id=k.created_by WHERE k.user_id=? AND k.revoked=0 ORDER BY k.created", (uid,))


def revoke_key(uid: str, kid: str) -> bool:
    n = one("SELECT 1 AS n FROM api_keys WHERE id=? AND user_id=? AND revoked=0", (kid, uid))
    x("UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (kid, uid))
    return bool(n)


# ---------------------------------------------------------------- connections (mailboxes)
def upsert_connection(uid: str, provider: str, email: str, secret: Any, metadata: Optional[dict] = None, cid: str = "") -> str:
    """Create a mailbox connection, or refresh the credentials of an existing one (reconnect)."""
    enc = vault.encrypt(secret)
    existing = connection(cid, uid) if cid else one(
        "SELECT * FROM connections WHERE user_id=? AND provider=? AND email=?", (uid, provider, email))
    if existing:
        meta = json.loads(existing.get("metadata") or "{}")
        meta.update(metadata or {})
        x("UPDATE connections SET secret=?, metadata=?, status='active', status_reason=NULL, status_at=?, fail_count=0 WHERE id=?",
          (enc, json.dumps(meta), time.time(), existing["id"]))
        return existing["id"]
    new = vault.new_id("cn")
    x("INSERT INTO connections (id, user_id, provider, email, secret, created, metadata, status) VALUES (?,?,?,?,?,?,?,'active')",
      (new, uid, provider, email, enc, time.time(), json.dumps(metadata or {})))
    return new


def connections(uid: str):
    return q("SELECT id, provider, email, created, metadata, status, status_reason, delivery, delivery_note "
             "FROM connections WHERE user_id=? ORDER BY created", (uid,))


def all_connections(provider: str = ""):
    return q("SELECT * FROM connections" + (" WHERE provider=?" if provider else ""), (provider,) if provider else ())


def connection(cid: str, uid: str = ""):
    return one("SELECT * FROM connections WHERE id=?" + (" AND user_id=?" if uid else ""), (cid, uid) if uid else (cid,))


def save_connection_secret(cid: str, secret: Any) -> None:
    x("UPDATE connections SET secret=? WHERE id=?", (vault.encrypt(secret), cid))


def set_connection_status(cid: str, status: str, reason: Optional[str] = None) -> None:
    x("UPDATE connections SET status=?, status_reason=?, status_at=? WHERE id=?", (status, reason, time.time(), cid))


def bump_fail(cid: str) -> int:
    x("UPDATE connections SET fail_count=COALESCE(fail_count,0)+1 WHERE id=?", (cid,))
    return (connection(cid) or {}).get("fail_count", 0)


def reset_fail(cid: str) -> None:
    x("UPDATE connections SET fail_count=0 WHERE id=? AND fail_count>0", (cid,))


def set_delivery(cid: str, mode: str, note: Optional[str] = None) -> None:
    x("UPDATE connections SET delivery=?, delivery_note=? WHERE id=?", (mode, note, cid))


def delete_connection(uid: str, cid: str) -> bool:
    if not connection(cid, uid):
        return False
    for w in watches(uid):
        if w["connection_id"] == cid:
            delete_watch(uid, w["id"])
    x("DELETE FROM sandbox WHERE connection_id=?", (cid,))
    x("DELETE FROM subscriptions WHERE connection_id=?", (cid,))
    x("DELETE FROM connections WHERE id=? AND user_id=?", (cid, uid))
    return True


# ---------------------------------------------------------------- Microsoft push subscriptions
def save_subscription(sub_id: str, cid: str, client_state: str, expires: float) -> None:
    x("DELETE FROM subscriptions WHERE connection_id=?", (cid,))
    x("INSERT INTO subscriptions (id, connection_id, client_state, expires, created, last_renewed) VALUES (?,?,?,?,?,?)",
      (sub_id, cid, client_state, expires, time.time(), time.time()))


def subscription_for(cid: str): return one("SELECT * FROM subscriptions WHERE connection_id=?", (cid,))
def subscription(sub_id: str): return one("SELECT * FROM subscriptions WHERE id=?", (sub_id,))
def drop_subscription(cid: str) -> None: x("DELETE FROM subscriptions WHERE connection_id=?", (cid,))


def renewed_subscription(sub_id: str, expires: float) -> None:
    x("UPDATE subscriptions SET expires=?, last_renewed=? WHERE id=?", (expires, time.time(), sub_id))


# ---------------------------------------------------------------- watches
def add_watch(uid: str, connection_id: str, what: list, webhook_url: str, webhook_secret: str, backfill_days: int, metadata: Optional[dict] = None, include: Optional[list] = None) -> str:
    wid = vault.new_id("wt")
    x("INSERT INTO watches (id, user_id, connection_id, what, webhook_url, webhook_secret, backfill_days, created, metadata, include)"
      " VALUES (?,?,?,?,?,?,?,?,?,?)",
      (wid, uid, connection_id, json.dumps(what), webhook_url or "", vault.encrypt(webhook_secret) if webhook_secret else "",
       backfill_days, time.time(), json.dumps(metadata or {}), json.dumps(include or [])))
    return wid


def _w(r):
    r["what"] = json.loads(r["what"])
    r["metadata"] = json.loads(r.get("metadata") or "{}")
    r["include"] = json.loads(r.get("include") or "[]")
    return r


def watches_for_connection(cid: str):
    return [_w(r) for r in q("SELECT * FROM watches WHERE connection_id=? ORDER BY created", (cid,))]


def watches(uid: str = ""):
    rows = q("SELECT * FROM watches" + (" WHERE user_id=?" if uid else "") + " ORDER BY created", (uid,) if uid else ())
    return [_w(r) for r in rows]


def watch(wid: str, uid: str = ""):
    r = one("SELECT * FROM watches WHERE id=?" + (" AND user_id=?" if uid else ""), (wid, uid) if uid else (wid,))
    return _w(r) if r else None


def delete_watch(uid: str, wid: str) -> bool:
    if not watch(wid, uid):
        return False
    x("DELETE FROM items WHERE watch_id=?", (wid,))
    x("DELETE FROM deliveries WHERE watch_id=?", (wid,))
    x("DELETE FROM watches WHERE id=?", (wid,))
    return True


def set_status(wid: str, error: Optional[str]) -> None:
    x("UPDATE watches SET last_run=?, last_error=? WHERE id=?", (time.time(), error, wid))


# ---------------------------------------------------------------- items
def seen(wid: str, item_id: str) -> bool:
    return bool(one("SELECT 1 AS n FROM items WHERE key=?", (f"{wid}:{item_id}",)))


def save_item(w: Dict[str, Any], item, results: Dict[str, Any], event_id: str = "") -> None:
    pub = item.public()
    x("INSERT INTO items (key, user_id, watch_id, item_id, title, sender, snippet, received, attachments,"
      " results, matched, processed, event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
      " ON CONFLICT (key) DO UPDATE SET user_id=EXCLUDED.user_id, watch_id=EXCLUDED.watch_id, item_id=EXCLUDED.item_id,"
      " title=EXCLUDED.title, sender=EXCLUDED.sender, snippet=EXCLUDED.snippet, received=EXCLUDED.received,"
      " attachments=EXCLUDED.attachments, results=EXCLUDED.results, matched=EXCLUDED.matched,"
      " processed=EXCLUDED.processed, event_id=EXCLUDED.event_id",
      (f"{w['id']}:{item.id}", w["user_id"], w["id"], item.id, item.title, item.sender, pub["snippet"], item.received,
       json.dumps(item.attachments), json.dumps(results), int(any(r["matched"] for r in results.values())), time.time(), event_id or None))


def items(uid: str, matched_only: bool = False, watch_id: str = "", what: str = "", limit: int = 50):
    sql, args = "SELECT * FROM items WHERE user_id=?", [uid]
    if matched_only:
        sql += " AND matched=1"
    if watch_id:
        sql += " AND watch_id=?"; args.append(watch_id)
    sql += " ORDER BY processed DESC LIMIT ?"
    args.append(min(limit * 4 if what else limit, 1000))
    out = []
    for r in q(sql, tuple(args)):
        r["results"] = json.loads(r["results"])
        r["attachments"] = json.loads(r["attachments"] or "[]")
        r["matched"] = bool(r["matched"])
        if not r["results"]:      # marked as seen without being checked (mail older than the watch)
            continue
        if what and not r["results"].get(what, {}).get("matched"):
            continue
        out.append(r)
    return out[:limit]


def log_page(uid: str, matched: Optional[bool] = None, watch_id: str = "", connection_id: str = "", since: float = 0,
             before: float = 0, q_text: str = "", delivery: str = "", limit: int = 50):
    """Checked emails with what happened to each, newest first. `before` is the `next_before` of the previous page."""
    sql = ("SELECT i.*, w.connection_id AS cid, w.webhook_url AS hook, w.include AS inc, d.id AS d_id, d.status AS d_status, "
           "d.code AS d_code, d.attempts AS d_attempts, d.error AS d_error, d.updated AS d_updated, d.created AS d_created "
           "FROM items i JOIN watches w ON w.id=i.watch_id "
           "LEFT JOIN deliveries d ON d.event_id=i.event_id AND d.watch_id=i.watch_id "
           "WHERE i.user_id=? AND i.results != '{}'")
    args: list = [uid]
    if matched is not None:
        sql += " AND i.matched=?"; args.append(int(matched))
    if watch_id:
        sql += " AND i.watch_id=?"; args.append(watch_id)
    if connection_id:
        sql += " AND w.connection_id=?"; args.append(connection_id)
    if since:
        sql += " AND i.processed>=?"; args.append(since)
    if before:
        sql += " AND i.processed<?"; args.append(before)
    if q_text:
        sql += " AND (i.title LIKE ? OR i.sender LIKE ?)"; args += [f"%{q_text}%", f"%{q_text}%"]
    if delivery == "none":
        sql += " AND d.id IS NULL"
    elif delivery:
        sql += " AND d.status=?"; args.append(delivery)
    sql += " ORDER BY i.processed DESC, i.key DESC LIMIT ?"
    args.append(min(max(limit, 1), 200))
    rows = q(sql, tuple(args))
    for r in rows:
        r["results"] = json.loads(r["results"])
        r["attachments"] = json.loads(r["attachments"] or "[]")
        r["inc"] = json.loads(r["inc"] or "[]")
    return rows


# ---------------------------------------------------------------- deliveries
def add_delivery(wid: str, event_id: str, url: str) -> str:
    did = vault.new_id("dl")
    x("INSERT INTO deliveries (id, watch_id, event_id, url, status, attempts, created, updated) VALUES (?,?,?,?,?,?,?,?)",
      (did, wid, event_id, url, "pending", 0, time.time(), time.time()))
    return did


def update_delivery(did: str, status: str, code: Optional[int], attempts: int, error: str = "") -> None:
    x("UPDATE deliveries SET status=?, code=?, attempts=?, error=?, updated=? WHERE id=?",
      (status, code, attempts, error[:300], time.time(), did))


def deliveries(wid: str, limit: int = 20):
    return q("SELECT id, event_id, status, code, attempts, error, created, updated FROM deliveries "
             "WHERE watch_id=? ORDER BY created DESC LIMIT ?", (wid, limit))


# ---------------------------------------------------------------- sandbox mailbox (for testing)
def add_sandbox_email(cid: str, sender: str, subject: str, body: str, raw: Optional[bytes] = None) -> str:
    eid = vault.new_id("sb")
    x("INSERT INTO sandbox (id, connection_id, sender, subject, body, received, raw) VALUES (?,?,?,?,?,?,?)",
      (eid, cid, sender, subject, body, time.time(), raw))
    return eid


def sandbox_email(eid: str): return one("SELECT * FROM sandbox WHERE id=?", (eid,))


def matched_item(uid: str, cid: str, email_id: str):
    """An email that matched one of this user's watches on this mailbox. Only these may be downloaded."""
    return one("SELECT i.* FROM items i JOIN watches w ON w.id=i.watch_id WHERE i.user_id=? AND w.connection_id=? "
               "AND i.item_id=? AND i.matched=1 LIMIT 1", (uid, cid, email_id))


def sandbox_emails(cid: str):
    return q("SELECT * FROM sandbox WHERE connection_id=? ORDER BY received", (cid,))


# ---------------------------------------------------------------- custom types
def _t(r):
    if r:
        r["spec"] = json.loads(r["spec"])
    return r


def _real(tid: str, uid: str) -> str:
    """Ids are global in the table, so a user's tuned copy of a default type is stored as override:<preset>:<user>."""
    return f"{tid}:{uid}" if tid.startswith("override:") else tid


def add_type(uid: str, label: str, description: str, spec: dict) -> str:
    tid = vault.new_id("type")
    x("INSERT INTO types (id, user_id, label, description, spec, created) VALUES (?,?,?,?,?,?)",
      (tid, uid, label, description, json.dumps(spec), time.time()))
    return tid


def types(uid: str): return [_t(r) for r in q("SELECT * FROM types WHERE user_id=? AND id NOT LIKE 'override:%' ORDER BY created", (uid,))]
def type_(tid: str, uid: str): return _t(one("SELECT * FROM types WHERE id=? AND user_id=?", (_real(tid, uid), uid)))
def delete_type(uid: str, tid: str) -> bool:
    tid = _real(tid, uid)
    if one("SELECT 1 FROM types WHERE id=? AND user_id=?", (tid, uid)) is None:
        return False
    x("DELETE FROM types WHERE id=? AND user_id=?", (tid, uid))
    return True


def set_type_spec(uid: str, tid: str, label: str, description: str, spec: dict) -> None:
    """Replace a custom type's settings, or create/replace the user's tuned copy of a default type (id override:<preset>)."""
    tid = _real(tid, uid)
    if one("SELECT 1 FROM types WHERE id=? AND user_id=?", (tid, uid)):
        x("UPDATE types SET label=?, description=?, spec=? WHERE id=? AND user_id=?", (label, description, json.dumps(spec), tid, uid))
    else:
        x("INSERT INTO types (id, user_id, label, description, spec, created) VALUES (?,?,?,?,?,?)",
          (tid, uid, label, description, json.dumps(spec), time.time()))


# ---------------------------------------------------------------- corrections (wrong catches and misses the user flagged)
def add_feedback(uid: str, what_id: str, item_key: str, kind: str, subject: str, body: str) -> bool:
    if one("SELECT 1 FROM feedback WHERE user_id=? AND what_id=? AND item_key=?", (uid, what_id, item_key)):
        return False
    x("INSERT INTO feedback (id, user_id, what_id, item_key, kind, subject, body, created) VALUES (?,?,?,?,?,?,?,?)",
      (vault.new_id("fb"), uid, what_id, item_key, kind, subject, body, time.time()))
    return True


def feedback(uid: str, what_id: str = ""):
    if what_id:
        return q("SELECT * FROM feedback WHERE user_id=? AND what_id=? ORDER BY created", (uid, what_id))
    return q("SELECT * FROM feedback WHERE user_id=? ORDER BY created", (uid,))


def clear_feedback(uid: str, what_id: str) -> None:
    x("DELETE FROM feedback WHERE user_id=? AND what_id=?", (uid, what_id))


def item_row(uid: str, key: str): return one("SELECT * FROM items WHERE key=? AND user_id=?", (key, uid))


def update_watch(uid: str, wid: str, what=None, webhook_url=None, webhook_secret=None, include=None, metadata=None) -> None:
    w = watch(wid, uid)
    if not w:
        return
    x("UPDATE watches SET what=?, webhook_url=?, webhook_secret=?, include=?, metadata=? WHERE id=? AND user_id=?",
      (json.dumps(w["what"] if what is None else what),
       w["webhook_url"] if webhook_url is None else webhook_url,
       w["webhook_secret"] if webhook_secret is None else (vault.encrypt(webhook_secret) if webhook_secret else ""),
       json.dumps(w["include"] if include is None else include),
       json.dumps(w["metadata"] if metadata is None else metadata), wid, uid))


def prune(cutoff: float) -> Dict[str, int]:
    """Delete log rows (checked emails and webhook deliveries) older than the cutoff. Returns how many of each."""
    n_items = _run("DELETE FROM items WHERE processed<?", (cutoff,), "count")
    n_del = _run("DELETE FROM deliveries WHERE created<?", (cutoff,), "count")
    x("DELETE FROM signups WHERE created<?", (time.time() - 7 * 86400,))
    return {"items": n_items, "deliveries": n_del}


# ---------------------------------------------------------------- billing (Helcim)
def save_billing_session(token_hash: str, wid: str, secret: str, plan: str, billing: str, kind: str, customer_code: str) -> None:
    x("DELETE FROM billing_sessions WHERE created<? OR token_hash=?", (time.time() - 7200, token_hash))
    x("INSERT INTO billing_sessions (token_hash, workspace_id, secret, plan, billing, kind, customer_code, created) VALUES (?,?,?,?,?,?,?,?)",
      (token_hash, wid, secret, plan, billing, kind, customer_code, time.time()))


def billing_session(token_hash: str, wid: str):
    return one("SELECT * FROM billing_sessions WHERE token_hash=? AND workspace_id=? AND created>?", (token_hash, wid, time.time() - 3600))


def drop_billing_session(token_hash: str) -> None: x("DELETE FROM billing_sessions WHERE token_hash=?", (token_hash,))


def set_billing(wid: str, **fields) -> None:
    allowed = {"helcim_customer", "helcim_subscription", "billing_status", "billing_next", "cancel_at", "past_due_since"}
    cols = [k for k in fields if k in allowed]
    if cols:
        x(f"UPDATE orgs SET {', '.join(c + '=?' for c in cols)} WHERE id=?", tuple(fields[c] for c in cols) + (wid,))


def orgs_with_subscriptions():
    return q("SELECT * FROM orgs WHERE helcim_subscription IS NOT NULL AND helcim_subscription != ''")
