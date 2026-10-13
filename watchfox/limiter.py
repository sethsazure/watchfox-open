"""Small in-memory sliding-window rate limiter. Per process: use a shared store if you run several."""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException

_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def hit(bucket: str, key: str, limit: int, window: int) -> None:
    now = time.time()
    with _lock:
        q = _hits[(bucket, key)]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, f"Rate limit reached ({limit} per {window}s). Try again shortly.",
                                headers={"Retry-After": str(int(q[0] + window - now) + 1)})
        q.append(now)
