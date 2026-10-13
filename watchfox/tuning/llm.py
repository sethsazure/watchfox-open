"""AI assistant for the people who set WatchFox up. It helps create and tune types and nothing else.

It is NEVER used when catching email: the engine and the runner do not import this module (a test checks that).
It only ever receives text the person typed into the type wizard (a name, a sentence, a description) plus the names of
the built-in email kinds. Real email content, including emails flagged as wrong catches, is never sent to it.
Runs on Azure AI Foundry when AZURE_AI_ENDPOINT and AZURE_AI_KEY are set, otherwise on OpenAI with OPENAI_API_KEY (see .env.example)."""
import json
from typing import Any, Dict, List

import httpx

from .. import config


def available() -> bool:
    return bool((config.AZURE_AI_ENDPOINT and config.AZURE_AI_KEY) or config.OPENAI_API_KEY)


def _chat(prompt: str) -> Dict[str, Any]:
    if config.AZURE_AI_ENDPOINT and config.AZURE_AI_KEY:
        url, headers, model = f"{config.AZURE_AI_ENDPOINT}/openai/v1/chat/completions", {"api-key": config.AZURE_AI_KEY}, config.AZURE_AI_DEPLOYMENT
    else:
        url, headers, model = f"{config.OPENAI_BASE_URL}/chat/completions", {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, config.TUNING_MODEL
    r = httpx.post(url, timeout=90, headers=headers,
                   json={"model": model, "response_format": {"type": "json_object"}, "temperature": 0.8,
                         "messages": [{"role": "system", "content": "You help configure an email classifier. Reply with JSON only. "
                                       "All sample emails are invented, generic and realistic: no real people, companies or personal data."},
                                      {"role": "user", "content": prompt}]})
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _emails(xs: Any, limit: int) -> List[Dict[str, str]]:
    out = []
    for e in xs if isinstance(xs, list) else []:
        if isinstance(e, dict) and str(e.get("body", "")).strip():
            out.append({"subject": str(e.get("subject", ""))[:200], "body": str(e["body"])[:1500]})
    return out[:limit]


STYLE = ("Make them varied: different industries, tones, lengths (some one line, some a full paragraph), some with greetings "
         "and signatures, some written as forwarded or replied threads, a few with typos or terse phrasing.")


def examples(label: str, description: str, positives: int, negatives: int) -> Dict[str, List[Dict[str, str]]]:
    """Invented sample emails: ones that match the type, and hard look-alikes that must not match."""
    prompt = (f"Type name: {label}\nWhat counts: {description}\n\n"
              f"Write {positives} emails that clearly match this type and {negatives} look-alike emails that must NOT match: "
              "same vocabulary or situation, but a different intent (for example a reminder, a confirmation, a complaint, "
              "a marketing message, a reply that mentions it in passing, or an automatic notification). "
              f"{STYLE}\nReturn {{\"positives\": [{{\"subject\":..,\"body\":..}}], \"negatives\": [...]}}.")
    d = _chat(prompt)
    return {"positives": _emails(d.get("positives"), positives), "negatives": _emails(d.get("negatives"), negatives)}


def wordings(label: str, description: str) -> List[str]:
    d = _chat(f"Type name: {label}\nWhat counts: {description}\n\nWrite 5 different yes/no questions about an email whose answer is yes "
              "exactly for this type. Each starts with 'Is' or 'Does', is specific, and names the sender's intent. "
              "Return {\"wordings\": [..]}.")
    return [w.strip() for w in d.get("wordings", []) if isinstance(w, str) and w.strip()][:6]


def similar_kinds(label: str, description: str, kinds: Dict[str, str]) -> List[str]:
    """Which built-in kinds of email resemble this type, so they are not used as 'should ignore' examples."""
    listing = "\n".join(f"{k}: {v}" for k, v in kinds.items())
    d = _chat(f"Type name: {label}\nWhat counts: {description}\n\nKinds of email:\n{listing}\n\n"
              "List the kind ids that are the same as, a part of, or very close to this type (so they must not be treated as "
              "'not this type'). Be inclusive for near matches. Return {\"kinds\": [ids]}.")
    return [k for k in d.get("kinds", []) if k in kinds]


def suggest(sentence: str, kinds: Dict[str, str]) -> Dict[str, Any]:
    """From one sentence, a complete first draft of a type: name, description, examples and look-alikes."""
    prompt = (f"A user wants WatchFox to catch this in their email: \"{sentence}\"\n\n"
              "Return {\"label\": a short name (2-4 words), \"description\": one or two clear sentences saying exactly what counts and what does not, "
              "\"positives\": 8 invented emails that match, \"negatives\": 8 look-alikes that must NOT match "
              "(same situation, different intent)}. Each email is {\"subject\":..,\"body\":..} with 2-4 sentences. " + STYLE)
    d = _chat(prompt)
    out = {"label": str(d.get("label", "")).strip()[:60], "description": str(d.get("description", "")).strip()[:500],
           "positives": _emails(d.get("positives"), 10), "negatives": _emails(d.get("negatives"), 10)}
    try:
        out["similar_kinds"] = similar_kinds(out["label"] or sentence, out["description"] or sentence, kinds)
    except Exception:
        out["similar_kinds"] = []
    return out
