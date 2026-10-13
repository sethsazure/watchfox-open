"""Paying for a plan: Helcim checkout, subscriptions, cancelling, and keeping the plan in step with what Helcim charges."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import billing, config, helcim, limiter, store
from .auth import current_user, require_owner, require_verified

router = APIRouter()
GRACE_DAYS = 7                       # a failed payment keeps the plan this long while the card is fixed


def plan_name(plan: str, period: str) -> str:
    return f"WatchFox {billing.PLANS[plan]['name']} {period}"


def price(plan: str, period: str, owner_email: str) -> float:
    if owner_email.lower() in config.HELCIM_TEST_EMAILS:        # the owner is trying the flow: charge a small test amount
        return config.HELCIM_TEST_AMOUNT
    return float(billing.PLANS[plan]["monthly" if period == "monthly" else "annual"])


def customer_code(wid: str) -> str:
    return "WF" + wid.split("_")[-1].upper()[:16]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _today_mountain() -> str:
    """Helcim dates are in Mountain Time. Using that date never lands in Helcim's future, so the first charge is not delayed."""
    return datetime.now(ZoneInfo("America/Edmonton")).date().isoformat()


def _add_period(day: str, period: str) -> str:
    """The date one billing period after `day` (YYYY-MM-DD): the first renewal when the first period was paid by card form."""
    import calendar
    from datetime import date

    d = date.fromisoformat(day)
    months = 12 if period == "annual" else 1
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def _need_helcim() -> None:
    if not helcim.configured():
        raise HTTPException(503, "Online payment is not set up yet. Use \"Request an upgrade\" and we will activate your plan.")


def _plan_id(plan: str, period: str) -> int:
    name = plan_name(plan, period)
    for p in helcim.plans():
        if p.get("name") == name and p.get("status", "active") == "active":
            return int(p["id"])
    raise HTTPException(503, "This plan is not available for online payment yet. Please contact us.")


class CheckoutIn(BaseModel):
    kind: str = "subscribe"                   # "subscribe" (choose a plan) or "card" (replace the saved card)
    plan: str = ""
    billing: str = "monthly"


class ConfirmIn(BaseModel):
    checkoutToken: str
    eventMessage: Any = None


def _status(wid: str) -> Dict[str, Any]:
    o = store.org(wid) or {}
    return {"configured": helcim.configured(), "plan": o.get("plan") or "kit", "billing": o.get("billing") or "monthly",
            "status": o.get("billing_status") or "none", "next_billing": o.get("billing_next"), "cancel_at": o.get("cancel_at"),
            "past_due": (o.get("billing_status") == "past_due"), "has_card": bool(o.get("helcim_customer")),
            "subscribed": bool(o.get("helcim_subscription"))}


@router.get("/v1/billing")
def billing_status(user=Depends(current_user)):
    return _status(user["id"])


@router.post("/v1/billing/checkout")
def checkout(body: CheckoutIn, user=Depends(current_user)):
    """Start the secure card form. Returns the checkoutToken that HelcimPay.js needs."""
    require_owner(user)
    require_verified(user)
    _need_helcim()
    limiter.hit("checkout", user["id"], 20, 3600)
    o = store.org(user["id"]) or {}
    if body.kind == "card":
        if not o.get("helcim_customer"):
            raise HTTPException(400, "There is no saved card yet. Choose a plan first.")
    else:
        if body.plan not in ("tracker", "alpha") or body.billing not in ("monthly", "annual"):
            raise HTTPException(400, "Choose the Tracker or Alpha plan, billed monthly or annual.")
        if o.get("helcim_subscription") and o.get("plan") == body.plan and (o.get("billing") or "monthly") == body.billing \
                and o.get("billing_status") in ("active", "past_due"):
            raise HTTPException(400, "You are already on that plan.")
        _plan_id(body.plan, "monthly" if body.billing == "monthly" else "yearly")        # fail early if the plan is missing in Helcim
    code = o.get("helcim_customer") or customer_code(user["id"])
    if body.kind != "card":
        try:                                           # a card is already saved (this or an earlier attempt): no need to enter it again
            cust = helcim.find_customer(code)
            card = helcim.default_card(cust["id"]) if cust else None
        except (helcim.HelcimError, ValueError, KeyError, TypeError):
            card = None
        if card:
            sid = secrets.token_urlsafe(16)[:22]
            store.save_billing_session(_token_hash(sid), user["id"], "", body.plan, body.billing, "saved", code)
            return {"mode": "saved_card", "checkoutToken": sid, "amount": price(body.plan, body.billing, user["email"]), "currency": helcim.CURRENCY,
                    "card": f"card ending {card['last4']}" if card["last4"] else "saved card"}
    amount = price(body.plan, body.billing, user["email"]) if body.kind != "card" else 0
    try:
        t = helcim.initialize(code, bool(o.get("helcim_customer")), user["email"], o.get("name") or "", amount)
    except helcim.HelcimError as e:
        raise HTTPException(502, str(e))
    kind = "purchase" if body.kind != "card" else "card"           # a plan is paid in the card form itself; replacing a card is a $0 check
    store.save_billing_session(_token_hash(t["checkoutToken"]), user["id"], t["secretToken"], body.plan, body.billing, kind, code)
    return {"checkoutToken": t["checkoutToken"], "amount": amount, "currency": helcim.CURRENCY, "kind": body.kind}


@router.post("/v1/billing/confirm")
def confirm(body: ConfirmIn, user=Depends(current_user)):
    """The browser reports that the card was saved. We do not trust it: Helcim is asked directly, and the card must belong to the customer
    this checkout was created for."""
    require_owner(user)
    _need_helcim()
    sess = store.billing_session(_token_hash(body.checkoutToken), user["id"])
    if not sess:
        raise HTTPException(400, "This payment form has expired. Start again.")
    paid_tx: Dict[str, Any] = {}
    if sess["kind"] == "saved":
        pass                                           # the customer code is derived from this organization, and it already has a card on file
    else:
        msg = body.eventMessage
        shape = f"{type(msg).__name__}" + (f" keys={list(msg)[:6]}" if isinstance(msg, dict) else f" length={len(msg)}" if isinstance(msg, str) else "")
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except ValueError:
                msg = {}
        data = (msg or {}).get("data") if isinstance(msg, dict) else None
        tx: Dict[str, Any] = {}
        try:
            want = "purchase" if sess["kind"] == "purchase" else "verify"
            if isinstance(data, dict) and data.get("transactionId"):
                tx = helcim.card_transaction(data["transactionId"])
            if not tx or str(tx.get("customerCode", "")).upper() != sess["customer_code"].upper() or tx.get("type") != want:
                # the browser message did not name a usable transaction: ask Helcim for this customer's newest one
                tx = helcim.latest_transaction(sess["customer_code"], sess["created"], want)
        except (helcim.HelcimError, ValueError, TypeError):
            raise HTTPException(502, "We could not confirm the card with the payment service. Nothing was charged.")
        if not tx:
            print(f"[helcim] confirm: no approved card check found (message was {shape})", flush=True)
            raise HTTPException(402, "The card was not accepted. Nothing was charged. Try another card.")
        if tx.get("status") != "APPROVED" or tx.get("type") != want or str(tx.get("customerCode", "")).upper() != sess["customer_code"].upper():
            raise HTTPException(402, "The card was not accepted. Nothing was charged. Try another card.")
        if want == "purchase" and abs(float(tx.get("amount") or 0) - price(sess["plan"], sess["billing"], user["email"])) > 0.005:
            print(f"[helcim] confirm: charged amount {tx.get('amount')} does not match the plan price", flush=True)
            raise HTTPException(402, "The payment did not match the plan price, so the plan was not changed. Please contact us.")
        paid_tx = tx
        if isinstance(data, dict) and not helcim.hash_matches(data, (msg or {}).get("hash", ""), sess["secret"]):
            print("[helcim] note: the browser response hash did not match; the transaction itself was confirmed with Helcim", flush=True)
    store.drop_billing_session(_token_hash(body.checkoutToken))
    wid, o = user["id"], store.org(user["id"]) or {}
    store.set_billing(wid, helcim_customer=sess["customer_code"])
    if sess["kind"] == "card":
        store.set_billing(wid, past_due_since=None, billing_status="active" if o.get("helcim_subscription") else "none")
        return {"ok": True, "message": "Your card was saved.", **_status(wid)}
    # subscribe
    plan, period = sess["plan"], sess["billing"]
    helcim_period = "monthly" if period == "monthly" else "yearly"
    amount = price(plan, period, user["email"])
    paid_now = bool(paid_tx)                                   # the card form took the first payment; Helcim's subscription starts at the first renewal
    activate = _add_period(_today_mountain(), period) if paid_now else _today_mountain()
    sub: Dict[str, Any] = {}
    try:
        sub = helcim.create_subscription(_plan_id(plan, helcim_period), sess["customer_code"], amount, activate)
    except (helcim.HelcimError, HTTPException) as e:
        if not paid_now:
            raise HTTPException(502, str(getattr(e, "detail", e)))
        print(f"[billing] paid but the subscription could not be created yet for {wid}; it will be retried", flush=True)
    failed = bool(sub) and (str(sub.get("hasFailedPayments", "")).lower() == "true" or any(
        any(w in str(p.get("status", "")).lower() for w in ("declin", "fail")) for p in sub.get("payments") or []))
    if failed:
        try:
            helcim.cancel_subscription(sub["id"])
        except helcim.HelcimError:
            pass
        raise HTTPException(402, "The first payment was declined, so the plan was not changed. Check the card details or try another card.")
    old = o.get("helcim_subscription")
    if old and sub and str(old) != str(sub.get("id")):
        try:
            helcim.cancel_subscription(old)            # moving between plans: the old subscription stops
        except helcim.HelcimError:
            print(f"[helcim] could not cancel the earlier subscription {old}", flush=True)
    store.set_plan(wid, plan, period)
    if sub:
        store.set_billing(wid, helcim_subscription=str(sub.get("id")), billing_status="active", billing_next=sub.get("dateBilling") or activate, cancel_at=None, past_due_since=None)
    else:                                                      # paid, but Helcim would not create the subscription: keep the plan and retry in the background
        store.set_billing(wid, helcim_subscription="pending", billing_status="pending_subscription", billing_next=activate, cancel_at=None, past_due_since=None)
    return {"ok": True, "message": f"Payment received. You are now on the {billing.PLANS[plan]['name']} plan.", "charged": amount,
            "currency": helcim.CURRENCY, **_status(wid)}


def _payment_row(t: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(t.get("type"))
    return {"id": t.get("transactionId") or t.get("id"), "date": str(t.get("dateCreated", ""))[:10], "amount": float(t.get("amount") or 0),
            "currency": t.get("currency") or helcim.CURRENCY, "kind": "refund" if kind in ("refund", "reverse") else "payment",
            "card": str(t.get("cardNumber", ""))[-4:], "brand": t.get("cardType") or ""}


@router.get("/v1/billing/payments")
def payments(user=Depends(current_user)):
    """The owner's payment history, read from Helcim on request."""
    require_owner(user)
    if not helcim.configured():
        return {"payments": []}
    try:
        rows = helcim.customer_transactions(customer_code(user["id"]))
    except helcim.HelcimError as e:
        raise HTTPException(502, str(e))
    return {"payments": [_payment_row(t) for t in rows]}


@router.get("/v1/billing/payments/{txn}/receipt")
def receipt(txn: int, user=Depends(current_user)):
    """A PDF receipt made on the spot from Helcim's record of the payment. Nothing is stored. Only the organization's own payments."""
    require_owner(user)
    _need_helcim()
    code = customer_code(user["id"])
    try:
        t = helcim.card_transaction(txn)
    except helcim.HelcimError:
        t = {}
    if not t or str(t.get("customerCode", "")).upper() != code.upper() or t.get("status") != "APPROVED" or t.get("type") not in ("purchase", "refund", "reverse"):
        raise HTTPException(404, "That payment was not found.")
    from . import receipts
    o = store.org(user["id"]) or {}
    pdf = receipts.make(_payment_row(t), t, o.get("name") or user.get("email") or "", user.get("email") or "", plan_label=o.get("plan"))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="watchfox-receipt-{txn}.pdf"', "Cache-Control": "no-store"})


@router.post("/v1/billing/cancel")
def cancel(user=Depends(current_user)):
    """Stop billing. The plan stays until the end of the period that was already paid for."""
    require_owner(user)
    _need_helcim()
    o = store.org(user["id"]) or {}
    if not o.get("helcim_subscription") or o.get("billing_status") == "cancelling":
        raise HTTPException(400, "There is no active subscription to cancel.")
    end = time.time()
    if o.get("billing_next"):
        try:
            end = datetime.fromisoformat(str(o["billing_next"])[:10]).replace(tzinfo=ZoneInfo("America/Edmonton")).timestamp()
        except ValueError:
            pass
    try:
        helcim.cancel_subscription(o["helcim_subscription"])
    except helcim.HelcimError as e:
        raise HTTPException(502, str(e))
    store.set_billing(user["id"], billing_status="cancelling", cancel_at=max(end, time.time()))
    return {"ok": True, "message": "Your subscription is cancelled. You keep your plan until the end of the period you have paid for.", **_status(user["id"])}


def downgrade(wid: str, reason: str) -> None:
    store.set_plan(wid, "kit", "monthly")
    store.set_billing(wid, helcim_subscription=None, billing_status="none", cancel_at=None, past_due_since=None, billing_next=None)
    print(f"[billing] {wid} moved to the Kit plan ({reason})", flush=True)


def sync_all() -> None:
    """Run every few hours: end cancelled plans on time, and follow failed payments to a grace period and then a downgrade."""
    if not helcim.configured():
        return
    now = time.time()
    for o in store.orgs_with_subscriptions():
        try:
            if o.get("billing_status") == "cancelling":
                if o.get("cancel_at") and now >= o["cancel_at"]:
                    downgrade(o["id"], "cancelled")
                continue
            if o.get("billing_status") == "pending_subscription":
                period = o.get("billing") or "monthly"
                made = helcim.create_subscription(_plan_id(o["plan"], "monthly" if period == "monthly" else "yearly"), o["helcim_customer"],
                                                  price(o["plan"], period, ""), o.get("billing_next") or _today_mountain())
                store.set_billing(o["id"], helcim_subscription=str(made.get("id")), billing_status="active", billing_next=made.get("dateBilling") or o.get("billing_next"))
                continue
            sub = helcim.subscription(o["helcim_subscription"])
            if sub is None:
                continue                                  # Helcim could not be reached: look again next time
            failed = str(sub.get("hasFailedPayments", "")).lower() == "true"
            store.set_billing(o["id"], billing_next=sub.get("dateBilling") or o.get("billing_next"))
            if failed and not o.get("past_due_since"):
                store.set_billing(o["id"], billing_status="past_due", past_due_since=now)
            elif failed and now - float(o["past_due_since"]) > GRACE_DAYS * 86400:
                try:
                    helcim.cancel_subscription(o["helcim_subscription"])
                except helcim.HelcimError:
                    pass
                downgrade(o["id"], "payment kept failing")
            elif not failed and o.get("billing_status") == "past_due":
                store.set_billing(o["id"], billing_status="active", past_due_since=None)
        except Exception as e:
            print(f"[billing] sync skipped {o.get('id')}: {type(e).__name__}", flush=True)
