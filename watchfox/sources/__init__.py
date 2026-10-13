"""Mailbox sources. Each turns a connected mailbox into a stream of emails (Items).

Folder, FTP and API sources are parked in watchfox/later/ until the agent for them exists.
"""
from typing import Callable, Dict, Iterator, Optional

from ..items import Item


class AuthError(Exception):
    """The mailbox sign-in no longer works: the person has to connect again. Never retried automatically."""


class Source:
    id = ""
    label = ""
    interval: Optional[int] = 60      # seconds between polls

    def __init__(self, cfg: dict):
        # cfg: {"secret": <decrypted credentials>, "since": <epoch seconds>, "connection_id": ..., "email": ...}
        self.cfg = cfg

    def check(self) -> None:
        """Raise ValueError with a clear message if the credentials do not work."""

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        return iter(())


def registry() -> Dict[str, type]:
    from .gmail import Gmail
    from .imap import Imap
    from .m365 import M365
    from .sandbox import Sandbox

    return {c.id: c for c in (Gmail, M365, Imap, Sandbox)}


def make(provider: str, cfg: dict) -> Source:
    reg = registry()
    if provider not in reg:
        raise ValueError(f"Unknown mailbox provider {provider!r}. Known: {', '.join(reg)}")
    return reg[provider](cfg)
