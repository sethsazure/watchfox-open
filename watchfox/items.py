"""An Item is anything that can be watched: an email, a file, a pushed document."""
import io
import re
import time
from dataclasses import asdict, dataclass, field
from email import policy
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

SUPPORTED = {".eml", ".txt", ".md", ".html", ".htm", ".json", ".csv", ".pdf", ".log"}


@dataclass
class Item:
    id: str                       # unique within the watcher
    title: str = ""
    sender: str = ""
    text: str = ""
    received: float = field(default_factory=time.time)
    kind: str = "document"        # "email" gets reply/forward handling, anything else is read as is
    ref: str = ""                 # where it came from: a path, a message id, a url
    attachments: List[str] = field(default_factory=list)
    bulk: bool = False            # looks like a newsletter, mass mailing or automatic message
    raw: Optional[bytes] = None   # the original message, in memory only while it is being handled; never stored

    def public(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["snippet"] = re.sub(r"\s+", " ", self.text).strip()[:300]
        d.pop("text")
        return d


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "div", "tr", "li"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Strip()
    try:
        p.feed(html)
        text = "".join(p.out)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\xa0]+", " ", text)).strip()


_BULK_BODY = re.compile(r"\bunsubscribe\b|manage (your )?(email )?preferences|view (this email )?in (your )?browser", re.I)


def is_bulk(headers: Dict[str, str], subject: str, body: str) -> bool:
    """Newsletters, mass mailings and automatic messages. Uses the mail headers that bulk senders are expected to set,
    plus an unsubscribe link in the text. A forward ("Fw:") is never judged by its text, because the forwarded
    message may carry an old footer."""
    h = {k.lower(): str(v) for k, v in headers.items()}
    if "list-unsubscribe" in h or h.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return True
    if h.get("auto-submitted", "no").lower() not in ("no", ""):
        return True
    if subject.strip().lower().startswith(("fw:", "fwd:")):
        return False
    return bool(_BULK_BODY.search(body or ""))


def parse_eml(data: bytes) -> Dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            body = part.get_content()
        except Exception:
            body = ""
        if part.get_content_type() == "text/html":
            body = html_to_text(body)
    return {
        "sender": str(msg.get("From", "")),
        "subject": str(msg.get("Subject", "")),
        "date": str(msg.get("Date", "")),
        "body": body.strip(),
        "attachments": [p.get_filename() or "(unnamed)" for p in msg.iter_attachments()],
        "bulk": is_bulk(dict(msg.items()), str(msg.get("Subject", "")), body),
    }


def from_bytes(data: bytes, name: str, item_id: Optional[str] = None, ref: str = "", received: Optional[float] = None) -> Optional[Item]:
    """Turn a file's bytes into an Item, or None if the type is not supported / has no text."""
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        return None
    sender, attachments, kind, bulk = "", [], "document", False
    if ext == ".eml":
        p = parse_eml(data)
        title, text, sender, attachments, kind = p["subject"] or name, p["body"], p["sender"], p["attachments"], "email"
        bulk = p["bulk"]
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader

            text = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages[:15])
        except Exception:
            return None
        title = name
    else:
        raw = data.decode("utf-8", "replace")
        text = html_to_text(raw) if ext in (".html", ".htm") else raw
        title = name
    if not text.strip() and not title:
        return None
    return Item(id=item_id or name, title=title, sender=sender, text=text.strip(), kind=kind, ref=ref or name,
                attachments=attachments, received=received or time.time(), bulk=bulk,
                raw=data if ext == ".eml" else None)
