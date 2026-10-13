import time
from pathlib import Path
from typing import Callable, Iterator

from ..items import SUPPORTED, Item, from_bytes
from . import Field, Source

MAX_BYTES = 20 * 1024 * 1024


class Folder(Source):
    id = "folder"
    label = "Folder"
    description = "A folder on this machine. New and changed files are read (.eml .txt .md .html .json .csv .pdf)."
    interval = 5
    fields = [Field("path", "Folder", env="WATCHFOX_FOLDER")]

    def _dir(self) -> Path:
        return Path(str(self.cfg.get("path", ""))).expanduser()

    def check(self) -> None:
        if not self._dir().is_dir():
            raise ValueError(f"Folder not found: {self._dir()}")

    def poll(self, seen: Callable[[str], bool], limit: int) -> Iterator[Item]:
        base = self._dir()
        pattern = "**/*" if self.cfg.get("recursive") else "*"
        files = []
        for p in base.glob(pattern):
            try:
                if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED:
                    st = p.stat()
                    if time.time() - st.st_mtime > 1.5 and st.st_size <= MAX_BYTES:  # skip files still being written
                        files.append((st.st_mtime, p, st.st_size))
            except OSError:
                continue
        for mtime, p, size in sorted(files, reverse=True)[:limit]:
            item_id = f"{p.relative_to(base)}:{int(mtime)}"
            if seen(item_id):
                continue
            try:
                item = from_bytes(p.read_bytes(), p.name, item_id=item_id, ref=str(p), received=mtime)
            except OSError:
                continue
            if item:
                yield item
