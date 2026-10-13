"""Fast tests that need no engine. Run: pip install pytest && pytest"""
import os
import tempfile
import time

os.environ["WATCHFOX_HOME"] = tempfile.mkdtemp()

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from watchfox import config, limiter, presets, vault  # noqa: E402
from watchfox.items import from_bytes  # noqa: E402
from watchfox.server import check_webhook_url  # noqa: E402
from watchfox.sources import registry  # noqa: E402


def test_password_and_key_hashing():
    h = vault.hash_password("correct horse battery")
    assert vault.verify_password("correct horse battery", h) and not vault.verify_password("wrong", h)
    full, prefix, kh = vault.new_api_key()
    assert full.startswith("wf_live_") and full.startswith(prefix) and vault.hash_key(full) == kh and full not in kh


def test_encryption_roundtrip_and_signing():
    assert vault.decrypt(vault.encrypt({"a": 1})) == {"a": 1}
    tok = vault.sign({"uid": "u1"}, 60)
    assert vault.verify(tok)["uid"] == "u1" and vault.verify(tok + "x") is None
    assert vault.verify(vault.sign({"uid": "u1"}, -1)) is None


def test_email_parsing():
    it = from_bytes(b"From: a@b.example\nSubject: RFQ 1\nContent-Type: text/plain\n\nPlease quote 5 units.\n", "x.eml")
    assert it.title == "RFQ 1" and it.sender == "a@b.example" and it.kind == "email"


def test_presets():
    assert len(presets.all_presets()) == 6 and presets.resolve("enquiry")[0]["id"] == "enquiry"
    assert presets.resolve({"question": "Is it urgent?"})[0]["threshold"] == 0.5
    with pytest.raises(ValueError):
        presets.resolve("nope")


def test_only_email_sources_are_public():
    assert set(registry()) == {"gmail", "m365", "imap", "sandbox"}


def test_webhook_url_rules(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_INSECURE_WEBHOOKS", False)
    for bad in ("http://example.com/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "ftp://example.com", "not a url"):
        with pytest.raises(HTTPException):
            check_webhook_url(bad)
    monkeypatch.setattr(config, "ALLOW_INSECURE_WEBHOOKS", True)
    check_webhook_url("http://127.0.0.1:9000/hook")


def test_rate_limiter():
    for _ in range(3):
        limiter.hit("t", "k", 3, 60)
    with pytest.raises(HTTPException) as e:
        limiter.hit("t", "k", 3, 60)
    assert e.value.status_code == 429


def test_subject_rules_and_bulk_detection():
    from watchfox.items import is_bulk

    w = presets.all_presets()["enquiry"]
    for ignored in ("Purchase Order PO-5521", "PO 99-1204 attached", "Quotation Q-4471 for your request", "Price list October",
                    "Re: Quote Q-2026-88", "Order confirmation 7B-4410"):
        assert presets.ignore_reason(w, ignored, False), ignored
    for kept in ("RFQ-2026-118: Door operators", "Quotation request", "FW: RFQ - 214 Maple St", "Request for Quotation Q1 2026", "Fw: 40 Baif"):
        assert not presets.ignore_reason(w, kept, False), kept
    assert presets.ignore_reason(w, "Weekly news", True) == "looks like bulk or automatic mail"
    assert is_bulk({"List-Unsubscribe": "<mailto:x@y.example>"}, "Hi", "hello")
    assert is_bulk({"Auto-Submitted": "auto-replied"}, "Out of office", "away")
    assert is_bulk({}, "News", "Click here to unsubscribe") and not is_bulk({}, "Fw: RFQ", "unsubscribe")
    assert not is_bulk({}, "Quote please", "Can you quote 40 units?")


def test_several_exclusion_questions_are_normalised():
    assert len(presets.excludes(presets.all_presets()["enquiry"])) == 3
    assert presets.excludes({"exclude_question": "x?", "exclude_threshold": 0.4}) == [{"question": "x?", "threshold": 0.4}]


def test_confidence_scale():
    assert presets.confidence(0.38, 0.38) == 56 and presets.confidence(0.64, 0.38) == 94 and presets.confidence(0.9, 0.5) == 99
    assert presets.confidence(0.05, 0.38) < 15 and presets.confidence(0.5, 0.5) < presets.confidence(0.6, 0.5)
    assert [presets.confidence_level(p) for p in (56, 65, 84, 85, 99)] == ["low", "medium", "medium", "high", "high"]
