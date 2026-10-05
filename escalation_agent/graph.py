"""The LangGraph case workflow. Every wait is an interrupt, so state survives across days."""
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .drafting import case_file, draft_letter

REQUIRED = ["platform", "order_id", "amount"]
FIELDS = REQUIRED + ["product", "issue", "purchase_date", "name"]


class CaseState(TypedDict, total=False):
    case_id: str
    product: str
    issue: str
    platform: str
    order_id: str
    amount: str
    purchase_date: str
    name: str
    stage: int
    day: int
    stage_start: int
    created_at: float
    skew: int  # demo days added by simulate_days, on top of real elapsed time
    sent: bool
    draft: str
    timeline: list
    status: str  # collecting | drafting | draft_ready | waiting | escalating | gate | ready_to_file | resolved | closed


def _days(v, default=0):
    """Coerce a user/tool-supplied day count to a non-negative int (bad input never reaches the graph)."""
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return default


def build_graph(rules, checkpointer, polish=None, clock=time.time):
    stages = rules["stages"]

    def today(s):
        """Case day = real elapsed days since opening + demo skew."""
        return int((clock() - s.get("created_at", clock())) // 86400) + s.get("skew", 0)

    def cur(s):
        return stages[s["stage"]]

    def log(s, text):
        return s["timeline"] + [f"Day {today(s)} · {text}"]

    def resolved(s):
        return {"status": "resolved", "timeline": log(s, "Resolved")}

    def closed(s):
        return {"status": "closed", "timeline": log(s, "Closed by the user without filing")}

    def intake(s):
        missing = [f for f in REQUIRED if not s.get(f)]
        if not missing:
            return {"status": "drafting"}
        reply = interrupt({"ask": "I still need: " + ", ".join(missing) + ".", "missing": missing})
        return {k: str(v).strip() for k, v in (reply or {}).items() if k in FIELDS and v and str(v).strip()}

    def draft(s):
        text = draft_letter(s, cur(s), sender=s.get("name") or "[Your name]")
        if polish:
            text = polish(text, s)
        return {"draft": text, "status": "draft_ready", "sent": False, "day": today(s)}

    def await_send(s):
        reply = interrupt({"ask": f"The letter to {cur(s)['name']} is ready. Tell me once you have sent it."}) or {}
        if reply.get("action") == "sent":
            return {"sent": True, "stage_start": today(s), "day": today(s), "status": "waiting",
                    "timeline": log(s, f"Sent to {cur(s)['name']}")}
        if reply.get("action") == "resolved":
            return resolved(s)
        if reply.get("action") == "stop":
            return closed(s)
        return {}

    def wait(s):
        st = cur(s)
        left = max(0, st["window_days"] - (today(s) - s["stage_start"]))
        reply = interrupt({"ask": f"Waiting on {st['name']}. {left} days left in the reply window."}) or {}
        act = reply.get("action")
        if act == "resolved":
            return resolved(s)
        if act == "stop":
            return closed(s)
        out = {}
        if act == "advance":
            out["skew"] = s.get("skew", 0) + _days(reply.get("days", 1))
        elif act == "reply":
            out["timeline"] = log(s, f"Reply from {st['name']}: {reply.get('text', '')}".strip().rstrip(":"))
        # any resume (including a plain "check") re-reads the real clock
        day = today({**s, **out})
        out["day"] = day
        out["status"] = "escalating" if day - s["stage_start"] >= st["window_days"] else "waiting"
        return out

    def next_stage(s):
        nxt = s["stage"] + 1
        return {"stage": nxt, "sent": False,
                "status": "gate" if stages[nxt].get("is_filing") else "drafting",
                "timeline": log(s, f"No reply from {cur(s)['name']}, escalating")}

    def gate(s):
        reply = interrupt({"ask": (f"The Consumer Commission is a formal filing and takes real effort. "
                                   f"Is it worth it for Rs. {s['amount']}? Say file or stop.")}) or {}
        act = reply.get("action")
        if act == "file":
            return {"draft": case_file(s), "status": "ready_to_file", "timeline": log(s, "Case file prepared")}
        if act == "stop":
            return closed(s)
        if act == "resolved":
            return resolved(s)
        return {}

    g = StateGraph(CaseState)
    for name, fn in [("intake", intake), ("draft", draft), ("await_send", await_send),
                     ("wait", wait), ("next_stage", next_stage), ("gate", gate)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, lambda s: "gate" if s.get("_resume") == "gate" else "intake")
    g.add_conditional_edges("intake", lambda s: "draft" if all(s.get(f) for f in REQUIRED) else "intake")
    g.add_edge("draft", "await_send")
    g.add_conditional_edges("await_send", lambda s: {"waiting": "wait", "resolved": END, "closed": END}.get(s["status"], "await_send"))
    g.add_conditional_edges("wait", lambda s: {"escalating": "next_stage", "resolved": END, "closed": END}.get(s["status"], "wait"))
    g.add_conditional_edges("next_stage", lambda s: "gate" if s["status"] == "gate" else "draft")
    g.add_conditional_edges("gate", lambda s: END if s["status"] in ("ready_to_file", "closed", "resolved") else "gate")
    return g.compile(checkpointer=checkpointer)