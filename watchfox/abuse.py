"""Keeps the free plan from being farmed and the service from being flooded.

Accounts can only be made by signing in with GitHub, Google or Microsoft, so a bot needs a real provider account for every
account it makes. On top of that: disposable addresses and address aliases are refused, a limited number of new accounts may
start from one network address per day (and from the whole service per hour), very new GitHub accounts are refused, and a
real mailbox gets one free allowance a month no matter how many accounts it is connected to."""
import hashlib
import ipaddress
import os
import re
import socket
import time
from typing import Optional
from urllib.parse import urlparse

from . import billing, config, limiter, store

DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "guerrillamail.org", "sharklasers.com", "grr.la", "10minutemail.com",
    "10minutemail.net", "tempmail.com", "temp-mail.org", "temp-mail.io", "tempmailo.com", "throwawaymail.com", "yopmail.com", "yopmail.net",
    "yopmail.fr", "getnada.com", "nada.email", "maildrop.cc", "dispostable.com", "trashmail.com", "trashmail.net", "fakeinbox.com",
    "mintemail.com", "mohmal.com", "emailondeck.com", "spamgourmet.com", "mytemp.email", "tempinbox.com", "burnermail.io", "moakt.com",
    "tmpmail.org", "tmpmail.net", "tempail.com", "mailnesia.com", "mailcatch.com", "inboxkitten.com", "anonbox.net", "dropmail.me",
    "emailfake.com", "fakemail.net", "harakirimail.com", "jetable.org", "33mail.com", "spambox.us", "trbvm.com", "cyber-mail.cc",
}


class Refused(Exception):
    """A sign-up that is turned away. The message is safe to show to the person."""


# ---------------------------------------------------------------- who is calling
def _strip_port(addr: str) -> str:
    a = addr.strip()
    if a.startswith("["):                                       # [2001:db8::1]:443
        return a[1:].split("]")[0]
    if a.count(":") == 1:                                       # 203.0.113.9:51234
        return a.split(":")[0]
    return a


def client_ip(request) -> str:
    """The caller's address. Behind a proxy, only the entry the proxy itself added is believed: anything the caller put in
    X-Forwarded-For comes before it, so counting from the end cannot be spoofed. WATCHFOX_PROXY_HOPS is how many proxies add one."""
    peer = request.client.host if request.client else "?"
    if os.getenv("WATCHFOX_TRUST_PROXY") != "1":
        return peer
    fwd = [p for p in (x.strip() for x in request.headers.get("x-forwarded-for", "").split(",")) if p]
    hops = max(1, int(os.getenv("WATCHFOX_PROXY_HOPS", "1")))
    return _strip_port(fwd[-hops]) if len(fwd) >= hops else peer


# ---------------------------------------------------------------- never reach inside our own network
def public_host(host: str, port: int = 443) -> bool:
    """True if the host name resolves only to public addresses. Used for mail servers and webhooks, so a customer cannot point
    WatchFox at our database, our other servers or the cloud's internal services."""
    try:
        for _, _, _, _, addr in socket.getaddrinfo(host, port):
            ip = ipaddress.ip_address(addr[0].split("%")[0])
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified \
                    or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")):        # carrier-grade NAT, which includes Tailscale addresses
                return False
    except (socket.gaierror, ValueError, UnicodeError):
        return False
    return True


def public_url(url: str) -> bool:
    u = urlparse(url)
    return bool(u.hostname) and public_host(u.hostname, u.port or (443 if u.scheme == "https" else 80))


# ---------------------------------------------------------------- new accounts
def check_new_account(ip: str, email: str, github_age_days: Optional[float] = None) -> None:
    """Raises Refused if this sign-up looks like farming free accounts. Called only for brand-new accounts that were not invited."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in DISPOSABLE:
        raise Refused("Please sign in with a permanent email address, not a disposable one.")
    if store.user_by_canon(store.canonical_email(email)):
        raise Refused("An account already exists for this address. Sign in the way you did before (GitHub, Google or Microsoft).")
    if github_age_days is not None and github_age_days < config.GITHUB_MIN_AGE_DAYS:
        raise Refused(f"This GitHub account is too new. Try again in a few days, or sign in with Google or Microsoft.")
    now = time.time()
    if store.signups_since(ip, now - 86400) >= config.NEW_ACCOUNTS_PER_IP_DAY:
        raise Refused("Too many accounts were created from this network today. Try again tomorrow, or ask us for help.")
    if store.signups_total_since(now - 3600) >= config.NEW_ACCOUNTS_PER_HOUR:
        raise Refused("WatchFox is very busy right now. Please try again in a little while.")
    store.add_signup(ip)


# ---------------------------------------------------------------- one free allowance per real mailbox
def _mailbox_hash(conn) -> str:
    return hashlib.sha256(store.canonical_email(conn["email"]).encode()).hexdigest()


def _counts(wid: str, conn) -> bool:
    """Only free-plan accounts are held to the shared allowance, and only real mailboxes (a sandbox has no owner to compare)."""
    return bool(conn) and config.ENFORCE_PLANS and conn["provider"] != "sandbox" and billing.plan_of(wid)["id"] == "kit"


def mailbox_allows(wid: str, conn) -> bool:
    if not _counts(wid, conn):
        return True
    return store.mailbox_runs(_mailbox_hash(conn), billing.month()) < billing.PLANS["kit"]["runs"]


def count_mailbox(wid: str, conn) -> None:
    if _counts(wid, conn):
        store.add_mailbox_run(_mailbox_hash(conn), billing.month())


# ---------------------------------------------------------------- free accounts sharing a network address
def free_gate(user, request) -> None:
    """Test calls and sandbox mail from free accounts are also capped per network address, so a loop over many free accounts
    from one place does not get each account's 50 runs."""
    if config.ENFORCE_PLANS and billing.plan_of(user["id"])["id"] == "kit":
        limiter.hit("free-ip", client_ip(request), config.FREE_PER_IP_DAY, 86400)
