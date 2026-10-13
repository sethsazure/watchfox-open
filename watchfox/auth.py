"""Who is calling: an API key, or the website's session cookie. Both resolve to the caller's workspace."""
from typing import Any, Dict

from fastapi import HTTPException, Request, Response

from . import billing, config, limiter, store, vault

COOKIE = "wf_session"


def _not_suspended(wid: str, request: Request) -> None:
    """A suspended organization can still sign in to see why (/web/me), but nothing else works."""
    o = store.org(wid)
    if o and o.get("suspended") and request.url.path not in ("/web/me", "/web/logout"):
        raise HTTPException(403, "This account is suspended. Please contact hello@watchfox.run.")


def current_user(request: Request) -> Dict[str, Any]:
    """`id` is the workspace id (shared by every member of the organization), so everything downstream is scoped to the
    organization. `uid` is the person (empty for an API key)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        row = store.user_for_key(vault.hash_key(auth[7:].strip()))
        if row:
            limiter.hit("api", row["id"], config.API_PER_MIN, 60)           # one account cannot flood the service through its keys
            _not_suspended(row["id"], request)
            return {"id": row["id"], "uid": "", "email": row["email"], "role": "key", "verified": True}
        raise HTTPException(401, "Invalid API key. Create one in the dashboard under API keys.")
    tok = request.cookies.get(COOKIE)
    data = vault.verify(tok) if tok else None
    if data and data.get("uid"):
        u = store.user(data["uid"])
        if u:
            m = store.member(u["id"]) or {"workspace_id": u["id"], "role": "owner"}
            _not_suspended(m["workspace_id"], request)
            return {"id": m["workspace_id"], "uid": u["id"], "email": u["email"], "role": m["role"], "verified": bool(u["verified"])}
    raise HTTPException(401, "Missing API key. Send: Authorization: Bearer YOUR_API_KEY")


def require_verified(user: Dict[str, Any]) -> None:
    if not user.get("verified"):
        raise HTTPException(403, "Verify your email address first. We sent you a link; you can ask for another in Settings.")


def require_owner(user: Dict[str, Any]) -> None:
    if user.get("role") not in ("owner",):
        raise HTTPException(403, "Only the organization owner can do that.")


def require_run(user: Dict[str, Any]) -> None:
    """Refuse a test call when the month's runs are used up."""
    if not billing.allow(user["id"]):
        s = billing.summary(user["id"])
        raise HTTPException(429, f"You have used all {s['runs_limit']} runs on the {s['plan_name']} plan this month. "
                                 "Upgrade your plan, or wait until the 1st (UTC).", headers={"Retry-After": "3600"})


def set_session(response: Response, uid: str) -> None:
    response.set_cookie(COOKIE, vault.sign({"uid": uid}, 14 * 86400), max_age=14 * 86400, httponly=True,
                        samesite="lax", secure=config.BASE_URL.startswith("https://"), path="/")
