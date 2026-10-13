"""Accounts: email verification, password reset, sign in with GitHub/Google/Microsoft, the organization and its team,
plans and usage."""
import hashlib
import re
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import abuse, billing, config, limiter, mailer, oauth, store, vault
from .auth import COOKIE, current_user, require_owner, set_session

router = APIRouter()
EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ip(request: Request) -> str:
    return abuse.client_ip(request)


# ---------------------------------------------------------------- email verification
def send_verification(uid: str, email: str) -> Optional[str]:
    link = f"{config.BASE_URL}/web/verify?token={vault.sign({'p': 'verify', 'uid': uid}, 3 * 86400)}"
    mailer.send(email, "Confirm your email for WatchFox",
                f"Welcome to WatchFox.\n\nConfirm your email address to start using the API:\n{link}\n\nThe link works for 3 days. "
                "If you did not create this account, ignore this message.")
    return link if config.DEV_LINKS else None


@router.get("/web/verify")
def verify_email(token: str):
    d = vault.verify(token)
    if not d or d.get("p") != "verify" or not store.user(d.get("uid", "")):
        return RedirectResponse("/#/login?notice=bad-link")
    store.set_verified(d["uid"])
    return RedirectResponse("/#/dashboard?notice=verified")


@router.post("/web/verify/resend")
def resend_verification(user=Depends(current_user)):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    if not user["uid"]:
        raise HTTPException(400, "Sign in to the website to do this.")
    if user["verified"]:
        return {"ok": True, "already": True}
    limiter.hit("verify", user["uid"], 5, 3600)
    link = send_verification(user["uid"], user["email"])
    return {"ok": True, **({"dev_link": link} if link else {})}


# ---------------------------------------------------------------- password reset and change
class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


class PasswordIn(BaseModel):
    current: str
    password: str


@router.post("/web/forgot")
def forgot(body: ForgotIn, request: Request):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    limiter.hit("forgot", _ip(request), 8, 3600)
    email = body.email.strip().lower()[:254]
    u = store.user_by_email(email) if EMAIL_RE.match(email) else None
    out: Dict[str, Any] = {"ok": True, "message": "If that address has an account, we have sent a link to reset the password."}
    if u:
        limiter.hit("forgot-user", u["id"], 5, 3600)
        link = f"{config.BASE_URL}/#/reset?token={vault.sign({'p': 'reset', 'uid': u['id'], 'h': u['pw_hash'][-16:]}, 3600)}"
        mailer.send(email, "Reset your WatchFox password", f"Choose a new password here:\n{link}\n\nThe link works for one hour and only once. "
                    "If you did not ask for this, ignore this message: your password has not changed.")
        if config.DEV_LINKS:
            out["dev_link"] = link
    return out


@router.post("/web/reset")
def reset(body: ResetIn, request: Request, response: Response):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    limiter.hit("reset", _ip(request), 20, 3600)
    d = vault.verify(body.token)
    u = store.user_by_email_id(d["uid"]) if d and d.get("p") == "reset" else None
    if not u or u["pw_hash"][-16:] != d.get("h"):
        raise HTTPException(400, "This reset link is no longer valid. Ask for a new one.")
    if not 10 <= len(body.password) <= 200:
        raise HTTPException(400, "Use a password of 10 to 200 characters.")
    store.set_password(u["id"], vault.hash_password(body.password))
    store.set_verified(u["id"])                      # they received the link at that address
    set_session(response, u["id"])
    return {"ok": True}


@router.post("/web/password")
def change_password(body: PasswordIn, user=Depends(current_user)):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    if not user["uid"]:
        raise HTTPException(400, "Sign in to the website to do this.")
    row = store.one("SELECT * FROM users WHERE id=?", (user["uid"],))
    if not vault.verify_password(body.current, row["pw_hash"]):
        raise HTTPException(400, "Your current password is not right.")
    if not 10 <= len(body.password) <= 200:
        raise HTTPException(400, "Use a password of 10 to 200 characters.")
    store.set_password(user["uid"], vault.hash_password(body.password))
    return {"ok": True}


# ---------------------------------------------------------------- sign in with GitHub, Google, Microsoft
@router.get("/web/providers")
def sign_in_options():
    return {"oauth": [{"id": p, "label": oauth.LABELS[p]} for p in oauth.configured()], "password": config.PASSWORD_LOGIN, "dev_links": config.DEV_LINKS}


def _fail(why: str):
    r = RedirectResponse(f"/#/login?error={quote(why)}")
    r.delete_cookie("wf_oauth", path="/")
    return r


@router.get("/auth/login/{provider}/start")
def oauth_start(provider: str, request: Request, invite: str = ""):
    limiter.hit("oauth", _ip(request), 40, 600)
    if provider not in oauth.configured():
        return _fail(f"{oauth.LABELS.get(provider, provider)} sign-in is not set up on this server.")
    state, (verifier, challenge) = secrets.token_urlsafe(24), oauth.new_pkce()
    r = RedirectResponse(oauth.authorize_url(provider, state, challenge))
    r.set_cookie("wf_oauth", vault.sign({"s": state, "v": verifier, "p": provider, "inv": invite[:600]}, 600), max_age=600, httponly=True,
                 samesite="lax", secure=config.BASE_URL.startswith("https://"), path="/")
    return r


@router.get("/auth/login/{provider}/callback")
def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    ck = vault.verify(request.cookies.get("wf_oauth", "") or "")
    if error or not code or not ck or ck.get("p") != provider or not secrets.compare_digest(str(ck.get("s")), state):
        return _fail("Sign-in was cancelled or expired. Try again.")
    try:
        prof = oauth.fetch_profile(provider, code, ck["v"])
        if not prof["email"] or "@" not in prof["email"]:
            raise ValueError("no email")
    except Exception:
        return _fail(f"{oauth.LABELS[provider]} did not give us an email address we can use.")
    invite = _invite(ck["inv"]) if ck.get("inv") else None
    if ck.get("inv") and not invite:
        return _fail("This invitation has expired or was cancelled. Ask for a new one.")
    ident = store.identity(provider, prof["subject"])
    uid = ident["user_id"] if ident and store.user(ident["user_id"]) else ""
    existing = None if uid else store.user_by_email(prof["email"])
    if existing and not prof["verified"]:
        return _fail("That email already belongs to an account. Sign in with the way you used before (GitHub or Google).")
    if existing:
        uid = existing["id"]                           # the provider vouches for the address, so this is the same person
    if invite and uid and (store.member(uid) or {}).get("workspace_id") != invite["workspace_id"]:
        return _fail("You already have a WatchFox account, and a person can belong to only one organization. Use a different sign-in for this invitation.")
    if not uid:
        inv = invite or (store.invite_for_email(prof["email"]) if prof["verified"] else None)
        if not inv:
            try:
                abuse.check_new_account(_ip(request), prof["email"], prof.get("age_days"))
            except abuse.Refused as e:
                return _fail(str(e))
        name = f"{prof['email'].split('@')[0]}'s organization"
        # signing in through a provider is the proof of identity; accounts need no password
        uid = store.create_user(prof["email"], vault.hash_password(secrets.token_urlsafe(24)), verified=True,
                                workspace=inv["workspace_id"] if inv else "", name=name)
        if inv:
            store.accept_invite(inv["id"])
    elif invite:
        store.accept_invite(invite["id"])
    store.set_verified(uid)
    if prof.get("name"):
        store.set_display_name(uid, prof["name"])
    store.add_identity(provider, prof["subject"], uid)
    r = RedirectResponse("/#/dashboard")
    set_session(r, uid)
    r.delete_cookie("wf_oauth", path="/")
    return r


# ---------------------------------------------------------------- who am I, the organization and the team
@router.get("/web/me")
def me(request: Request, user=Depends(current_user)):
    o = store.org(user["id"]) or {}
    from . import admin
    return {"id": user["id"], "email": user["email"], "role": user["role"], "verified": user["verified"],
            "org": {"id": user["id"], "name": o.get("name")}, "plan": billing.summary(user["id"]),
            "is_admin": admin.admin_of(request) is not None, "suspended": bool(o.get("suspended"))}


class OrgIn(BaseModel):
    name: str


@router.get("/v1/org")
def get_org(user=Depends(current_user)):
    o = store.org(user["id"]) or {}
    return {"id": user["id"], "name": o.get("name"), "plan": billing.summary(user["id"])}


@router.patch("/v1/org")
def rename_org(body: OrgIn, user=Depends(current_user)):
    require_owner(user)
    name = body.name.strip()[:80]
    if not name:
        raise HTTPException(400, "Give the organization a name.")
    store.set_org_name(user["id"], name)
    return {"ok": True, "name": name}


class InviteIn(BaseModel):
    email: str


@router.get("/v1/team")
def get_team(user=Depends(current_user)):
    members = store.team(user["id"])
    pending = store.invites(user["id"])
    return {"members": [{"id": m["id"], "email": m["email"], "role": m["role"], "verified": bool(m["verified"]), "you": m["id"] == user["uid"]} for m in members],
            "invites": pending, "limit": billing.limit(user["id"], "members"), "can_manage": user["role"] == "owner"}


@router.post("/v1/team/invites", status_code=201)
def invite(body: InviteIn, user=Depends(current_user)):
    require_owner(user)
    email = body.email.strip().lower()[:254]
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    limiter.hit("invite", user["id"], 30, 3600)
    if len(store.team(user["id"])) + len(store.invites(user["id"])) >= billing.limit(user["id"], "members"):
        raise HTTPException(400, f"Your plan allows {billing.limit(user['id'], 'members')} team members. Upgrade for more.")
    if store.user_by_email(email):
        raise HTTPException(409, "That address already has a WatchFox account. A person can belong to only one organization, so invite a different address.")
    if any(i["email"] == email for i in store.invites(user["id"])):
        raise HTTPException(409, "That person has already been invited.")
    iid = store.add_invite(user["id"], email, hashlib.sha256(secrets.token_bytes(16)).hexdigest(), user["uid"], 7 * 86400)
    link = _invite_link(iid, time.time() + 7 * 86400)
    org = (store.org(user["id"]) or {}).get("name", "an organization")
    emailed = config.EMAIL_ENABLED or config.DEV_LINKS
    if emailed:
        who = sender_name(user["uid"], user["email"])
        mailer.send(email, f"{who.replace(' via WatchFox', '')} invited you to {org} on WatchFox",
                    f"{user['email']} invited you to join {org} on WatchFox as a developer.\n\nAccept the invitation by signing in with GitHub, Google or Microsoft:\n{link}\n\nThe link works for 7 days.\n\nYou can reply to this email to reach {user['email']}.",
                    from_name=who, reply_to=user["email"])
    emailed = config.EMAIL_ENABLED
    return {"id": iid, "email": email, "link": link, "emailed": emailed}


@router.get("/v1/team/invites/{iid}/link")
def invite_link(iid: str, user=Depends(current_user)):
    """The link for a pending invitation, so the owner can copy it again."""
    require_owner(user)
    row = store.invite_by_id(iid)
    if not row or row["workspace_id"] != user["id"]:
        raise HTTPException(404, "No such invitation.")
    return {"link": _invite_link(iid, row["expires"])}


@router.delete("/v1/team/invites/{iid}")
def cancel_invite(iid: str, user=Depends(current_user)):
    require_owner(user)
    if not store.revoke_invite(user["id"], iid):
        raise HTTPException(404, "No such invitation.")
    return {"ok": True}


@router.delete("/v1/team/members/{uid}")
def remove_member(uid: str, user=Depends(current_user)):
    require_owner(user)
    if uid == user["uid"]:
        raise HTTPException(400, "The owner cannot be removed.")
    if not store.remove_member(user["id"], uid):
        raise HTTPException(404, "No such team member.")
    return {"ok": True}


class JoinIn(BaseModel):
    token: str
    password: str


def _invite(token: str):
    d = vault.verify(token)                                   # links are signed, so the owner can fetch the same link again later
    if d and d.get("i"):
        return store.invite_by_id(d["i"])
    return store.invite_by_token(hashlib.sha256(token.encode()).hexdigest())


def sender_name(uid: str, email: str) -> str:
    """\"Sam via WatchFox\": who is inviting, as the person's own name from their sign-in provider (or the start of their email)."""
    u = store.user(uid) if uid else None
    name = re.sub(r"[\x00-\x1f<>\"\\]", "", (u or {}).get("display_name") or "").strip()[:40] or email.split("@")[0].replace(".", " ").title()
    return f"{name} via WatchFox"


def _invite_link(iid: str, expires: float) -> str:
    return f"{config.BASE_URL}/#/join?token={vault.sign({'i': iid}, max(60, int(expires - time.time())))}"


@router.get("/web/invite")
def invite_info(token: str, request: Request):
    limiter.hit("invite-info", _ip(request), 60, 600)
    i = _invite(token)
    if not i:
        raise HTTPException(404, "This invitation has expired or was cancelled. Ask for a new one.")
    return {"email": i["email"], "org": i["org_name"], "oauth": [p for p in oauth.configured()], "password": config.PASSWORD_LOGIN}


@router.post("/web/join")
def join(body: JoinIn, request: Request, response: Response):
    if not config.PASSWORD_LOGIN:
        raise HTTPException(404, "Not found.")
    limiter.hit("join", _ip(request), 20, 3600)
    i = _invite(body.token)
    if not i:
        raise HTTPException(404, "This invitation has expired or was cancelled. Ask for a new one.")
    if not 10 <= len(body.password) <= 200:
        raise HTTPException(400, "Use a password of 10 to 200 characters.")
    if store.user_by_email(i["email"]):
        raise HTTPException(409, "That address already has an account. Sign in instead.")
    uid = store.create_user(i["email"], vault.hash_password(body.password), verified=True, workspace=i["workspace_id"])
    store.accept_invite(i["id"])
    set_session(response, uid)
    return {"id": uid, "email": i["email"]}


# ---------------------------------------------------------------- plans and usage
@router.get("/v1/plans")
def plans():
    return {"plans": billing.catalogue(), "run": "One run is one email checked by one watch, whether or not it is caught."}


@router.get("/v1/plan")
def my_plan(user=Depends(current_user)):
    return billing.summary(user["id"])


@router.get("/v1/usage")
def usage(user=Depends(current_user)):
    s = billing.summary(user["id"])
    return {k: s[k] for k in ("plan", "plan_name", "runs_used", "runs_limit", "runs_remaining", "resets_at")}


class UpgradeIn(BaseModel):
    plan: str
    billing: str = "monthly"


@router.post("/v1/plan/upgrade")
def upgrade(body: UpgradeIn, user=Depends(current_user)):
    """Records the request. Payment is not connected yet, so a person at WatchFox switches the plan after contacting you."""
    require_owner(user)
    if body.plan not in billing.PLANS or body.plan == "kit" or body.billing not in ("monthly", "annual"):
        raise HTTPException(400, "Choose the Tracker or Alpha plan, billed monthly or annual.")
    limiter.hit("upgrade", user["id"], 5, 3600)
    store.add_plan_request(user["id"], body.plan, body.billing, user["email"])
    return {"ok": True, "message": f"Thanks. We have your request for {billing.PLANS[body.plan]['name']} ({body.billing}). We will contact {user['email']} to activate it."}
