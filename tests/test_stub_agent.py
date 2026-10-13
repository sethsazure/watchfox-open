"""The stand-in agent used in the quick start gives sensible answers (no model, no network)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("stub_agent", Path(__file__).resolve().parents[1] / "examples" / "stub_agent.py")
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)


def test_stub_separates_an_invoice_from_a_newsletter():
    invoice = "Please find attached invoice 2291 for $2,450. Amount due in 30 days. Payment terms net 30."
    news = "This week in logistics. Unsubscribe any time. Join our webinar."
    q = "Is this email an invoice or a bill asking for payment?"
    assert stub.score(q, invoice) > 0.6 > stub.score(q, news)
    assert stub.score("Is this an automatic notice, newsletter or marketing email?", news) > 0.4


def test_stub_answers_batches_in_the_server_format():
    qs = {"w0": {"type": "noul", "instructions": "Is this a request for a quote or price?"}}
    out = stub.answers({"subject": "RFQ", "body": "Please send a quote and your best price."}, qs)
    assert 0 < out["w0"]["noul"] <= 0.95 and out["w0"]["type"] == "noul"
