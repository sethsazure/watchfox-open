"""Helcim: card payments and subscriptions.

Cards are entered into Helcim's own secure form (HelcimPay.js), so WatchFox never sees or stores card numbers. We keep a Helcim
customer code and a subscription id per organization. Docs: https://devdocs.helcim.com"""
import hashlib
import hmac
import json
import secrets
import string
from typing import Any, Dict, List, Optional

import httpx

from . import config

BASE = "https://api.helcim.com/v2"
CURRENCY = "USD"


class HelcimError(Exception):
    """Helcim refused a request or could not be reached. The message is safe to show to the person."""


def configured() -> bool:
    return bool(config.HELCIM_API_TOKEN)


def _call(method: str, path: str, body: Optional[dict] = None, idempotency: bool = False) -> Any:
    headers = {"api-token": config.HELCIM_API_TOKEN, "accept": "application/json", "content-type": "application/json"}
    if idempotency:
        headers["idempotency-key"] = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(25))
    try:
        r = httpx.request(method, BASE + path, headers=headers, json=body, timeout=30)
    except httpx.TransportError:
        raise HelcimError("The payment service could not be reached. Please try again in a moment.")
    if r.status_code == 204:
        return None
    try:
        data = r.json()
    except ValueError:
        data = None
    if r.status_code >= 400:
        detail = ""
        if isinstance(data, dict):
            e = data.get("errors")
            detail = "; ".join(f"{k}: {v}" for k, v in e.items()) if isinstance(e, dict) else "; ".join(map(str, e)) if isinstance(e, list) else ""
        print(f"[helcim] {method} {path} -> HTTP {r.status_code} {detail[:200]}", flush=True)
        raise HelcimError("The payment service did not accept that request." + (f" ({detail[:160]})" if detail and r.status_code < 500 else ""))
    return data


def _data(r: Any) -> Any:
    """Some Helcim answers come wrapped as {timestamp, status, data}; others are the data itself."""
    if isinstance(r, dict) and "data" in r and ("timestamp" in r or "status" in r):
        return r["data"]
    return r


def terminal_id() -> Optional[int]:
    """The active terminal that processes in the plan currency."""
    for t in [t for t in (_data(_call("GET", "/card-terminals")) or []) if isinstance(t, dict)]:
        if t.get("status") == "ACTIVE" and t.get("currency") == CURRENCY:
            return int(t["id"])
    return None


def initialize(customer_code: str, existing: bool, contact_name: str, business_name: str, amount: float = 0) -> Dict[str, str]:
    """Open a secure card form. With an amount it is a real payment (the form shows the amount and a Subscribe button) that also saves
    the card; with no amount it is a $0 card check used to replace a saved card.
    If the customer was created by an earlier attempt, Helcim refuses to create it again, so the existing one is used."""
    paying = amount > 0
    base: Dict[str, Any] = {"paymentType": "purchase" if paying else "verify", "amount": round(amount, 2) if paying else 0, "currency": CURRENCY,
                            "paymentMethod": "cc", "setAsDefaultPaymentMethod": 1, "confirmationScreen": paying, "language": "en",
                            "customStyling": {"appearance": "light", "brandColor": "0A0A0A", "cornerRadius": "rounded",
                                              **({"ctaButtonText": "subscribe"} if paying else {})}}
    tid = terminal_id()
    if tid:
        base["terminalId"] = tid
    attempts = [{"customerCode": customer_code}] if existing else [
        {"customerRequest": {"customerCode": customer_code, "contactName": contact_name[:100] or "WatchFox customer", "businessName": business_name[:100]}},
        {"customerCode": customer_code}]
    last: Optional[HelcimError] = None
    for extra in attempts:
        try:
            r = _call("POST", "/helcim-pay/initialize", {**base, **extra})
        except HelcimError as e:
            last = e
            continue
        if r and r.get("checkoutToken") and r.get("secretToken"):
            return {"checkoutToken": r["checkoutToken"], "secretToken": r["secretToken"]}
    raise last or HelcimError("The payment service did not start the secure card form.")


def find_customer(code: str) -> Optional[Dict[str, Any]]:
    rows = [c for c in (_data(_call("GET", f"/customers?customerCode={code}")) or []) if isinstance(c, dict)]
    return next((c for c in rows if str(c.get("customerCode", "")).upper() == code.upper()), None)


def default_card(customer_id: Any) -> Optional[Dict[str, str]]:
    """The customer's default saved card as {last4, expiry}, or None if there is no card."""
    cards = [c for c in (_data(_call("GET", f"/customers/{int(customer_id)}/cards")) or []) if isinstance(c, dict)]
    c = next((c for c in cards if c.get("default")), cards[0] if cards else None)
    if not c:
        return None
    return {"last4": str(c.get("cardF6L4", ""))[-4:], "expiry": str(c.get("cardExpiry", ""))}


def latest_transaction(customer_code: str, since: float, kind: str = "verify") -> Dict[str, Any]:
    """The newest approved card transaction of this kind (verify or purchase) for this customer made after `since` (a Unix time). Helcim's dates are Mountain Time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    rows = _data(_call("GET", f"/card-transactions?customerCode={customer_code}&limit=10")) or []
    for t in sorted([t for t in rows if isinstance(t, dict)], key=lambda t: str(t.get("dateCreated", "")), reverse=True):
        try:
            made = datetime.strptime(str(t.get("dateCreated"))[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/Edmonton")).timestamp()
        except ValueError:
            continue
        if made >= since - 60 and t.get("status") == "APPROVED" and t.get("type") == kind \
                and str(t.get("customerCode", "")).upper() == customer_code.upper():
            return t
    return {}


def customer_transactions(customer_code: str) -> List[Dict[str, Any]]:
    """Every approved payment or refund on this customer, newest first. Read from Helcim each time; WatchFox keeps no copy."""
    rows = _data(_call("GET", f"/card-transactions?customerCode={customer_code}&limit=100")) or []
    keep = [t for t in rows if isinstance(t, dict) and t.get("status") == "APPROVED" and t.get("type") in ("purchase", "refund", "reverse")
            and str(t.get("customerCode", "")).upper() == customer_code.upper()]
    return sorted(keep, key=lambda t: str(t.get("dateCreated", "")), reverse=True)


def hash_matches(data: Any, given_hash: str, secret_token: str) -> bool:
    """Helcim signs the transaction data it sends to the browser: sha256(JSON(data) + secretToken)."""
    canonical = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    return hmac.compare_digest(hashlib.sha256((canonical + secret_token).encode()).hexdigest(), (given_hash or "").lower())


def card_transaction(transaction_id: Any) -> Dict[str, Any]:
    r = _data(_call("GET", f"/card-transactions/{int(transaction_id)}"))
    return r if isinstance(r, dict) else {}


def plans() -> List[Dict[str, Any]]:
    return [p for p in (_data(_call("GET", "/payment-plans")) or []) if isinstance(p, dict)]


def create_plan(name: str, amount: float, period: str, terminal: Optional[int]) -> Dict[str, Any]:
    p = {"name": name, "description": f"WatchFox {name}", "type": "subscription", "status": "active", "recurringAmount": amount,
         "billingPeriod": period, "billingPeriodIncrements": 1, "dateBilling": "Sign-up", "termType": "forever",
         "paymentMethod": "card", "taxType": "no_tax"}
    if terminal:
        p["cardTerminalId"] = terminal
    r = _call("POST", "/payment-plans", {"paymentPlans": [p]})
    return ((r or {}).get("data") or [{}])[0]


def create_subscription(plan_id: int, customer_code: str, amount: float, activate: str) -> Dict[str, Any]:
    r = _call("POST", "/subscriptions", {"subscriptions": [{"dateActivated": activate, "paymentPlanId": plan_id, "customerCode": customer_code,
                                                              "recurringAmount": amount, "paymentMethod": "card"}]}, idempotency=True)
    return ((r or {}).get("data") or [{}])[0]


def subscription(sub_id: Any) -> Optional[Dict[str, Any]]:
    try:
        r = _data(_call("GET", f"/subscriptions/{int(sub_id)}?includeSubObjects=true"))
        return r if isinstance(r, dict) else None
    except HelcimError:
        return None


def cancel_subscription(sub_id: Any) -> None:
    _call("DELETE", f"/subscriptions/{int(sub_id)}")


latest_verify = latest_transaction          # the earlier name
