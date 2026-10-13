"""Sign in with GitHub, Google or Microsoft (OAuth 2.0 authorization code flow with PKCE).

Callback address to register with each provider: <WATCHFOX_URL>/auth/login/<github|google|microsoft>/callback
GitHub uses GITHUB_CLIENT_ID/SECRET, Google GOOGLE_CLIENT_ID/SECRET, Microsoft MS_CLIENT_ID/SECRET."""
import base64
import hashlib
import secrets
from typing import Any, Dict, List
from urllib.parse import urlencode

import time

import httpx

from . import config

LABELS = {"github": "GitHub", "google": "Google", "microsoft": "Microsoft"}


def _creds(p: str):
    return {"github": (config.GITHUB_CLIENT_ID, config.GITHUB_CLIENT_SECRET),
            "google": (config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET),
            "microsoft": (config.MS_CLIENT_ID, config.MS_CLIENT_SECRET)}[p]


def configured() -> List[str]:
    return [p for p in LABELS if all(_creds(p))]


def redirect_uri(p: str) -> str:
    return f"{config.BASE_URL}/auth/login/{p}/callback"


def new_pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(p: str, state: str, challenge: str) -> str:
    cid = _creds(p)[0]
    q: Dict[str, str] = {"client_id": cid, "redirect_uri": redirect_uri(p), "state": state, "response_type": "code",
                         "code_challenge": challenge, "code_challenge_method": "S256"}
    if p == "github":
        return "https://github.com/login/oauth/authorize?" + urlencode({**q, "scope": "read:user user:email"})
    if p == "google":
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({**q, "scope": "openid email profile", "prompt": "select_account"})
    return f"https://login.microsoftonline.com/{config.MS_TENANT}/oauth2/v2.0/authorize?" + urlencode(
        {**q, "scope": "openid email profile User.Read", "response_mode": "query", "prompt": "select_account"})


def fetch_profile(p: str, code: str, verifier: str) -> Dict[str, Any]:
    """Trade the code for the person's identity: {subject, email, verified, name}. `verified` is true only when the
    provider itself vouches that the person controls that email address."""
    cid, secret = _creds(p)
    data = {"client_id": cid, "client_secret": secret, "code": code, "redirect_uri": redirect_uri(p),
            "grant_type": "authorization_code", "code_verifier": verifier}
    url = {"github": "https://github.com/login/oauth/access_token", "google": "https://oauth2.googleapis.com/token",
           "microsoft": f"https://login.microsoftonline.com/{config.MS_TENANT}/oauth2/v2.0/token"}[p]
    with httpx.Client(timeout=20) as c:
        r = c.post(url, data=data, headers={"Accept": "application/json"})
        r.raise_for_status()
        tok = r.json().get("access_token")
        if not tok:
            raise ValueError("no access token")
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        if p == "github":
            u = c.get("https://api.github.com/user", headers=h).json()
            emails = c.get("https://api.github.com/user/emails", headers=h).json()
            primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
            if not primary:
                raise ValueError("no verified primary email on this GitHub account")
            age = None
            try:
                from datetime import datetime, timezone
                age = (time.time() - datetime.fromisoformat(str(u["created_at"]).replace("Z", "+00:00")).timestamp()) / 86400
            except (KeyError, ValueError):
                pass
            return {"subject": str(u["id"]), "email": primary["email"].lower(), "verified": True, "name": u.get("name") or u.get("login") or "", "age_days": age}
        if p == "google":
            u = c.get("https://openidconnect.googleapis.com/v1/userinfo", headers=h).json()
            return {"subject": str(u["sub"]), "email": u["email"].lower(), "verified": bool(u.get("email_verified")), "name": u.get("name") or ""}
        u = c.get("https://graph.microsoft.com/v1.0/me", headers=h).json()
        email = (u.get("mail") or u.get("userPrincipalName") or "").lower()
        # Microsoft does not promise that the address in a profile belongs to the person, so it is never treated as verified
        return {"subject": str(u["id"]), "email": email, "verified": False, "name": u.get("displayName") or ""}
