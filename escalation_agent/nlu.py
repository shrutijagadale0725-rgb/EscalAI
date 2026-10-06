"""Turns free text into a tool call.

Tries Groq function-style extraction first; falls back to deterministic regex when
there is no GROQ_API_KEY, the call fails, or the model's answer doesn't validate
against the real tool list. The regex path is what already shipped, so the demo
still works for free with zero setup.

The Groq path is NOT tested against the live API in this environment (no network
access to api.groq.com here) -- test it yourself once GROQ_API_KEY is set, before
you rely on it for a demo.
"""
import json
import os
import re

PLATFORMS = ["amazon", "flipkart", "myntra", "meesho", "ajio", "nykaa", "swiggy", "zomato", "blinkit", "croma"]
GREETING = re.compile(r"^(?:hey|hi|hello|hiya|yo|sup)\b[,!.]*\s*", re.I)
NAME_LEAD = re.compile(r"^(?:my name is|i am|i'm|im|this is|it's|call me)\s+", re.I)
NAME_SHAPE = re.compile(r"^[a-z][a-z .'-]{0,38}$", re.I)
SKIP_WORD = re.compile(r"^(skip|no|nope|later|don'?t)\b", re.I)
COMPLAINT = re.compile(r"\b(broken|damaged|refund|order|not|stopped|wrong|late|delay\w*|defective|arrived|"
                        r"delivered|received|never|missing|fake|scam|working|issue|problem)\b", re.I)
CMD = re.compile(r"\b(sent|mailed|emailed|submitted|skip|advance|letter|draft|show|resolved|fixed|stop|file)\b", re.I)
ORDER_RE = re.compile(r"\b([a-z]{1,4}-?\d{3,})\b", re.I)
_NUM = r"\d[\d,]*(?:\.\d+)?"
# Amount can come before the currency word (Rs 2,499) or after it (2499rs, 5000 rupees).
# This must run BEFORE ORDER_RE, or a bare suffix amount like "rs5000" gets swallowed
# whole as an order id (ORDER_RE matches letters+digits with no required separator).
AMOUNT_RE = re.compile(
    rf"(?:(?:rs\.?|₹|inr)\s*({_NUM})|(?<![a-z0-9-])({_NUM})\s*(?:rs\b|rupees\b|inr\b|₹))", re.I)

TOOL_SCHEMAS = [
    {"name": "start_case", "when": "the user is describing a new problem for the first time",
     "args": "product, issue, platform, order_id, amount, purchase_date"},
    {"name": "log_response", "when": "the user answers missing details, says they sent the letter, says "
                                     "the seller replied, says it's resolved, or says file/stop at a decision point",
     "args": "case_id, event (answer|sent|seller_replied|resolved|file|stop), platform, order_id, amount, note"},
    {"name": "get_next_step", "when": "the user is asking for a status update", "args": "case_id"},
    {"name": "draft_escalation", "when": "the user wants to see the current letter", "args": "case_id"},
    {"name": "simulate_days", "when": "the user wants to skip/advance the clock (demo only)", "args": "case_id, days"},
    {"name": "reopen_case", "when": "the case is closed and the user wants to reopen it, look into it again, "
                                     "or says the same issue has come back", "args": "case_id"},
]


def extract_fields(text, bare=False):
    """bare=True is used on the follow-up 'give me the missing details' turn, where a lone
    number (no Rs/currency word) is very likely the amount, e.g. '2499 rupees' or just '2499'."""
    out = {}
    low = text.lower()
    for p in PLATFORMS:
        if p in low:
            out["platform"] = p.capitalize()
            break
    rest = text
    m = AMOUNT_RE.search(rest)
    if m:
        out["amount"] = (m.group(1) or m.group(2)).replace(",", "")
        rest = rest[:m.start()] + " " + rest[m.end():]
    elif bare:
        m = re.search(r"\b(\d{3,})\b", rest)
        if m:
            out["amount"] = m.group(1)
            rest = rest[:m.start()] + " " + rest[m.end():]
    m = ORDER_RE.search(rest)
    if m:
        out["order_id"] = m.group(1).upper()
        rest = rest[:m.start()] + " " + rest[m.end():]
    return out

def _strip_issue(text, fields):
    """Drop the order-id/amount fragments the message already supplied as structured fields,
    so the drafted letter's problem line doesn't just repeat Order/Amount again."""
    rest = text
    for pattern in (AMOUNT_RE, ORDER_RE):
        m = pattern.search(rest)
        if m:
            rest = rest[:m.start()] + rest[m.end():]
    rest = re.sub(r"\border\b", "", rest, flags=re.I)
    rest = re.sub(r"[,\s]+$", "", rest.strip())
    rest = re.sub(r"\s{2,}", " ", rest).strip(" ,.")
    return rest or text.strip()

def regex_route(message, case_id, status):
    low = message.lower()
    if not case_id:
        m = (re.search(r"my (.+?) (?:arrived|came|is|was|stopped|has|are|were|got|broke|from)\b", low)
            or re.search(r"\b(?:ordered|bought|purchased|received)\s+(?:a|an|some|few)?\s*(.+?)"
                   r"(?:\s+(?:from|on|via|through|but|and|which|that|is|was|arrived|came)\b|[.,!]|$)", low))
        fields = extract_fields(message)
        return "start_case", {"product": (m.group(1).strip() if m else "item")[:40],
                               "issue": _strip_issue(message, fields), **fields}
    base = {"case_id": case_id}
    if status == "closed" and re.search(r"\b(reopen|re-?open|look into it again|same issue again|continue (this|it))\b", low):
        return "reopen_case", base
    days = re.search(r"(\d+)\s*days?", low)
    if re.search(r"\b(skip|jump|advance|fast[- ]?forward)\b", low):
        return "simulate_days", {**base, "days": int(days.group(1)) if days else 8}
    if re.search(r"\b(resolved|refund(ed)? (received|came|done)|got (my )?refund|fixed)\b", low):
        return "log_response", {**base, "event": "resolved"}
    if re.search(r"\b(sent|mailed|emailed|submitted|posted)\b", low):
        return "log_response", {**base, "event": "sent"}
    if status == "gate" and re.search(r"\b(file|yes)\b", low):
        return "log_response", {**base, "event": "file"}
    if re.search(r"\b(stop|drop it|cancel|close)\b", low):
        return "log_response", {**base, "event": "stop"}
    if re.search(r"\b(replied|responded|they said)\b", low):
        return "log_response", {**base, "event": "seller_replied", "note": message.strip()}
    if re.search(r"\b(letter|draft|show)\b", low):
        return "draft_escalation", base
    if status == "collecting":
        return "log_response", {**base, "event": "answer", **extract_fields(message, bare=True)}
    return "get_next_step", base

def regex_name(text):
    """Returns a capitalised name, 'SKIP', or None (not name-shaped -> treat as a complaint)."""
    g = GREETING.sub("", text.strip().lower())
    if SKIP_WORD.match(g):
        return "SKIP"
    w = re.sub(r"[,.!]+$", "", NAME_LEAD.sub("", g)).strip()
    if NAME_SHAPE.match(w) and len(w.split()) <= 4 and not COMPLAINT.search(w) and not CMD.search(w):
        return w.title()
    return None

def _groq_json(system, user, key, model):
    import httpx  # imported lazily so a missing key never requires this dependency
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=15,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def groq_name(text, key, model):
    system = ('Extract a person\'s name from the message, if any is actually given. '
              'Reply ONLY as JSON: {"name": "Their Name"} or {"name": null} if they declined or gave no name. '
              "Never invent a name that was not stated. Title-case it.")
    data = _groq_json(system, text, key, model) or {}
    name = data.get("name")
    return name.strip().title() if isinstance(name, str) and name.strip() else None


def groq_route(text, case_id, status, key, model):
    system = ("You are the natural-language front end for a consumer-complaint agent. Choose exactly one tool "
               "for the user's message. Use ONLY facts the user actually stated -- never invent an order id, "
               "amount or platform. Reply ONLY as JSON: {\"tool\": \"<name>\", \"args\": {...}}. Tools:\n"
               + "\n".join(f"- {t['name']} ({t['args']}): use when {t['when']}" for t in TOOL_SCHEMAS)
               + f"\nCurrent case_id: {case_id or 'none yet'}. Current status: {status or 'none'}.")
    data = _groq_json(system, text, key, model) or {}
    tool = data.get("tool")
    # Drop nulls: the model returns JSON null for fields it is unsure of, and a None
    # reaching a tool's .strip() call would crash instead of falling back to "".
    args = {k: v for k, v in (data.get("args") or {}).items() if v is not None}
    if tool not in {t["name"] for t in TOOL_SCHEMAS}:
        raise ValueError(f"model chose an unknown tool: {tool!r}")
    if case_id and tool != "start_case":
        args.setdefault("case_id", case_id)
    return tool, args


def understand(message, case_id, status, awaiting_name):
    """Returns ('__name__', {'name': str|None}) during the name step, else (tool_name, args)."""
    key, model = os.getenv("GROQ_API_KEY"), os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    if awaiting_name:
        name, got_name = None, False
        if key:
            try:
                name, got_name = groq_name(message, key, model), True
            except Exception:
                pass
        if not got_name:
            r = regex_name(message)
            name = None if r == "SKIP" else r

        # The same message may also describe the problem -- don't discard that.
        if COMPLAINT.search(message) or extract_fields(message):
            try:
                tool, args = (groq_route(message, None, None, key, model) if key
                              else regex_route(message, None, None))
            except Exception:
                tool, args = regex_route(message, None, None)
            if tool == "start_case":
                if name:
                    args["name"] = name
                return tool, args

        return "__name__", {"name": name}
    if key:
        try:
            return groq_route(message, case_id, status, key, model)
        except Exception:
            pass
    return regex_route(message, case_id, status)