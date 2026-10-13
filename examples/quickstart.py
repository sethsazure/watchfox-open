"""Sandbox quickstart: create a mailbox and a watch, drop in an email, read what was caught.
Run:  WATCHFOX_URL=https://your-host WATCHFOX_KEY=wf_live_... python quickstart.py"""
import os
import time

import requests

BASE = os.environ.get("WATCHFOX_URL", "http://localhost:8787")
H = {"Authorization": f"Bearer {os.environ['WATCHFOX_KEY']}"}

box = requests.post(f"{BASE}/v1/connections", headers=H, json={"provider": "sandbox"}).json()
watch = requests.post(f"{BASE}/v1/watches", headers=H, json={"connection_id": box["id"], "what": ["enquiry", "invoice"]}).json()
print("watch", watch["id"], watch["labels"])

for subject, body in [("Pricing", "Could you quote us for 40 licences, spec attached?"),
                      ("Invoice 2291", "Please find attached invoice 2291 for $2,450, due in 30 days."),
                      ("Newsletter", "This week in product: three new features. Unsubscribe any time.")]:
    requests.post(f"{BASE}/v1/connections/{box['id']}/sandbox-emails", headers=H,
                  json={"subject": subject, "body": body, "sender": "someone@example.com"})

time.sleep(8)   # the sandbox is checked every couple of seconds
for m in requests.get(f"{BASE}/v1/matches", headers=H).json():
    print("caught:", [r["label"] for r in m["results"].values() if r["matched"]], "-", m["email"]["subject"])
