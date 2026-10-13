"""Fetch the original email (.eml) and its attachments from the mailbox at the moment they are asked for.
Nothing is stored: the bytes pass through to the caller and no copy is kept."""
import base64
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate
from typing import Any, Dict, List, Sequence

import httpx

from . import config, graph, store, vault
from .sources import AuthError, make


class Gone(Exception):
    """The email is no longer in the mailbox (deleted, moved, or the mailbox was reorganised)."""


class TooLarge(Exception):
    """The email is bigger than the download limit (WATCHFOX_MAX_DOWNLOAD_MB, 100 MB by default)."""


def build_eml(sender: str, subject: str, body: str, attachments: Sequence[Dict[str, Any]] = (), received: float = None) -> bytes:
    """A real .eml for the sandbox mailbox, optionally with attachments given as base64."""
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "mailbox@watchfox.test", subject
    m["Date"] = formatdate(received, usegmt=True)
    m.set_content(body or "")
    for a in attachments:
        maintype, _, subtype = (a.get("content_type") or "application/octet-stream").partition("/")
        m.add_attachment(base64.b64decode(a["content_base64"]), maintype=maintype or "application",
                         subtype=subtype or "octet-stream", filename=a["filename"])
    return bytes(m)


def parse_attachments(raw: bytes) -> List[Dict[str, Any]]:
    """Each attachment with its bytes: {id, filename, content_type, size, data}. ids are positions in the message."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    out = []
    for i, part in enumerate(msg.iter_attachments()):
        data = part.get_payload(decode=True)
        if data is None:                                   # a forwarded message attached as message/rfc822
            inner = part.get_payload()
            data = bytes(inner[0]) if inner else b""
        out.append({"id": str(i), "filename": part.get_filename() or f"attachment-{i}",
                    "content_type": part.get_content_type(), "size": len(data), "data": data})
    return out


def fetch_raw(conn: Dict[str, Any], email_id: str) -> bytes:
    """The original message, read live from the mailbox. Raises Gone if it is no longer there, AuthError if the
    sign-in no longer works."""
    provider = conn["provider"]
    if provider == "sandbox":
        row = store.sandbox_email(email_id)
        if not row or row["connection_id"] != conn["id"]:
            raise Gone()
        return row["raw"] or build_eml(row["sender"], row["subject"], row["body"], received=row["received"])
    if provider in ("imap", "gmail"):
        vid, _, uid = email_id.partition(":")
        if not uid.isdigit():
            raise Gone()
        m = make(provider, {"secret": vault.decrypt(conn["secret"])})._connect()      # AuthError passes up
        try:
            current = (m.response("UIDVALIDITY")[1] or [b"0"])[0]
            current = current.decode() if isinstance(current, bytes) else str(current)
            if current != vid:                              # the mailbox was rebuilt: old ids mean nothing now
                raise Gone()
            _, meta = m.uid("fetch", uid, "(RFC822.SIZE)")       # ask the size first, so a huge message is never read
            text = b" ".join(x if isinstance(x, bytes) else x[0] for x in (meta or []) if x)
            size = re.search(rb"RFC822\.SIZE (\d+)", text)
            if size and int(size.group(1)) > config.MAX_DOWNLOAD_BYTES:
                raise TooLarge()
            _, data = m.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in data if isinstance(p, tuple)), None)
            if not raw:
                raise Gone()
            return raw
        finally:
            try:
                m.logout()
            except Exception:
                pass
    if provider == "m365":
        token = graph.token_for(conn)
        limit = config.MAX_DOWNLOAD_BYTES
        with httpx.stream("GET", f"{config.GRAPH_URL}/me/messages/{email_id}/$value",
                          headers={"Authorization": f"Bearer {token}"}, timeout=60) as r:
            if r.status_code == 404:
                raise Gone()
            if r.status_code == 401:
                raise AuthError("The Microsoft sign-in was rejected. The person needs to connect their mailbox again.")
            r.raise_for_status()
            if int(r.headers.get("content-length") or 0) > limit:
                raise TooLarge()
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise TooLarge()
            return bytes(buf)
    raise Gone()
