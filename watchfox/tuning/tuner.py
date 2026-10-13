"""The standard tuner: turns a plain-language definition of a type into a question, a threshold and exclusions.

Steps, the same ones used to tune Enquiries:
  1. candidate wordings of the yes/no question (templates, plus optional drafts from the AI assistant)
  2. score every labelled email against every candidate, pick the wording that separates yes from no best (AUC)
  3. choose the threshold that keeps every real catch with the fewest false alarms
  4. for false alarms that remain, find an exclusion question that removes them without losing a real catch
  5. report the result, including the mistakes that are left

Labelled emails come from the built-in library (34 kinds of everyday email) plus any examples the user supplies.
"""
from typing import Any, Dict, List, Sequence, Tuple

from ..engine import engine
from ..items import Item
from .library import CATEGORY_INFO, LIBRARY

Example = Tuple[str, str, str]          # (subject, sender, body)

# which built-in kinds of email count as "yes" for each default type (used when a default type is re-tuned)
PRESET_KINDS = {"enquiry": ["quote_request"], "invoice": ["invoice", "payment_reminder"], "support": ["support"],
                "purchase_order": ["purchase_order"], "job_application": ["job_application"], "claim": ["claim"]}

EXCLUSION_BANK = [
    "Is this email an unsolicited marketing message, newsletter, advertisement or automated notification?",
    "Is this an invoice, bill, receipt or payment notice?",
    "Is the sender trying to sell us something or promote their own services?",
    "Is this a reminder, confirmation, statement or notice about something that already happened?",
    "Is this a meeting invitation, calendar item or scheduling message?",
    "Is this an automatic reply, delivery failure or system message?",
    "Is the sender asking for a refund, replacement, compensation or credit?",
    "Is the sender asking us to give a quote or price?",
    "Is the sender expressing dissatisfaction or giving feedback without asking for anything to be fixed?",
]


def candidate_wordings(label: str, description: str, extra: Sequence[str] = ()) -> List[str]:
    d = description.strip().rstrip(".")
    base = [
        f"Is this email about: {d}?",
        f"Is the sender sending us {label.lower()}: {d}?",
        f"Is the main purpose of this email to deliver or request the following: {d}?",
        f"Does the sender write about {d[0].lower() + d[1:]}, and is it the reason for the email?",
        f"Would an employee file this email under '{label}' ({d})?",
    ]
    seen, out = set(), []
    for q in list(extra) + base:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _item(i: int, ex: Example) -> Item:
    return Item(id=str(i), title=ex[0], sender=ex[1], text=ex[2], kind="email")


def _scores(examples: Sequence[Example], questions: Sequence[str]) -> List[List[float]]:
    """scores[email][question], every question answered in a single pass per email (batched when the model is remote)."""
    whats = [{"id": f"q{i}", "label": "q", "question": q, "threshold": 0.5} for i, q in enumerate(questions)]
    results = engine.score_many([_item(i, ex) for i, ex in enumerate(examples)], whats)
    return [[res[f"q{k}"]["score"] for k in range(len(questions))] for res in results]


def auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    if not pos or not neg:
        return 0.0
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def best_threshold(pos: Sequence[float], neg: Sequence[float]) -> Tuple[float, int, int]:
    """Threshold that catches every positive when possible (missing is worse than a false alarm), then fewest
    false alarms. Falls back to the best compromise. Returns (threshold, missed, false_alarms)."""
    cands = sorted(set(round(x, 3) for x in list(pos) + list(neg)))
    best = None
    for t in [0.05 * k for k in range(1, 20)] + cands:
        missed = sum(p < t for p in pos)
        fa = sum(n >= t for n in neg)
        key = (missed * 3 + fa, -t)                  # a miss counts three false alarms; prefer the higher line on ties
        if best is None or key < best[0]:
            best = (key, t, missed, fa)
    t = best[1]
    below = max([n for n in neg if n < t], default=None)
    above = min([p for p in pos if p >= t], default=None)
    if below is not None and above is not None and above > below:
        t = round((below + above) / 2, 3)             # sit in the middle of the gap, not on a sample
    return round(t, 3), sum(p < t for p in pos), sum(n >= t for n in neg)


def _words(text: str) -> set:
    return {w[:5] for w in __import__("re").findall(r"[a-z]{4,}", text.lower())} - {"email", "sende", "someo", "about", "which", "their", "there", "using", "asking", "sendi", "thing"}


def _median(xs: Sequence[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def tune(label: str, description: str, positives: Sequence[Example] = (), negatives: Sequence[Example] = (),
         categories: Sequence[str] = (), llm_wordings: Sequence[str] = (), progress=None,
         skip_kinds: Sequence[str] = ()) -> Dict[str, Any]:
    """Tune a type. `categories` names library kinds that count as this type (the rest of the library are the
    negatives). Examples the user supplies are added to the right side. Returns the settings and a report."""
    pos = [(s, f, b) for c, s, f, b in LIBRARY if c in categories] + list(positives)
    lib = [(c, (s, f, b)) for c, s, f, b in LIBRARY if c not in categories]
    neg = [e for _, e in lib] + list(negatives)
    kind_of = [c for c, _ in lib] + [None] * len(negatives)          # None = supplied by the user: always kept
    if len(pos) < 3:
        raise ValueError("Give at least 3 example emails that should be caught (more is better).")
    questions = candidate_wordings(label, description, llm_wordings)
    allq = questions + EXCLUSION_BANK
    say = progress or (lambda m: None)
    say(f"Scoring {len(pos) + len(neg)} sample emails against {len(allq)} questions")
    P, N = _scores(pos, allq), _scores(neg, allq)
    nq = len(questions)

    # Library kinds that look like this type must not be used as negatives (a "Meeting invites" type must not be
    # tuned against the library's meeting emails). A kind is left out when its name shares words with the type, or
    # when its emails score close to the user's positives on the wording that separates the library best.
    own = _words(label)
    k0 = max(range(nq), key=lambda k: _median([r[k] for r in P]) - _median([N[i][k] for i, c in enumerate(kind_of) if c]))
    pm = _median([r[k0] for r in P])
    dropped = [c for c in skip_kinds if c in kind_of]
    for c in sorted({c for c in kind_of if c and c not in dropped}):
        rows_c = [N[i][k0] for i, kc in enumerate(kind_of) if kc == c]
        info = CATEGORY_INFO.get(c, (c, ""))
        if (own & _words(info[0])) or (_median(rows_c) > 0.5 and _median(rows_c) >= pm):
            dropped.append(c)
    keep = [i for i, c in enumerate(kind_of) if c not in dropped]
    neg, N = [neg[i] for i in keep], [N[i] for i in keep]

    rows = []
    for k in range(nq):
        ps, ns = [r[k] for r in P], [r[k] for r in N]
        t, missed, fa = best_threshold(ps, ns)
        rows.append({"question": questions[k], "auc": round(auc(ps, ns), 3), "threshold": t, "missed": missed,
                     "false_alarms": fa})
    rows.sort(key=lambda r: (-r["auc"], r["missed"] * 3 + r["false_alarms"]))
    top = rows[0]
    k = questions.index(top["question"])
    threshold = top["threshold"]

    # exclusions: for the remaining false alarms look for a bank question that removes them and keeps every catch
    fa_idx = [i for i, r in enumerate(N) if r[k] >= threshold]
    excludes, removed = [], set()
    for _ in range(3):
        best = None
        for e, q in enumerate(EXCLUSION_BANK):
            col = nq + e
            for et in (0.4, 0.5, 0.6, 0.7):
                if any(P[i][col] >= et for i in range(len(P)) if P[i][k] >= threshold):
                    continue                          # would lose a real catch
                gain = [i for i in fa_idx if i not in removed and N[i][col] >= et]
                if gain and (best is None or len(gain) > len(best[0])):
                    best = (gain, {"question": q, "threshold": et})
        if not best or len(best[0]) < 1:
            break
        excludes.append(best[1])
        removed.update(best[0])
    left = [i for i in fa_idx if i not in removed]
    missed = [i for i, r in enumerate(P) if r[k] < threshold]
    mistakes = ([{"kind": "missed", "subject": pos[i][0], "score": round(P[i][k], 2)} for i in missed] +
                [{"kind": "false alarm", "subject": neg[i][0], "score": round(N[i][k], 2)} for i in left])
    return {
        "label": label, "description": description, "question": top["question"], "threshold": threshold,
        "excludes": excludes, "skip_bulk": True,
        "inputs": {"positives": [{"subject": a, "body": c} for a, _, c in positives],
                   "negatives": [{"subject": a, "body": c} for a, _, c in negatives],
                   "treat_as_yes": list(categories), "skip_kinds": list(skip_kinds)},
        "report": {
            "samples": {"should_catch": len(pos), "should_ignore": len(neg)},
            "left_out_as_similar": [CATEGORY_INFO[c][0] for c in dropped if c in CATEGORY_INFO],
            "own_look_alikes": len(negatives),
            "caught": len(pos) - len(missed), "missed": len(missed),
            "false_alarms_before_exclusions": len(fa_idx), "false_alarms_after": len(left),
            "separation": top["auc"], "wordings_tried": rows, "mistakes": mistakes,
            "note": "Measured on built-in sample emails plus the examples you gave. Check it on your own mail "
                    "before relying on it, and add examples of the mistakes to tune again.",
        },
    }
