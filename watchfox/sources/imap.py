import imaplib
import time
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator

from .. import abuse
from ..items import Item, from_bytes
from . import AuthError, Source

_AUTH_MARKERS = ("authenticationfailed", "invalid credentials", "login failed", "authentication failed", "invalid password",
                 "application-specific password", "web login required")


class Imap(Source):
    id = "imap"
    label = "IMAP"
    interval = 60
    method = "password"
    default_host = ""
    help = "The mail server did not accept this email address and password."

    def _connect(self):
        s = self.cfg["secret"]
        host, port = s.get("host") or self.default_host, int(s.get("port", 993))
        if port != 993 or not abuse.public_host(host, 993):          # never reach into our own network, even if the name changed later
            raise AuthError("The mail server address is not allowed. Use a public IMAP server on port 993.")
        try:
            m = imaplib.IMAP4_SSL(host, port, timeout=30)
            m.login(s["user"], s["password"])
        except imaplib.IMAP4.error as e:
            text = str(e).lower()
            if any(k in text for k in _AUTH_MARKERS):
                raise AuthError(self.help)
            raise
        m.select(s.get("folder", "INBOX"), readonly=True)  # read-only: never marks mail as read
        return m

    def check(self) -> None:
        try:
            self._connect().logout()
        except AuthError as e:
            raise ValueError(str(e))
        except Exception as e:
            raise ValueError("Could not sign in to the mail server. Check the server name, address and password.")

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        since = float(self.cfg.get("since", 0))
        day = datetime.fromtimestamp(since, timezone.utc).strftime("%d-%b-%Y")
        m = self._connect()
        try:
            validity = (m.response("UIDVALIDITY")[1] or [b"0"])[0]
            validity = validity.decode() if isinstance(validity, bytes) else str(validity)
            _, data = m.uid("search", None, "SINCE", day)
            for uid in reversed((data[0] or b"").split()[-limit:]):
                item_id = f"{validity}:{uid.decode()}"
                if seen(item_id):
                    continue
                _, msg = m.uid("fetch", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in msg if isinstance(p, tuple)), None)
                if not raw:
                    continue
                try:
                    received = parsedate_to_datetime(message_from_bytes(raw)["Date"]).timestamp()
                except Exception:
                    received = time.time()
                if received < since - 60:      # SINCE is whole days; skip mail from before the watch began
                    continue
                item = from_bytes(raw, "mail.eml", item_id=item_id, ref=f"imap:{uid.decode()}", received=received)
                if item:
                    yield item
        finally:
            try:
                m.logout()
            except Exception:
                pass
