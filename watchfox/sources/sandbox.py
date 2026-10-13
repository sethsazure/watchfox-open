from typing import Callable, Iterator

from .. import mailfetch, store
from ..items import Item, from_bytes
from . import Source


class Sandbox(Source):
    """A pretend mailbox for testing. You add emails through the API; they flow through watches like real mail."""
    id = "sandbox"
    label = "Sandbox mailbox"
    interval = 2
    method = "sandbox"

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        for e in store.sandbox_emails(self.cfg["connection_id"]):
            if seen(e["id"]) or e["received"] < self.cfg.get("since", 0) - 5:
                continue
            raw = e.get("raw") or mailfetch.build_eml(e["sender"], e["subject"], e["body"], received=e["received"])
            item = from_bytes(raw, "mail.eml", item_id=e["id"], ref=f"sandbox:{e['id']}", received=e["received"])
            if item:
                yield item
