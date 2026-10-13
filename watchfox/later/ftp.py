import io
from ftplib import FTP, FTP_TLS
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.parse import unquote, urlparse

from ..items import SUPPORTED, Item, from_bytes
from . import Field, Source

MAX_BYTES = 10 * 1024 * 1024


class Ftp(Source):
    id = "ftp"
    label = "FTP folder"
    description = "A folder on an FTP or FTPS server, e.g. ftp://user:pass@host/incoming."
    interval = 30
    fields = [Field("url", "FTP address (ftp://user:password@host/folder)", secret=True, env="WATCHFOX_FTP_URL")]

    def _parts(self):
        u = urlparse(str(self.cfg.get("url", "")))
        if u.scheme not in ("ftp", "ftps") or not u.hostname:
            raise ValueError("FTP address must look like ftp://user:password@host/folder (or ftps://)")
        return u

    def _connect(self):
        u = self._parts()
        ftp = FTP_TLS() if u.scheme == "ftps" else FTP()
        ftp.connect(u.hostname, u.port or 21, timeout=20)
        ftp.login(unquote(u.username or "anonymous"), unquote(u.password or ""))
        if u.scheme == "ftps":
            ftp.prot_p()
        if u.path and u.path != "/":
            ftp.cwd(unquote(u.path))
        return ftp

    def check(self) -> None:
        try:
            self._connect().quit()
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Could not connect to the FTP server: {e}")

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        ftp = self._connect()
        try:
            try:
                entries = [(n, f) for n, f in ftp.mlsd(facts=["type", "size", "modify"]) if f.get("type") == "file"]
            except Exception:  # server without MLSD
                entries = []
                for n in ftp.nlst():
                    try:
                        entries.append((n, {"size": str(ftp.size(n) or 0), "modify": ""}))
                    except Exception:
                        pass
            entries = [(n, f) for n, f in entries if not n.startswith(".") and PurePosixPath(n).suffix.lower() in SUPPORTED]
            entries.sort(key=lambda e: e[1].get("modify", ""), reverse=True)
            for name, facts in entries[:limit]:
                item_id = f"{name}:{facts.get('size', '')}:{facts.get('modify', '')}"
                if seen(item_id) or int(facts.get("size") or 0) > MAX_BYTES:
                    continue
                buf = io.BytesIO()
                ftp.retrbinary(f"RETR {name}", buf.write)
                item = from_bytes(buf.getvalue(), name, item_id=item_id, ref=f"{self.cfg['url'].split('@')[-1]}/{name}")
                if item:
                    yield item
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
