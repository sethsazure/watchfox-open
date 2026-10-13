import datetime as dt
from typing import Any, Callable, Dict, Iterator

import httpx

from .. import billing, config, store
from ..items import Item
from . import AuthError, Source

SCOPES = ["Mail.Read"]
_flows: Dict[str, Any] = {}


def configured() -> bool:
    return bool(config.MS_CLIENT_ID)


def _app(cache=None):
    import msal

    authority = f"https://login.microsoftonline.com/{config.MS_TENANT}"
    if config.MS_CLIENT_SECRET:     # normal setup: a web server app with a client secret
        return msal.ConfidentialClientApplication(config.MS_CLIENT_ID, client_credential=config.MS_CLIENT_SECRET,
                                                  authority=authority, token_cache=cache)
    return msal.PublicClientApplication(config.MS_CLIENT_ID, authority=authority, token_cache=cache)


def _cache(serialized: str = ""):
    import msal

    c = msal.SerializableTokenCache()
    if serialized:
        c.deserialize(serialized)
    return c


def start(link: dict) -> str:
    flow = _app().initiate_auth_code_flow(SCOPES, redirect_uri=f"{config.BASE_URL}/auth/m365/callback", prompt="select_account")
    _flows[flow["state"]] = (flow, link)
    return flow["auth_uri"]


def finish(params: Dict[str, str]):
    """Returns (connection_id, email, link). A reconnect (link['c']) refreshes the same connection."""
    entry = _flows.pop(params.get("state", ""), None)
    if entry is None:
        raise ValueError("This sign-in link expired. Start again.")
    flow, link = entry
    cache = _cache()
    result = _app(cache).acquire_token_by_auth_code_flow(flow, params)
    if "error" in result:
        raise ValueError(result.get("error_description") or result["error"])
    claims = result.get("id_token_claims") or {}
    email = claims.get("preferred_username") or claims.get("email") or "unknown"
    if link.get("c"):
        existing = store.connection(link["c"], link["uid"])
        if existing and existing["email"].lower() != email.lower():
            raise ValueError(f"Please sign in as {existing['email']}, the mailbox that is being reconnected.")
    elif not any(c["email"].lower() == email.lower() and c["provider"] == "m365" for c in store.connections(link["uid"])) \
            and len(store.connections(link["uid"])) >= billing.limit(link["uid"], "mailboxes"):
        raise ValueError("This account has reached its mailbox limit, so this mailbox cannot be added. Ask the app owner to upgrade.")
    cid = store.upsert_connection(link["uid"], "m365", email, cache.serialize(), link.get("m"), link.get("c") or "")
    return cid, email, link


def admin_consent_url(redirect_uri: str) -> str:
    return (f"https://login.microsoftonline.com/{config.MS_TENANT}/adminconsent?client_id={config.MS_CLIENT_ID}"
            f"&redirect_uri={redirect_uri}")


class M365(Source):
    id = "m365"
    label = "Microsoft 365"
    interval = 60
    method = "oauth"

    def check(self) -> None:
        if not configured():
            raise ValueError("Microsoft 365 is not available on this server.")

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        from .. import graph

        conn = store.connection(self.cfg["connection_id"])
        token = graph.token_for(conn)
        since = dt.datetime.fromtimestamp(float(self.cfg["since"]), dt.timezone.utc)
        url = f"{config.GRAPH_URL}/me/mailFolders/inbox/messages"
        params = {"$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}", "$orderby": "receivedDateTime desc",
                  "$top": str(min(limit, 50)), "$select": graph.SELECT}
        headers = {"Authorization": f"Bearer {token}", **graph.TEXT}
        count = 0
        with httpx.Client(timeout=30) as http:
            while url and count < limit:
                r = http.get(url, params=params, headers=headers)
                if r.status_code == 401:
                    raise AuthError("The Microsoft sign-in was rejected. The person needs to connect their mailbox again.")
                r.raise_for_status()
                data = r.json()
                for m in data.get("value", []):
                    count += 1
                    if not seen(m["id"]):
                        yield graph.message_to_item(m)
                url, params = data.get("@odata.nextLink"), None
