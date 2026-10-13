"""Outgoing email for verification, password reset and invitations.
With SMTP configured it sends. Without, it writes the message to ~/.watchfox/outbox and the server log, so a local
install still works (the dashboard shows the link on screen when config.DEV_LINKS is on)."""
import smtplib
import threading
import time
from email.message import EmailMessage
from pathlib import Path

from . import config


def _cloudflare(to: str, subject: str, text: str, from_name: str = "", reply_to: str = "") -> None:
    import httpx

    body = {"to": to, "from": {"address": config.EMAIL_FROM_ADDRESS, "name": from_name} if from_name else config.EMAIL_FROM_ADDRESS,
            "subject": subject, "text": text}
    if reply_to:
        body["reply_to"] = reply_to
    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/email/sending/send",
                   headers={"Authorization": f"Bearer {config.CF_EMAIL_TOKEN}"}, timeout=20, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")


def _smtp(to: str, subject: str, text: str, from_name: str = "", reply_to: str = "") -> None:
    from email.utils import formataddr, parseaddr

    m = EmailMessage()
    m["From"] = formataddr((from_name, parseaddr(config.SMTP_FROM)[1])) if from_name else config.SMTP_FROM
    m["To"], m["Subject"] = to, subject
    if reply_to:
        m["Reply-To"] = reply_to
    m.set_content(text)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(m)


def deliver(to: str, subject: str, text: str, from_name: str = "", reply_to: str = "") -> bool:
    """Send one email now with whichever service is set up. Returns whether it was handed over."""
    try:
        if config.CF_ACCOUNT_ID and config.CF_EMAIL_TOKEN:
            _cloudflare(to, subject, text, from_name, reply_to)
        elif config.SMTP_HOST:
            _smtp(to, subject, text, from_name, reply_to)
        elif config.DEV_LINKS:                                   # local install: save it where a developer can read it
            box = config.HOME / "outbox"
            box.mkdir(parents=True, exist_ok=True)
            (box / f"{int(time.time() * 1000)}-{to.replace('/', '_')}.txt").write_text(f"To: {to}\nSubject: {subject}\n\n{text}\n")
        else:
            return False
        return True
    except Exception as e:
        print(f"[mail] could not send to {to}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        return False


def send(to: str, subject: str, text: str, from_name: str = "", reply_to: str = "") -> None:
    threading.Thread(target=deliver, args=(to, subject, text, from_name, reply_to), daemon=True).start()
