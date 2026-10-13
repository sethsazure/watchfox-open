import base64
import json
import time
from typing import Any, Callable, Dict, Iterator

from .. import config, store, vault
from ..items import Item, html_to_text
from . import Source

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/userinfo.email", "openid"]
_flows: Dict[str, Any] = {}


def configured() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def _flow():
    from google_auth_oauthlib.flow import Flow

    client = {"web": {"client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
                      "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}
    return Flow.from_client_config(client, SCOPES, redirect_uri=f"{config.BASE_URL}/auth/gmail/callback")


def start(link: dict) -> str:
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _flows[state] = (flow, link)
    return url


def finish(params: Dict[str, str]):
    """Returns (connection_id, email, link)."""
    entry = _flows.pop(params.get("state", ""), None)
    if entry is None:
        raise ValueError("This sign-in link expired. Start again.")
    flow, link = entry
    flow.fetch_token(code=params["code"])
    creds = flow.credentials
    email = _service(creds).users().getProfile(userId="me").execute()["emailAddress"]
    cid = store.upsert_connection(link["uid"], "gmail", email, json.loads(creds.to_json()))
    return cid, email, link


def _service(creds):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _body(payload: Dict[str, Any]) -> str:
    plain, html = [], []

    def walk(p):
        data = (p.get("body") or {}).get("data")
        mt = p.get("mimeType", "")
        if data and mt in ("text/plain", "text/html"):
            txt = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if mt == "text/plain" else html).append(txt)
        for sub in p.get("parts") or []:
            walk(sub)

    walk(payload)
    return "\n".join(plain) if plain else html_to_text("\n".join(html))


class Gmail(Source):
    id = "gmail"
    label = "Gmail"
    interval = 60

    def check(self) -> None:
        if not configured():
            raise ValueError("Gmail is not available on this server.")

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(self.cfg["secret"], SCOPES)
        svc = _service(creds)
        try:
            resp = svc.users().messages().list(userId="me", q=f"in:inbox after:{int(self.cfg['since'])}", maxResults=limit).execute()
            for m in resp.get("messages", []):
                if seen(m["id"]):
                    continue
                full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
                h = {x["name"].lower(): x["value"] for x in full["payload"].get("headers", [])}
                files = []

                def names(p):
                    if p.get("filename"):
                        files.append(p["filename"])
                    for s in p.get("parts") or []:
                        names(s)

                names(full["payload"])
                yield Item(id=m["id"], title=h.get("subject", ""), sender=h.get("from", ""),
                           text=_body(full["payload"]) or full.get("snippet", ""), kind="email",
                           received=int(full.get("internalDate", time.time() * 1000)) / 1000,
                           ref=f"gmail:{m['id']}", attachments=files)
        finally:
            if creds.token:   # the access token may have been refreshed
                store.save_connection_secret(self.cfg["connection_id"], json.loads(creds.to_json()))
