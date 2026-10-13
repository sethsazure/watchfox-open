#!/usr/bin/env python3
"""A stand-in for the real checking server, so you can try WatchFox on a laptop without a GPU.

It speaks the same small HTTP API as `laya-serve` (GET /health, POST /v1/systemone, POST /v1/systemone/batch) but answers with simple
keyword rules instead of a model, so its scores are only a rough guide. Use the real open-weights model for anything that matters
(see the README).

    python examples/stub_agent.py                      # listens on http://127.0.0.1:9000
    LAYA_URL=http://127.0.0.1:9000 LAYA_API_KEY=local watchfox serve
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LEXICONS = [   # (words in the question that pick this list, words in the email that count as evidence)
    (("invoice", "bill asking", "payment"), ("invoice", "amount due", "due date", "payment terms", "remit", "balance", "tax invoice", "pay by")),
    (("quote", "price", "proposal", "rfq", "rfp"), ("quote", "quotation", "price", "pricing", "rfq", "rfp", "proposal", "estimate", "how much", "cost")),
    (("problem", "help", "support", "already use"), ("error", "not working", "cannot", "can't", "broken", "fails", "help", "issue", "since the update")),
    (("order placed", "purchase order", "placed for goods"), ("purchase order", "po ", "please ship", "place an order", "order for", "deliver to")),
    (("resume", "job", "application for a job"), ("resume", "cv", "apply", "applying", "position", "role", "hiring")),
    (("refund", "replacement", "compensation", "warranty"), ("refund", "replacement", "warranty", "damaged", "compensation", "credit note", "wrong item")),
    (("automatic", "newsletter", "marketing", "bulk", "unsubscribe", "advert", "pitch"), ("unsubscribe", "newsletter", "webinar", "out of office", "automatic reply", "limited time", "book a demo", "no-reply")),
]


def score(question: str, text: str) -> float:
    """Probability-like number from keyword evidence. Not a model."""
    q, t = question.lower(), text.lower()
    for triggers, words in LEXICONS:
        if any(w in q for w in triggers):
            hits = sum(1 for w in words if w in t)
            return round(min(0.95, 0.05 + 0.22 * hits), 4)
    qwords = {w for w in re.findall(r"[a-z]{5,}", q)}
    twords = set(re.findall(r"[a-z]{5,}", t))
    return round(min(0.9, 0.05 + 0.1 * len(qwords & twords)), 4)


def flatten(state) -> str:
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        return " ".join(flatten(v) for v in state.values())
    if isinstance(state, list):
        return " ".join(flatten(v) for v in state)
    return str(state or "")


def answers(state, questions):
    text = flatten(state)
    return {qid: {"type": "noul", "noul": score(str(q.get("instructions", "")), text)} for qid, q in questions.items()}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"status": "ok", "device": "stub (keyword rules, not a model)"}) if self.path == "/health" else self._send(404, {"detail": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)) or 0) or b"{}")
        if self.path == "/v1/systemone":
            self._send(200, {"model": body.get("model"), "answers": answers(body.get("state"), body.get("questions", {}))})
        elif self.path == "/v1/systemone/batch":
            self._send(200, {"model": body.get("model"), "results": [{"answers": answers(s, body.get("questions", {}))} for s in body.get("states", [])]})
        else:
            self._send(404, {"detail": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    print(f"Stand-in agent on http://127.0.0.1:{port} (keyword rules, not the real model)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
