"""Microsoft 365 push: subscribe each mailbox to inbox changes, keep the subscriptions renewed, and turn
notifications into emails for the watches. Polling continues underneath as a slow safety net."""
import hmac
import secrets
import threading
import time
from typing import Any, Dict

from . import config, graph, store
from .runner import runner
from .sources import AuthError

CHECK_EVERY = 60          # how often the manager looks at subscriptions
RENEW_AT = 0.5            # renew once this fraction of the lifetime has passed


def allowed() -> bool:
    return config.BASE_URL.startswith("https://") or config.PUSH_ALLOW_HTTP


def ensure(conn: Dict[str, Any]) -> None:
    """Make sure this Microsoft mailbox has a live subscription (create or renew as needed)."""
    cid = conn["id"]
    if not allowed():
        store.set_delivery(cid, "polling", "Push needs WatchFox to run at a public https address.")
        return
    sub = store.subscription_for(cid)
    try:
        token = graph.token_for(conn)
        now = time.time()
        if sub and sub["expires"] > now:
            lifetime = max(sub["expires"] - sub["created"], 1)
            if (sub["expires"] - now) / lifetime > RENEW_AT:
                store.set_delivery(cid, "push", None)
                return
            data = graph.renew_subscription(token, sub["id"])
            if data:
                store.renewed_subscription(sub["id"], graph.parse_iso(data["expirationDateTime"]))
                store.set_delivery(cid, "push", None)
                return
        state = secrets.token_urlsafe(24)
        data = graph.create_subscription(token, state)
        store.save_subscription(data["id"], cid, state, graph.parse_iso(data["expirationDateTime"]))
        store.set_delivery(cid, "push", None)
        runner.start_connection(cid) if store.watches_for_connection(cid) else None
    except AuthError as e:
        runner.flag(conn, str(e))
    except Exception as e:
        store.drop_subscription(cid)
        store.set_delivery(cid, "polling", f"Push is not active ({str(e)[:160]}). Checking every minute instead.")


def remove(cid: str) -> None:
    sub = store.subscription_for(cid)
    conn = store.connection(cid)
    if sub and conn:
        try:
            graph.delete_subscription(graph.token_for(conn), sub["id"])
        except Exception:
            pass
    store.drop_subscription(cid)


def manage_forever() -> None:
    while True:
        for conn in store.all_connections("m365"):
            if conn["status"] == "active" and store.watches_for_connection(conn["id"]):
                try:
                    ensure(conn)
                except Exception:
                    pass
        time.sleep(CHECK_EVERY)


def start() -> None:
    threading.Thread(target=manage_forever, daemon=True, name="push-manager").start()


# ---------------------------------------------------------------- what Microsoft sends us
def handle_notifications(payload: Dict[str, Any]) -> int:
    """Queue each new message. Only trusts notifications carrying the clientState we gave that subscription."""
    queued = 0
    for n in payload.get("value", []):
        sub = store.subscription(n.get("subscriptionId", ""))
        if not sub or not hmac.compare_digest(str(n.get("clientState", "")), sub["client_state"]):
            continue
        mid = (n.get("resourceData") or {}).get("id")
        if mid and n.get("changeType") in ("created", None):
            runner.ingest_q.put((sub["connection_id"], mid))
            queued += 1
    return queued


def handle_lifecycle(payload: Dict[str, Any]) -> None:
    for n in payload.get("value", []):
        sub = store.subscription(n.get("subscriptionId", ""))
        if not sub or not hmac.compare_digest(str(n.get("clientState", "")), sub["client_state"]):
            continue
        conn = store.connection(sub["connection_id"])
        if not conn:
            continue
        event = n.get("lifecycleEvent")
        if event == "missed":
            threading.Thread(target=_sweep_now, args=(conn["id"],), daemon=True).start()
        elif event in ("reauthorizationRequired", "subscriptionRemoved"):
            if event == "subscriptionRemoved":
                store.drop_subscription(conn["id"])
            threading.Thread(target=ensure, args=(conn,), daemon=True).start()


def _sweep_now(cid: str) -> None:
    """Notifications were missed: restart the mailbox loop so it checks immediately."""
    runner.stop_connection(cid)
    runner.start_connection(cid)
