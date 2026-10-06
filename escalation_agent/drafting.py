"""Letter drafting. Templates always work offline; Groq polishing is optional."""
import os


def _facts(s):
    parts = [f"order {s['order_id']}", f"Rs. {s['amount']}"]
    if s.get("purchase_date"):
        parts.append(f"purchased on {s['purchase_date']}")
    return ", ".join(parts)


def _history(s):
    return [t for t in s.get("timeline", []) if "Sent to" in t]


def draft_letter(s, stage, sender="[Your name]"):
    sid, facts = stage["id"], _facts(s)
    hist = "\n".join(f"- {h}" for h in _history(s)) or "- (none yet)"
    head = f"Regarding: {s['product']} ({facts}) on {s['platform']}"
    if sid == "seller":
        return (
            f"Subject: Complaint about {s['product']}, order {s['order_id']}\n\n"
            f"Dear Customer Service Team,\n\n"
            f"I am writing to report a problem with my recent order of {s['product']} "
            f"on {s['platform']} ({facts}). {_sentence(s['issue'])}.\n\n"
            f"I would appreciate a full refund or a replacement within {stage['window_days']} days. "
            f"Photos and the invoice are attached for your reference.\n\n"
            f"Regards,\n{sender}"
        )
    if sid == "grievance_officer":
        return (
            f"Subject: Unresolved complaint, order {s['order_id']}\n\n"
            f"To the Grievance Officer, {s['platform']},\n\n{head}.\n\n{s['issue']}\n\n"
            f"I have already contacted the seller with no resolution:\n{hist}\n\n"
            f"Please acknowledge this complaint and resolve it within {stage['window_days']} days.\n\n"
            f"Regards,\n{sender}"
        )
    if sid == "helpline":
        return (
            f"Complaint summary for the National Consumer Helpline\n\n{head}\n\n"
            f"Problem: {s['issue']}\n\nSteps already taken:\n{hist}\n\n"
            f"Relief sought: refund of Rs. {s['amount']} or replacement.\n\n{sender}"
        )
    return case_file(s)


def _commission_tier(amount):
    """Which Consumer Commission tier applies, by claim value.

    Thresholds are from the Consumer Protection (Jurisdiction of the District Commission,
    the State Commission and the National Commission) Rules, 2021. Verify against
    e-jagriti.gov.in before filing in case these are revised again.
    """
    try:
        value = float(str(amount).replace(",", "").strip() or 0)
    except ValueError:
        return None
    if value <= 50_00_000:
        return "District Commission (claims up to Rs 50 lakh)"
    if value <= 2_00_00_000:
        return "State Commission (Rs 50 lakh to Rs 2 crore)"
    return "National Commission (above Rs 2 crore)"


def case_file(s):
    tl = "\n".join(f"- {t}" for t in s.get("timeline", []))
    tier = _commission_tier(s.get("amount"))
    tier_line = f" Based on this claim value, that likely means the {tier}." if tier else ""
    return (
        "CASE FILE (for filing with the Consumer Commission)\n\n"
        f"Product: {s['product']}\nPlatform: {s['platform']}\nOrder: {s['order_id']}\n"
        f"Amount claimed: Rs. {s['amount']}\nProblem: {s['issue']}\n\nTimeline:\n{tl}\n\n"
        "Attach: invoice, photos, and every letter listed above.\n"
        f"File at e-Jagriti (e-jagriti.gov.in), the current portal for consumer complaints.{tier_line}\n"
        "Exact fees and jurisdiction still depend on claim value — verify current fees and the "
        "correct commission on e-jagriti.gov.in before filing. This is not legal advice."
    )


def make_polisher():
    """Return a Groq-backed polish function, or None if no key is set. Untested against the live API."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    import httpx

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")  # llama-3.3-70b-versatile was deprecated by Groq on 2026-08-16
    def polish(text, s):
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "temperature": 0.2, "messages": [
                    {"role": "system", "content": "Rewrite the letter to be polite, firm and clear. Keep every fact, date, number and order ID exactly. Add no new facts. Return only the letter."},
                    {"role": "user", "content": text}]},
                timeout=20,
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return text
        return out if all(m in out for m in (s["order_id"], str(s["amount"]))) else text

    return polish

def _sentence(text):
    text = (text or "").strip().rstrip(".!")
    return (text[:1].upper() + text[1:]) if text else text