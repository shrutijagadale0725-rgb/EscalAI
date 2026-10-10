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
    issue, amt, days = _sentence(s["issue"]), s["amount"], stage.get("window_days")
    if sid == "seller":
        return (
            f"Subject: Complaint and refund request: {s['product']}, order {s['order_id']}\n\n"
            f"Dear Customer Support Team,\n\n"
            f"I am writing to formally complain about my order of {s['product']} on {s['platform']} ({facts}).\n\n"
            f"Issue: {issue}.\n"
            f"I request a full refund of Rs. {amt} or a replacement within {days} days of this letter, "
            f"and a written confirmation of the action you will take.\n\n"
            f"Attached: invoice and photos showing the problem.\n"
            f"If this is not resolved, I will escalate to your Grievance Officer and, if needed, "
            f"to the National Consumer Helpline and the Consumer Commission.\n\n"
            f"Regards,\n{sender}"
        )
    if sid == "grievance_officer":
        return (
            f"Subject: Formal complaint to the Grievance Officer: order {s['order_id']}, Rs. {amt}\n\n"
            f"To the Grievance Officer,\n{s['platform']}\n\n"
            f"I am escalating my unresolved complaint about {s['product']} ({facts}).\n\n"
            f"Issue: {issue}.\n"
            f"Steps already taken, without resolution:\n{hist}\n\n"
            f"Under the Consumer Protection (E-Commerce) Rules, 2020, a Grievance Officer must acknowledge "
            f"a complaint within 48 hours and redress it within one month of receipt. I therefore request that you:\n"
            f"1. Acknowledge this complaint in writing within 48 hours.\n"
            f"2. Refund Rs. {amt} in full, or replace the product, within {days} days.\n\n"
            f"If this is not resolved in that time, I will escalate to the National Consumer Helpline and "
            f"file a complaint before the Consumer Commission through e-Jagriti. "
            f"I am keeping a record of all communication.\n\n"
            f"Regards,\n{sender}"
        )
    if sid == "helpline":
        return (
            f"Subject: Consumer complaint against {s['platform']}: order {s['order_id']}, Rs. {amt}\n\n"
            f"To the National Consumer Helpline,\n\n"
            f"I request your help with a complaint that the company has not resolved.\n\n"
            f"Company: {s['platform']}\n"
            f"Product: {s['product']}\n"
            f"Order: {s['order_id']}\n"
            f"Amount paid: Rs. {amt}\n"
            f"Issue: {issue}.\n"
            f"Steps already taken, without resolution:\n{hist}\n"
            f"Relief sought: a full refund of Rs. {amt} or a replacement.\n\n"
            f"Please take this up with the company on my behalf. If it stays unresolved, "
            f"I intend to file a case before the Consumer Commission through e-Jagriti.\n\n"
            f"Regards,\n{sender}"
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
    tier_line = f"Likely forum, based on claim value: {tier}.\n" if tier else ""
    bought = f"Purchased on: {s['purchase_date']}\n" if s.get("purchase_date") else ""
    return (
        "CASE FILE (for filing with the Consumer Commission)\n\n"
        f"Complainant: {s.get('name') or '[Your name]'}\n"
        f"Opposite party: {s['platform']}\n\n"
        f"Product: {s['product']}\nOrder: {s['order_id']}\n{bought}"
        f"Amount paid and claimed: Rs. {s['amount']}\n\n"
        f"Issue: {_sentence(s['issue'])}.\n\n"
        f"Chronology:\n{tl}\n\n"
        f"Relief sought: refund of Rs. {s['amount']} or replacement of the product, "
        "plus any compensation you wish to claim.\n\n"
        "Documents to attach: invoice, photos, and every letter and reply listed in the chronology.\n\n"
        f"{tier_line}"
        "File at e-Jagriti (e-jagriti.gov.in), the current portal for consumer complaints.\n"
        "Exact fees and jurisdiction still depend on claim value. Verify current fees and the "
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
                    {"role": "system", "content": "Rewrite this consumer complaint letter so it is firm, formal, concise and easy to act on. Keep every fact, date, number, order ID, deadline and timeline line exactly as written. Do not add facts, laws, section numbers or threats that are not already in the letter. Keep the subject line, numbered requests and sign-off. Return only the letter."},
                    {"role": "user", "content": text}]},
                timeout=20,
            )
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return text
        must = (s["order_id"], str(s["amount"]), *_history(s))
        return out if all(m in out for m in must) else text

    return polish

def _sentence(text):
    text = (text or "").strip().rstrip(".!")
    return (text[:1].upper() + text[1:]) if text else text