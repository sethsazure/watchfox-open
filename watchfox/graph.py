"""Microsoft Graph: sign-in tokens, mailbox change subscriptions (push) and reading a message."""
import datetime as dt
import time
from typing import Any, Dict, Optional

import httpx

from . import config, store, vault
from .items import Item, html_to_text, is_bulk
from .sources import AuthError
from .sources import m365 as m365_auth

SELECT = "id,subject,from,receivedDateTime,body,bodyPreview,hasAttachments,internetMessageHeaders"
TEXT = {"Prefer": 'outlook.body-content-type="text"'}
LIFETIMES_MIN = [4200, 2880, 1440]      # Outlook message subscriptions are limited to just under 3 days


def token_for(conn: Dict[str, Any]) -> str:
    """A valid access token for this connection, refreshed from the stored sign-in. AuthError if that no longer works."""
    cache = m365_auth._cache(vault.decrypt(conn["secret"]))
    app = m365_auth._app(cache)
    accts = app.get_accounts()
    result = app.acquire_token_silent(m365_auth.SCOPES, account=accts[0]) if accts else None
    if not result or "access_token" not in result:
        raise AuthError("The Microsoft sign-in expired or was revoked. The person needs to connect their mailbox again.")
    if cache.has_state_changed:
        store.save_connection_secret(conn["id"], cache.serialize())
    return result["access_token"]


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def parse_iso(s: str) -> float:
    s = s.strip().rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = head + "." + frac[:6].ljust(6, "0")
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


def message_to_item(m: Dict[str, Any], ref_prefix: str = "m365") -> Item:
    body = m.get("body") or {}
    text = body.get("content", "")
    if body.get("contentType") == "html":
        text = html_to_text(text)
    frm = (m.get("from") or {}).get("emailAddress") or {}
    headers = {h.get("name", ""): h.get("value", "") for h in (m.get("internetMessageHeaders") or [])}
    return Item(id=m["id"], title=m.get("subject") or "", bulk=is_bulk(headers, m.get("subject") or "", text), sender=f"{frm.get('name', '')} <{frm.get('address', '')}>".strip(),
                text=text or m.get("bodyPreview", ""), kind="email",
                received=parse_iso(m["receivedDateTime"]), ref=f"{ref_prefix}:{m['id']}",
                attachments=["(attachment)"] if m.get("hasAttachments") else [])


def get_message(token: str, message_id: str) -> Item:
    r = httpx.get(f"{config.GRAPH_URL}/me/messages/{message_id}", params={"$select": SELECT}, timeout=20,
                  headers={"Authorization": f"Bearer {token}", **TEXT})
    r.raise_for_status()
    return message_to_item(r.json())


def create_subscription(token: str, client_state: str) -> Dict[str, Any]:
    """Ask Graph to call us when mail arrives in the inbox. Graph checks our address before it agrees."""
    last: Optional[Exception] = None
    for minutes in LIFETIMES_MIN:
        body = {"changeType": "created", "notificationUrl": f"{config.BASE_URL}/hooks/m365/notify",
                "lifecycleNotificationUrl": f"{config.BASE_URL}/hooks/m365/lifecycle",
                "resource": "me/mailFolders('inbox')/messages", "expirationDateTime": iso(time.time() + minutes * 60),
                "clientState": client_state}
        r = httpx.post(f"{config.GRAPH_URL}/subscriptions", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if r.status_code in (200, 201):
            return r.json()
        last = RuntimeError(f"Graph refused the subscription ({r.status_code}): {r.text[:200]}")
        if r.status_code != 400 or "expiration" not in r.text.lower():
            break
    raise last


def renew_subscription(token: str, sub_id: str) -> Optional[Dict[str, Any]]:
    """Extend a subscription. Returns None if Graph no longer knows it (it must be recreated)."""
    for minutes in LIFETIMES_MIN:
        r = httpx.patch(f"{config.GRAPH_URL}/subscriptions/{sub_id}", json={"expirationDateTime": iso(time.time() + minutes * 60)},
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        if r.status_code != 400:
            break
    raise RuntimeError(f"Graph would not renew the subscription ({r.status_code}): {r.text[:200]}")


def delete_subscription(token: str, sub_id: str) -> None:
    try:
        httpx.delete(f"{config.GRAPH_URL}/subscriptions/{sub_id}", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    except Exception:
        pass
