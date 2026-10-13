"""Plans, limits and run metering.

A "run" is one email checked by one watch, whether or not it is caught. Emails checked through the test call
(POST /v1/classify) and sandbox emails count too. A watch that lists several types still counts as one run per email.
"""
import time
from typing import Any, Dict

from . import config, store

PLANS: Dict[str, Dict[str, Any]] = {
    "kit": {"id": "kit", "name": "Kit", "tagline": "Try it out", "monthly": 0, "annual": 0, "runs": 50,
            "mailboxes": 2, "watches": 3, "types": 3, "members": 3},
    "tracker": {"id": "tracker", "name": "Tracker", "tagline": "For products going live", "monthly": 29, "annual": 290, "runs": 5000,
                "mailboxes": 10, "watches": 25, "types": 15, "members": 10},
    "alpha": {"id": "alpha", "name": "Alpha", "tagline": "For real volume", "monthly": 149, "annual": 1490, "runs": 50000,
              "mailboxes": 50, "watches": 100, "types": 50, "members": 50},
}
DEFAULT = "kit"
SHORT = {"mailboxes": "MAX_CONNECTIONS", "watches": "MAX_WATCHES", "types": "MAX_TYPES"}


def catalogue() -> list:
    out = []
    for p in PLANS.values():
        d = dict(p)
        d["annual_monthly_equivalent"] = round(p["annual"] / 12, 2) if p["annual"] else 0
        d["annual_saving_percent"] = round(100 * (1 - p["annual"] / (p["monthly"] * 12))) if p["monthly"] else 0
        out.append(d)
    return out


def month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def next_reset() -> int:
    t = time.gmtime()
    y, m = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    import calendar
    return calendar.timegm((y, m, 1, 0, 0, 0))


def plan_of(wid: str) -> Dict[str, Any]:
    o = store.org(wid)
    return PLANS.get((o or {}).get("plan") or DEFAULT, PLANS[DEFAULT])


def limit(wid: str, what: str) -> int:
    """The most this account may have of: runs, mailboxes, watches, types or members."""
    if not config.ENFORCE_PLANS:
        return {"runs": 10 ** 9, "members": 10 ** 6}.get(what) or getattr(config, SHORT[what])
    return plan_of(wid)[what]


def used(wid: str) -> int:
    return store.runs_used(wid, month())


def allow(wid: str) -> bool:
    return used(wid) < limit(wid, "runs")


def add_run(wid: str) -> None:
    store.add_runs(wid, month(), 1)


def summary(wid: str) -> Dict[str, Any]:
    p, o = plan_of(wid), store.org(wid) or {}
    n = used(wid)
    return {"plan": p["id"], "plan_name": p["name"], "billing": o.get("billing") or "monthly", "runs_used": n, "runs_limit": p["runs"],
            "runs_remaining": max(0, p["runs"] - n), "resets_at": next_reset(),
            "limits": {k: p[k] for k in ("mailboxes", "watches", "types", "members")},
            "next_plan": next((q["id"] for q in PLANS.values() if q["runs"] > p["runs"]), None)}
