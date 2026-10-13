"""The admin console: the owner of the service can see every organization and person and manage them. Reached at /admin.

Who may use it: only people listed in WATCHFOX_ADMIN_EMAILS, signed in on the website (never with an API key), whose email is verified and
who signed in through the admin provider (GitHub by default). Everyone else, signed in or not, gets a plain "not found", so the console
cannot even be discovered. Every change needs a header a web page on another site cannot send, and every change is written to an audit log.
The console never shows email content, mailbox passwords, webhook secrets or API keys."""
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import billing, config, helcim, limiter, push, store
from .auth import COOKIE
from . import vault
from .runner import runner

router = APIRouter(prefix="/v1/admin")


def admin_of(request: Request) -> Optional[Dict[str, Any]]:
    """The signed-in admin, or None. Only the website session counts: an API key never makes anyone an admin."""
    if not config.ADMIN_EMAILS:
        return None
    tok = request.cookies.get(COOKIE)
    data = vault.verify(tok) if tok else None
    u = store.user(data["uid"]) if data and data.get("uid") else None
    if not u or not u["verified"] or u["email"].lower() not in config.ADMIN_EMAILS:
        return None
    if config.ADMIN_PROVIDER and not store.has_identity(u["id"], config.ADMIN_PROVIDER):
        return None
    return u


def admin_only(request: Request) -> Dict[str, Any]:
    u = admin_of(request)
    if not u:
        raise HTTPException(404, "Not found.")
    limiter.hit("admin", u["id"], 600, 60)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-watchfox-admin") != "1":
        raise HTTPException(403, "Missing admin header.")
    return u


def _month() -> str:
    return billing.month()


@router.get("/overview")
def overview(admin=Depends(admin_only)):
    from .engine import engine
    return {**store.admin_overview(_month()), "agent": engine.serving, "queued": engine.waiting, "month": _month()}


@router.get("/orgs")
def orgs(q: str = "", limit: int = 50, offset: int = 0, admin=Depends(admin_only)):
    return {"orgs": store.admin_orgs(_month(), q[:80], max(1, min(limit, 200)), max(0, offset))}


def _recent_months(n: int = 3):
    t = time.gmtime()
    out = []
    y, m = t.tm_year, t.tm_mon
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


@router.get("/orgs/{wid}")
def org_detail(wid: str, admin=Depends(admin_only)):
    d = store.admin_org_detail(wid, _recent_months())
    if not d:
        raise HTTPException(404, "No such organization.")
    for w in d["watches"]:                                    # only the host of a webhook is shown, never its secret or path
        w["webhook_host"] = urlparse(w.pop("webhook_url") or "").hostname or ""
    d["limits"] = billing.PLANS.get(d["org"].get("plan") or "kit", billing.PLANS["kit"])
    return d


class PlanIn(BaseModel):
    plan: str
    billing: str = "monthly"


@router.post("/orgs/{wid}/plan")
def set_plan(wid: str, body: PlanIn, admin=Depends(admin_only)):
    """Give an organization a plan without charging: for friends, partners and fixes. A paid subscription in Helcim is not touched."""
    if body.plan not in billing.PLANS or body.billing not in ("monthly", "annual"):
        raise HTTPException(400, "Unknown plan.")
    if not store.org(wid):
        raise HTTPException(404, "No such organization.")
    store.set_plan(wid, body.plan, body.billing)
    store.add_audit(admin["email"], "set plan", wid, f"{body.plan} ({body.billing}); billing in Helcim not changed")
    return {"ok": True}


class SuspendIn(BaseModel):
    suspended: bool
    reason: str = ""


@router.post("/orgs/{wid}/suspend")
def suspend(wid: str, body: SuspendIn, admin=Depends(admin_only)):
    """Stop an organization: its API keys and sign-ins are refused and its mailboxes are no longer checked. Nothing is deleted."""
    if not store.org(wid):
        raise HTTPException(404, "No such organization.")
    store.set_suspended(wid, body.suspended, body.reason.strip())
    store.add_audit(admin["email"], "suspend" if body.suspended else "unsuspend", wid, body.reason.strip())
    return {"ok": True}


@router.post("/orgs/{wid}/reset-runs")
def reset_runs(wid: str, admin=Depends(admin_only)):
    if not store.org(wid):
        raise HTTPException(404, "No such organization.")
    store.reset_runs(wid, _month())
    store.add_audit(admin["email"], "reset runs", wid, _month())
    return {"ok": True}


@router.post("/orgs/{wid}/revoke-keys")
def revoke_keys(wid: str, admin=Depends(admin_only)):
    if not store.org(wid):
        raise HTTPException(404, "No such organization.")
    n = store.revoke_all_keys(wid)
    store.add_audit(admin["email"], "revoke all API keys", wid, f"{n} key(s)")
    return {"ok": True, "revoked": n}


@router.delete("/orgs/{wid}")
def delete_org(wid: str, confirm: str = "", admin=Depends(admin_only)):
    """Delete an organization and everything it owns, and cancel its subscription. `confirm` must repeat the organization's id."""
    o = store.org(wid)
    if not o:
        raise HTTPException(404, "No such organization.")
    if confirm != wid:
        raise HTTPException(400, "Type the organization id to confirm.")
    if (store.member(admin["id"]) or {}).get("workspace_id") == wid:
        raise HTTPException(400, "This is your own organization, the one you are signed in to. Delete it from the database tool instead.")
    billing_note = "no subscription"
    if o.get("helcim_subscription"):
        try:
            helcim.cancel_subscription(o["helcim_subscription"])
            billing_note = "Helcim subscription cancelled"
        except Exception as e:
            billing_note = f"COULD NOT cancel the Helcim subscription ({str(e)[:80]}): cancel it in Helcim"
    for c in store.connections(wid):
        runner.stop_connection(c["id"])
        push.remove(c["id"])
    people = [m["email"] for m in store.team(wid)]
    gone = store.delete_org(wid)
    store.add_audit(admin["email"], "delete organization", wid, f"{o.get('name')}; people: {', '.join(people)}; {gone['mailboxes']} mailbox(es); {billing_note}")
    return {"ok": True, "deleted": gone, "billing": billing_note}


@router.get("/users")
def users(q: str = "", limit: int = 100, offset: int = 0, admin=Depends(admin_only)):
    rows = store.admin_users(q[:80], max(1, min(limit, 300)), max(0, offset))
    return {"users": rows}


@router.delete("/users/{uid}")
def delete_user(uid: str, confirm: str = "", admin=Depends(admin_only)):
    """Remove one person from their organization (never its owner: delete the organization for that). `confirm` repeats their email."""
    u = store.user_by_email_id(uid)
    if not u:
        raise HTTPException(404, "No such person.")
    if confirm.lower() != u["email"].lower():
        raise HTTPException(400, "Type the person's email to confirm.")
    m = store.member(uid)
    if not m or m["role"] == "owner":
        raise HTTPException(400, "This person owns an organization. Delete the organization instead.")
    store.remove_member(m["workspace_id"], uid)
    store.add_audit(admin["email"], "remove person", uid, u["email"])
    return {"ok": True}


@router.get("/audit")
def audit(admin=Depends(admin_only)):
    return {"audit": store.audit(200)}
