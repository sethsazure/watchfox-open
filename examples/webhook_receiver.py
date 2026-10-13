"""A webhook receiver that verifies the signature.  python webhook_receiver.py
Create the watch with webhook_url pointing at this server (it must be https and public in production;
use a tunnel while developing) and put the watch's webhook_secret in WATCHFOX_WEBHOOK_SECRET."""
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ["WATCHFOX_WEBHOOK_SECRET"]


def verify(raw: bytes, header: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    good = hmac.new(SECRET.encode(), f"{parts['t']}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, parts["v1"])


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if not verify(raw, self.headers.get("X-WatchFox-Signature", "t=0,v1=")):
            self.send_response(400); self.end_headers(); return
        event = json.loads(raw)
        print([m["label"] for m in event["matches"]], event["email"]["subject"], "from", event["email"]["from"])
        self.send_response(204); self.end_headers()


HTTPServer(("", 9000), Hook).serve_forever()
