"""The engine talking to a remote laya-serve (mocked here; no model needed)."""
import json
import os
import tempfile

os.environ.setdefault("WATCHFOX_HOME", tempfile.mkdtemp())

import httpx  # noqa: E402
import pytest  # noqa: E402

from watchfox import config, presets  # noqa: E402
from watchfox.engine import Engine  # noqa: E402
from watchfox.items import Item  # noqa: E402

WHATS = [dict(presets.all_presets()["invoice"])]


def make(handler, monkeypatch):
    monkeypatch.setattr(config, "LAYA_URL", "https://laya.test:9443")
    monkeypatch.setattr(config, "LAYA_API_KEY", "secret-key")
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "")        # tests must never reach the real backup service
    monkeypatch.setattr(config, "LAYA_FALLBACK_RPM", 600000)    # no spacing between backup calls unless a test asks for it
    e = Engine()
    e._client = httpx.Client(base_url=config.LAYA_URL, transport=httpx.MockTransport(handler),
                             headers={"Authorization": "Bearer secret-key"})
    return e


def answer(req_json, p=0.9):
    return {qid: {"type": "noul", "noul": p} for qid in req_json["questions"]}


def test_single_scoring_uses_the_model_name_and_the_key(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "device": "cuda"})
        body = json.loads(req.content)
        assert body["model"] == "typed-decisions" and body["state"]["subject"] == "Invoice 7"
        assert req.headers["authorization"] == "Bearer secret-key"
        return httpx.Response(200, json={"model": "typed-decisions", "answers": answer(body)})

    e = make(handler, monkeypatch)
    r = e.score(Item(id="1", title="Invoice 7", text="please pay", kind="email"), WHATS)
    assert r["invoice"]["matched"] is True and r["invoice"]["score"] == 0.9
    assert e.status == "ready" and e.device == "cuda"


def test_batches_of_64_and_question_chunks(monkeypatch):
    calls = []

    def handler(req):
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(req.content)
        calls.append((req.url.path, len(body.get("states", [])), len(body["questions"])))
        return httpx.Response(200, json={"results": [{"answers": answer(body)} for _ in body["states"]]})

    e = make(handler, monkeypatch)
    items = [Item(id=str(i), title=f"s{i}", text="x", kind="email") for i in range(130)]
    out = e.score_many(items, WHATS)
    assert len(out) == 130 and all(r["invoice"]["matched"] for r in out)
    assert [c[1] for c in calls] == [64, 64, 2] and all(c[0] == "/v1/systemone/batch" for c in calls)
    # more than 64 questions are split across calls and merged
    many = [dict(WHATS[0], id=f"t{i}", question=f"Is it {i}?") for i in range(70)]
    calls.clear()
    r = e.score_many(items[:2], many)
    assert len(r[0]) == 70 and sorted(c[2] for c in calls) == [6, 64]


def test_busy_server_is_retried_and_a_bad_key_is_reported(monkeypatch):
    n = {"c": 0}

    def busy_then_ok(req):
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 3 else httpx.Response(200, json={"answers": answer(json.loads(req.content))})

    monkeypatch.setattr("time.sleep", lambda s: None)
    e = make(busy_then_ok, monkeypatch)
    assert e.score(Item(id="1", title="t", text="x", kind="email"), WHATS)["invoice"]["matched"] and n["c"] == 3

    e2 = make(lambda req: httpx.Response(200, json={"status": "ok"}) if req.url.path == "/health" else httpx.Response(401), monkeypatch)
    with pytest.raises(RuntimeError, match="refused the connection"):
        e2.score(Item(id="1", title="t", text="x", kind="email"), WHATS)


def test_unreachable_server_or_missing_url_is_a_clear_error(monkeypatch):
    def down(req):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr("time.sleep", lambda s: None)
    e = make(down, monkeypatch)
    with pytest.raises(RuntimeError, match="not reachable"):
        e.score(Item(id="1", title="t", text="x", kind="email"), WHATS)
    assert e.status == "error"
    # it recovers on its own once the server is back (after the 10 second pause between checks)
    ok = make(lambda req: httpx.Response(200, json={"status": "ok", "device": "cuda"}) if req.url.path == "/health" else
              httpx.Response(200, json={"answers": answer(json.loads(req.content))}), monkeypatch)
    ok._probed = 0
    assert ok.score(Item(id="1", title="t", text="x", kind="email"), WHATS)["invoice"]["matched"] and ok.status == "ready"
    monkeypatch.setattr(config, "LAYA_URL", "")
    none = Engine()
    with pytest.raises(RuntimeError, match="not configured"):
        none.score(Item(id="1", title="t", text="x", kind="email"), WHATS)


def test_backup_model_is_used_only_while_the_main_server_is_down(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    gw_calls = []

    def down(req):
        raise httpx.ConnectError("no route")

    def gateway(req):
        body = json.loads(req.content)
        gw_calls.append((str(req.url), req.headers["authorization"], body))
        return httpx.Response(200, json={"model": body["model"], "answers": answer(body, 0.8)})

    e = make(down, monkeypatch)
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "vck_test")
    monkeypatch.setattr(config, "LAYA_FALLBACK_URL", "https://ai-gateway.test/typesafe")
    e._gw = httpx.Client(base_url=config.LAYA_FALLBACK_URL, transport=httpx.MockTransport(gateway), headers={"Authorization": "Bearer vck_test"})
    item = Item(id="1", title="Invoice 7", text="please pay", sender="a@b.com", kind="email")
    r = e.score(item, WHATS)
    assert r["invoice"]["score"] == 0.8 and e.serving == "backup"
    url, auth, body = gw_calls[0]
    assert url.endswith("/typesafe/v1/systemone") and auth == "Bearer vck_test" and body["model"] == "convaiinnovations/laya"
    assert isinstance(body["state"], str) and json.loads(body["state"])["subject"] == "Invoice 7"       # same text the main server would read
    assert len(e.score_many([item, item, item], WHATS)) == 3 and len(gw_calls) == 4                      # no batch call on the backup

    # a card-less Vercel account says so clearly
    e._gw = httpx.Client(base_url=config.LAYA_FALLBACK_URL, headers={"Authorization": "x"},
                         transport=httpx.MockTransport(lambda req: httpx.Response(403, text='{"error":{"message":"AI Gateway requires a valid credit card on file"}}')))
    with pytest.raises(RuntimeError, match="backup agent is not available"):
        e.score(item, WHATS)
    # with the backup switched off the main server's failure is reported as before
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "")
    e2 = make(down, monkeypatch)
    assert e2.serving == "down"
    with pytest.raises(RuntimeError, match="not reachable"):
        e2.score(item, WHATS)


def test_backup_rate_limit_fails_fast_instead_of_hammering(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    n = {"c": 0}

    def limited(req):
        n["c"] += 1
        return httpx.Response(429, headers={"retry-after": "55"}, text="rate limit")

    e = make(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")), monkeypatch)
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "vck_test")
    e._gw = httpx.Client(base_url="https://gw.test", transport=httpx.MockTransport(limited), headers={"Authorization": "x"})
    item = Item(id="1", title="t", text="x", kind="email")
    for _ in range(3):
        with pytest.raises(RuntimeError, match="rate limited|busy"):
            e.score(item, WHATS)
    assert n["c"] == 1                                  # only the first attempt reached the service


def test_emails_wait_their_turn_for_the_backup(monkeypatch):
    import threading
    import time as _t
    done = []

    def gateway(req):
        body = json.loads(req.content)
        done.append((_t.time(), json.loads(body["state"])["subject"]))
        return httpx.Response(200, json={"answers": answer(body, 0.7)})

    e = make(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")), monkeypatch)
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "vck_test")
    monkeypatch.setattr(config, "LAYA_FALLBACK_RPM", 120)               # one call every half second
    e._gw = httpx.Client(base_url="https://gw.test", transport=httpx.MockTransport(gateway), headers={"Authorization": "x"})
    mk = lambda n: Item(id=str(n), title=f"mail {n}", text="x", kind="email")
    # a person using the test call does not queue: the second request in the same half second is refused at once
    assert e.score(mk(0), WHATS)["invoice"]["matched"]
    with pytest.raises(RuntimeError, match="busy"):
        e.score(mk(99), WHATS)
    # background mail waits its turn and every email is checked, in order
    out, start = [], _t.time()
    threads = [threading.Thread(target=lambda i=i: out.append((i, e.score(mk(i), WHATS, wait=True)))) for i in (1, 2, 3)]
    for t in threads:
        t.start(); _t.sleep(0.05)
    for t in threads:
        t.join(timeout=10)
    assert sorted(i for i, _ in out) == [1, 2, 3] and all(r["invoice"]["matched"] for _, r in out)
    assert [s for _, s in done[1:]] == ["mail 1", "mail 2", "mail 3"]
    gaps = [b[0] - a[0] for a, b in zip(done, done[1:])]
    assert min(gaps) >= 0.4 and e.waiting == 0                                    # spaced to the limit, queue empty afterwards

    # if the main server comes back while emails are waiting, they go to it instead of the slow backup
    ok_calls = []

    def main(req):
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        ok_calls.append(1)
        return httpx.Response(200, json={"answers": answer(json.loads(req.content), 0.9)})

    e2 = make(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")), monkeypatch)
    monkeypatch.setattr(config, "LAYA_FALLBACK_KEY", "vck_test")
    monkeypatch.setattr(config, "LAYA_FALLBACK_RPM", 6)                 # one call every ten seconds
    e2._gw = e._gw
    e2._gw_next = _t.time() + 8                                          # an email would have to wait about 8 seconds
    result = []
    t = threading.Thread(target=lambda: result.append(e2.score(mk(5), WHATS, wait=True)))
    t.start(); _t.sleep(0.3)
    e2._client = httpx.Client(base_url=config.LAYA_URL, transport=httpx.MockTransport(main), headers={"Authorization": "x"})
    e2.status = "ready"                                                  # the main server recovers
    t.join(timeout=10)
    assert result and result[0]["invoice"]["score"] == 0.9 and ok_calls
