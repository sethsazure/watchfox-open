"""The "what": one yes/no question per thing you want to catch.

`status` is honest about how much testing a preset has had:
  tuned    - checked against real emails
  starter  - sensible question and threshold, not yet tuned on real data
"""
import hashlib
from typing import Any, Dict

PRESETS: Dict[str, Dict[str, Any]] = {
    "enquiry": {
        "label": "Enquiries",
        "description": "Someone asking for a quote, price or proposal (RFQs, RFPs, spec packs).",
        "question": "Is the sender asking us to give a quote or price for work, for example from attached specifications, drawings or a survey?",
        "threshold": 0.38,
        "excludes": [
            {"question": "Is this email an unsolicited marketing message, newsletter, advertisement or automated notification?", "threshold": 0.5},
            {"question": "Is this an invoice, bill, receipt or payment notice?", "threshold": 0.6},
            {"question": "Is the sender trying to sell us something or promote their own services?", "threshold": 0.6},
        ],
        "skip_bulk": True,
        # plain rules on the subject line: documents that are about quotes or orders but are not a request for one
        "ignore_subject": [r"\bpurchase order\b", r"\bPO[-\s#]?\d{2,}", r"\border (confirmation|acknowledg\w*)\b",
                           r"(?<![A-Za-z])Q-?\d{3,}", r"\bprice list\b", r"\bquotation (accepted|attached)\b", r"^\s*quote accepted\b"],
        "status": "tuned",
    },
    "invoice": {
        "label": "Invoices",
        "description": "A bill or invoice asking for payment.",
        "question": "Is the sender sending us invoices: A bill or invoice asking for payment?",
        "threshold": 0.556,
        "skip_bulk": True,
        "status": "tuned",
    },
    "support": {
        "label": "Support tickets",
        "description": "Someone reporting a problem or asking for help with something they already use.",
        "question": "Does the sender write about someone reporting a problem or asking for help with something they already use, and is it the reason for the email?",
        "threshold": 0.53,
        "excludes": [
            {"question": "Is the sender asking for a refund, replacement, compensation or credit?", "threshold": 0.4},
            {"question": "Is the sender asking us to give a quote or price?", "threshold": 0.4},
            {"question": "Is this an invoice, bill, receipt or payment notice?", "threshold": 0.4},
        ],
        "skip_bulk": True,
        "status": "tuned",
    },
    "purchase_order": {
        "label": "Purchase orders",
        "description": "An order placed for goods or services.",
        "question": "Would an employee file this email under 'Purchase orders' (An order placed for goods or services)?",
        "threshold": 0.611,
        "excludes": [
            {"question": "Is this a reminder, confirmation, statement or notice about something that already happened?", "threshold": 0.5},
            {"question": "Is this a meeting invitation, calendar item or scheduling message?", "threshold": 0.4},
            {"question": "Is this an invoice, bill, receipt or payment notice?", "threshold": 0.6},
        ],
        "skip_bulk": True,
        "status": "tuned",
    },
    "job_application": {
        "label": "Job applications",
        "description": "A resume or application for a job.",
        "question": "Is this email about: A resume or application for a job?",
        "threshold": 0.609,
        "skip_bulk": True,
        "status": "tuned",
    },
    "claim": {
        "label": "Claims",
        "description": "Someone claiming a refund, a replacement, compensation or a warranty repair.",
        "question": "Is this email about: Someone claiming a refund, a replacement, compensation or a warranty repair?",
        "threshold": 0.274,
        "excludes": [
            {"question": "Is this email an unsolicited marketing message, newsletter, advertisement or automated notification?", "threshold": 0.4},
        ],
        "skip_bulk": True,
        "status": "tuned",
    },
}


def clean_excludes(xs) -> list:
    """Validate a list of {question, threshold}. Raises ValueError with a message a developer can act on."""
    if xs is None:
        return []
    if not isinstance(xs, list) or len(xs) > 8:
        raise ValueError("excludes must be a list of up to 8 objects like {\"question\": \"...\", \"threshold\": 0.5}.")
    out = []
    for e in xs:
        if not isinstance(e, dict) or not isinstance(e.get("question"), str) or not e["question"].strip():
            raise ValueError("Each exclude needs a text \"question\".")
        t = e.get("threshold", 0.5)
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0.01 <= t <= 0.99:
            raise ValueError("An exclude threshold must be a number between 0.01 and 0.99.")
        out.append({"question": e["question"].strip()[:300], "threshold": float(t)})
    return out


def clean_patterns(xs) -> list:
    """Validate subject rules (regular expressions). Patterns that could take exponential time are refused."""
    import re

    if xs is None:
        return []
    if not isinstance(xs, list) or len(xs) > 20:
        raise ValueError("ignore_subject must be a list of up to 20 regular expressions.")
    for p in xs:
        if not isinstance(p, str) or not p or len(p) > 120:
            raise ValueError("Each ignore_subject rule must be a text regular expression of up to 120 characters.")
        if re.search(r"\([^)]*[+*][^)]*\)\s*[+*{]", p):
            raise ValueError("That ignore_subject pattern repeats a repeated group, which can be very slow. Simplify it.")
        try:
            re.compile(p)
        except re.error:
            raise ValueError(f"ignore_subject rule is not a valid regular expression: {p[:40]}")
    return list(xs)


def clean_custom(w: Dict[str, Any]) -> Dict[str, Any]:
    """An inline 'what' sent by a developer, checked and trimmed."""
    q = w.get("question")
    if not isinstance(q, str) or not q.strip() or len(q) > 400:
        raise ValueError("Each custom 'what' needs a \"question\" (text, up to 400 characters).")
    t = w.get("threshold", 0.5)
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0.01 <= t <= 0.99:
        raise ValueError("threshold must be a number between 0.01 and 0.99.")
    out = {"question": q.strip(), "threshold": float(t), "skip_bulk": bool(w.get("skip_bulk", False)),
           "excludes": clean_excludes(w.get("excludes")), "ignore_subject": clean_patterns(w.get("ignore_subject"))}
    if w.get("exclude_question"):
        if not isinstance(w["exclude_question"], str):
            raise ValueError("exclude_question must be text.")
        et = w.get("exclude_threshold", 0.5)
        if isinstance(et, bool) or not isinstance(et, (int, float)) or not 0.01 <= et <= 0.99:
            raise ValueError("exclude_threshold must be a number between 0.01 and 0.99.")
        out["excludes"] = out["excludes"] + [{"question": w["exclude_question"][:300], "threshold": float(et)}]
    for k in ("id", "label", "description"):
        v = w.get(k)
        if v is not None and not isinstance(v, str):
            raise ValueError(f"{k} must be text.")
        if v:
            out[k] = v[:80 if k != "description" else 300]
    return out


def excludes(w: Dict[str, Any]) -> list:
    """The exclusion questions of a watch: a list of {question, threshold}. Accepts the older single
    exclude_question / exclude_threshold form as well."""
    if w.get("excludes"):
        return [{"question": e["question"], "threshold": float(e.get("threshold", 0.5))} for e in w["excludes"]]
    if (w.get("exclude_question") or "").strip():
        return [{"question": w["exclude_question"], "threshold": float(w.get("exclude_threshold") or 0.5)}]
    return []


def ignore_reason(w: Dict[str, Any], title: str, bulk: bool) -> str:
    """Why a watch skips this email without asking the model: a subject rule or a bulk-mail marker. "" if it does not."""
    import re

    for pattern in w.get("ignore_subject") or []:
        try:
            if re.search(pattern, title or "", re.I):
                return "subject matches an ignore rule"
        except re.error:
            continue
    if w.get("skip_bulk") and bulk:
        return "looks like bulk or automatic mail"
    return ""


def confidence(score: float, threshold: float) -> int:
    """How clearly an email fits, as a percentage anyone can read. It compares the score with the line for a match,
    so a catch just over the line is about 55 to 65 and a clear catch is 85 or more. It is NOT the probability that
    the answer is right: the model's raw scores are not calibrated probabilities."""
    return min(99, max(0, int(round(100 * min(1.0, score / (threshold + 0.3))))))


def confidence_level(pct: int) -> str:
    return "high" if pct >= 85 else "medium" if pct >= 65 else "low"


def all_presets() -> Dict[str, Dict[str, Any]]:
    return {k: dict(v, id=k) for k, v in PRESETS.items()}


def _from_spec(wid: str, t: Dict[str, Any], status: str) -> Dict[str, Any]:
    sp = t["spec"]
    return {"id": wid, "label": t["label"], "description": t["description"], "question": sp["question"],
            "threshold": sp["threshold"], "excludes": sp.get("excludes", []), "skip_bulk": sp.get("skip_bulk", True),
            "ignore_subject": sp.get("ignore_subject", []), "status": status}


def resolve(what, uid: str = "") -> list:
    """Accept "enquiry", ["enquiry", "invoice"], or inline custom dicts; return full watch dicts."""
    presets = all_presets()
    items = [what] if isinstance(what, (str, dict)) else list(what)
    out = []
    for w in items:
        if isinstance(w, str):
            if w.startswith("type_") and uid:
                from . import store

                t = store.type_(w, uid)
                if not t:
                    raise ValueError(f"Unknown type {w!r}. Your types are listed at GET /v1/types.")
                out.append(_from_spec(w, t, "custom"))
                continue
            if w in presets and uid:                     # the user's own re-tuned copy of a default type, if there is one
                from . import store

                t = store.type_("override:" + w, uid)
                if t:
                    out.append(_from_spec(w, t, "tuned for you"))
                    continue
            if w not in presets:
                raise ValueError(f"Unknown 'what': {w!r}. Known: {', '.join(presets)} or one of your own types (GET /v1/types).")
            out.append(presets[w])
        elif isinstance(w, dict) and "question" in w:
            c = clean_custom(w)
            out.append({"label": c.get("label") or c.get("id") or "custom", "status": "custom", **c,
                        "id": c.get("id") or "custom:" + hashlib.sha1(c["question"].encode()).hexdigest()[:8]})
        else:
            raise ValueError("Each 'what' must be a preset id or an object with a 'question'.")
    return out
