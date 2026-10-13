"""The checking agent. Every watch is one yes/no question, scored on its own.

A watch matches when P(yes) reaches its threshold and the optional "exclude if" question stays
under its limit. Probabilities are not calibrated: tune thresholds on your own labelled data.
"""
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import config, presets
from .items import Item

MAX_CHARS = 3000


def model_body(subject: str, body: str) -> str:
    """Text the model reads for an email.

    laya's cleaner drops everything below the first quoted-reply header, right for a plain reply but
    wrong for a forward ("Hi Sam, see below" over the customer's actual request). When the cleaned
    text is short and more text sits below it, keep the whole thread, quote markers removed.
    """
    from laya.email import clean_email_body

    cleaned = clean_email_body(body, max_chars=MAX_CHARS)
    raw = "\n".join(ln.lstrip("> ").rstrip() for ln in (body or "").replace("\r\n", "\n").split("\n"))
    is_fwd = (subject or "").strip().lower().startswith(("fw:", "fwd:"))
    if len(raw.strip()) > len(cleaned) + 40 and (is_fwd or len(cleaned) < 400):
        return re.sub(r"\n{3,}", "\n\n", raw).strip()[:MAX_CHARS]
    return cleaned


class ModelUnavailable(RuntimeError):
    """No agent can answer right now (the main one is down and the backup is busy or unavailable). Not the customer's fault, and
    not a reason to treat their mailbox as broken: the email stays unchecked and is tried again."""


class Engine:
    """Scores emails on the remote agent named by LAYA_URL (nothing runs locally)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client: Optional[httpx.Client] = None
        self._probed = 0.0
        self._gw: Optional[httpx.Client] = None
        self._gw_blocked_until = 0.0      # the backup answered 429: do not ask again until this time
        self._gw_lock = threading.Lock()
        self._gw_next = 0.0               # the next time the backup may be called (paced to its requests-a-minute limit)
        self.waiting = 0                  # emails queued for the backup right now
        self._warned = False
        self.status = "idle"  # idle | loading | ready | error (the main server)
        self.error: Optional[str] = None
        self.device: Optional[str] = None

    # ---------------------------------------------------------------- start up
    def load(self) -> None:
        """Check the agent server is up. Retried at most every 10 seconds while it is not, so a network blip heals itself."""
        if self.status == "ready" or time.time() - self._probed < 10:
            return
        with self._lock:
            if self.status == "ready" or time.time() - self._probed < 10:
                return
            self._probed = time.time()
            if not config.LAYA_URL:
                self.status, self.error = "error", "The checking agent is not configured."
                return
            self.status, self.error = "loading", None
            try:
                r = self.http().get("/health")
                r.raise_for_status()
                self.device = str(r.json().get("device") or "remote")
                self.status = "ready"
            except Exception as e:
                self._log(f"cannot reach {config.LAYA_URL}: {type(e).__name__}")
                self.status, self.error = "error", "The checking agent is not reachable right now."

    def http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=config.LAYA_URL, timeout=config.LAYA_TIMEOUT,
                                        headers={"Authorization": f"Bearer {config.LAYA_API_KEY}"},
                                        proxy=config.LAYA_PROXY or None,      # in the cloud the agent is reached through the Tailscale proxy
                                        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
        return self._client

    @staticmethod
    def _log(msg: str) -> None:
        print(f"[agent] {msg}", flush=True)

    @property
    def has_backup(self) -> bool:
        return bool(config.LAYA_FALLBACK_KEY)

    @property
    def serving(self) -> str:
        """"primary" (the main agent), "backup" (while the main agent is down) or "down"."""
        return "primary" if self.status == "ready" else "backup" if self.has_backup else "down"

    # ---------------------------------------------------------------- the backup (Vercel AI Gateway, TypeSafe-compatible API)
    def _backup_slot(self, wait: bool) -> None:
        """Reserve this email's turn on the backup. The backup answers only a few requests a minute, so turns are spaced
        evenly, in order. A caller that cannot wait (a person using the test call) fails at once instead of queueing."""
        gap = 60.0 / max(1, config.LAYA_FALLBACK_RPM)
        with self._gw_lock:
            now = time.time()
            start = max(now, self._gw_next, self._gw_blocked_until)
            if start - now > (config.LAYA_QUEUE_MAX_WAIT if wait else 0.25):
                raise ModelUnavailable(f"The agent is busy (about {int(start - now) + 1}s until it can take another email).")
            self._gw_next = start + gap
            self.waiting += 1
        try:
            while True:
                left = start - time.time()
                if left <= 0:
                    return
                time.sleep(min(left, 5))
                if self.status == "ready":            # the main server is back: stop waiting for the backup
                    raise ModelUnavailable("main server recovered")
        finally:
            with self._gw_lock:
                self.waiting -= 1

    def _backup_answers(self, state: Dict[str, Any], questions: Dict[str, Any], wait: bool = False) -> Dict[str, Any]:
        if self._gw is None:
            self._gw = httpx.Client(base_url=config.LAYA_FALLBACK_URL, timeout=config.LAYA_TIMEOUT,
                                    headers={"Authorization": f"Bearer {config.LAYA_FALLBACK_KEY}"})
        if not self._warned:
            self._warned = True
            self._log("main agent unreachable: using the backup agent")
        self._backup_slot(wait)
        out: Dict[str, Any] = {}
        for part in self._chunks(questions, 64):               # one request per email: the backup allows very few requests a minute
            try:
                r = self._gw.post("/v1/systemone", json={"model": config.LAYA_FALLBACK_MODEL, "state": json.dumps(state, ensure_ascii=False),
                                                         "questions": part})
            except httpx.TransportError:
                raise ModelUnavailable("The checking agent is not reachable right now.")
            if r.status_code == 403 and "credit card" in r.text:
                self._log("the backup service needs a credit card on file before it will answer")
                raise ModelUnavailable("The backup agent is not available.")
            if r.status_code == 429:                           # our pacing was too optimistic (other users of the same limit): wait it out
                retry = float(r.headers.get("retry-after") or 30)
                with self._gw_lock:
                    self._gw_blocked_until = time.time() + min(retry, 120)
                    self._gw_next = max(self._gw_next, self._gw_blocked_until)
                raise ModelUnavailable(f"The agent is busy (retry in {int(retry)}s).")
            if r.status_code >= 400:
                self._log(f"the backup service refused the request (HTTP {r.status_code})")
                raise ModelUnavailable("The backup agent could not process the request.")
            out.update(r.json()["answers"])
        return out

    # ---------------------------------------------------------------- asking the model
    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(3):
            try:
                r = self.http().post(path, json=payload)
                if r.status_code in (502, 503, 504):                 # busy or restarting: wait a little and retry
                    time.sleep(0.4 * (attempt + 1))
                    continue
                if r.status_code in (401, 403):
                    self._log("the agent server refused the API key")
                    self.status, self.error = "error", "The checking agent refused the connection."
                    raise RuntimeError(self.error)
                r.raise_for_status()
                return r.json()
            except RuntimeError:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError):
                time.sleep(0.4 * (attempt + 1))
        self.status, self.error = "error", "The checking agent is not reachable right now."
        self._probed = time.time()
        raise ModelUnavailable(self.error)

    @staticmethod
    def _chunks(questions: Dict[str, Any], size: int = 64) -> List[Dict[str, Any]]:
        keys = list(questions)
        return [{k: questions[k] for k in keys[i:i + size]} for i in range(0, len(keys), size)]

    def _answers(self, state: Dict[str, Any], questions: Dict[str, Any], wait: bool = False) -> Dict[str, Any]:
        while True:
            if self.status == "ready":
                try:
                    out: Dict[str, Any] = {}
                    for part in self._chunks(questions):
                        out.update(self._post("/v1/systemone", {"model": config.MODEL, "state": state, "questions": part})["answers"])
                    return out
                except RuntimeError:
                    if not self.has_backup:
                        raise
            try:
                return self._backup_answers(state, questions, wait)
            except ModelUnavailable as e:
                if wait and str(e) == "main server recovered":      # go round again on the main server
                    continue
                raise

    def _answers_many(self, states: List[Dict[str, Any]], questions: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One answers dict per state. States travel in batches of up to 64, each scored in one forward pass."""
        if self.status == "ready":
            try:
                merged: List[Dict[str, Any]] = [{} for _ in states]
                size = config.LAYA_BATCH_MAX
                for part in self._chunks(questions):
                    for i in range(0, len(states), size):
                        res = self._post("/v1/systemone/batch", {"model": config.MODEL, "states": states[i:i + size], "questions": part})["results"]
                        for k, r in enumerate(res):
                            merged[i + k].update(r["answers"])
                return merged
            except RuntimeError:
                if not self.has_backup:
                    raise
        return [self._backup_answers(s, questions) for s in states]      # the backup has no batch call, and callers here cannot wait

    # ---------------------------------------------------------------- scoring
    def _prepare(self, item: Item, whats: List[Dict[str, Any]]) -> Dict[str, Any]:
        from laya.email import email_state

        body = model_body(item.title, item.text) if item.kind == "email" else item.text[:MAX_CHARS]
        return email_state(subject=item.title or "", body=body, sender=item.sender or None, clean=False)

    @staticmethod
    def _questions(whats: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[int, list]]:
        questions, excl_of = {}, {}
        for i, w in enumerate(whats):
            questions[f"w{i}"] = {"type": "noul", "instructions": w["question"]}
            excl_of[i] = presets.excludes(w)
            for j, e in enumerate(excl_of[i]):
                questions[f"x{i}_{j}"] = {"type": "noul", "instructions": e["question"]}
        return questions, excl_of

    def _ready(self) -> None:
        self.load()
        if self.status != "ready" and not self.has_backup:
            raise ModelUnavailable(self.error or "The checking agent is not ready")

    def score(self, item: Item, whats: List[Dict[str, Any]], wait: bool = False) -> Dict[str, Dict[str, Any]]:
        """{what_id: {score, exclude_score, matched, excluded, threshold, label}} for one item. With wait=True (background
        checking of mail) an email waits its turn when only the slow backup is available, instead of failing."""
        self._ready()
        questions, excl_of = self._questions(whats)
        return self._finish(item, whats, excl_of, self._answers(self._prepare(item, whats), questions, wait))

    def score_many(self, items: List[Item], whats: List[Dict[str, Any]]) -> List[Dict[str, Dict[str, Any]]]:
        """The same as score() for many items. With a remote server this is one call per 64 emails instead of one per email."""
        self._ready()
        questions, excl_of = self._questions(whats)
        answers = self._answers_many([self._prepare(it, whats) for it in items], questions)
        return [self._finish(it, whats, excl_of, a) for it, a in zip(items, answers)]

    @staticmethod
    def _finish(item: Item, whats: List[Dict[str, Any]], excl_of: Dict[int, list], ans: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out = {}
        for i, w in enumerate(whats):
            score = round(float(ans[f"w{i}"]["noul"]), 4)
            ex = [(round(float(ans[f"x{i}_{j}"]["noul"]), 4), e["threshold"]) for j, e in enumerate(excl_of[i])]
            over = score >= w["threshold"]
            reason = presets.ignore_reason(w, item.title, item.bulk) if over else ""
            if over and not reason and any(s >= t for s, t in ex):
                reason = "looks like something other than a request"
            excluded = bool(reason)
            pct = presets.confidence(score, w["threshold"])
            out[w["id"]] = {"label": w["label"], "score": score, "threshold": w["threshold"],
                            "exclude_score": max((s for s, _ in ex), default=None),   # the highest exclusion score
                            "matched": bool(over and not excluded), "excluded": excluded, "reason": reason or None,
                            "confidence": pct, "confidence_level": presets.confidence_level(pct)}
        return out


engine = Engine()
