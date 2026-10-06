"""Thin service layer: one case = one LangGraph thread, persisted in SQLite."""
import re
import sqlite3
import time
import uuid
from pathlib import Path

import yaml
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .drafting import make_polisher
from .graph import build_graph

DONE = ("resolved", "closed", "ready_to_file")


def _tidy_platform(p):
    p = (p or "").strip()
    return p.title() if p.islower() else p


def _d(n):
    return "day" if n == 1 else "days"


def _tidy_amount(a):
    """'Rs. 2,499' / '₹2499.50' -> '2499' / '2499.50'. Empty if there is no number."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(a or ""))
    return m.group(0).replace(",", "") if m else ""


def _clean(payload):
    p = {k: (v.strip() if isinstance(v, str) else v) for k, v in payload.items()}
    if "amount" in p:
        p["amount"] = _tidy_amount(p["amount"])
    if p.get("platform"):
        p["platform"] = _tidy_platform(p["platform"])
    if p.get("action") == "advance":
        p["days"] = p.get("days", 1)
    return p


class CaseService:
    def __init__(self, db_path="cases.db", rules_path=None, polish="auto", clock=time.time):
        rules_path = rules_path or Path(__file__).resolve().parent.parent / "rules.yaml"
        self.rules = yaml.safe_load(Path(rules_path).read_text())
        self.clock = clock
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS case_index (case_id TEXT PRIMARY KEY, name TEXT, "
            "product TEXT, platform TEXT, order_id TEXT, amount TEXT, created_at REAL)")
        self._conn.commit()
        saver = SqliteSaver(self._conn)
        self.graph = build_graph(self.rules, saver, make_polisher() if polish == "auto" else polish, clock)

    def _cfg(self, case_id):
        return {"configurable": {"thread_id": case_id}}

    def start(self, product, issue, platform="", order_id="", amount="", purchase_date="", name=""):
        platform = _tidy_platform(platform)
        order_id = order_id.strip()
        amount = _tidy_amount(amount)

        # Reuse an existing open case for the same order instead of creating a duplicate.
        existing = self._conn.execute(
            "SELECT case_id FROM case_index WHERE product=? AND platform=? AND order_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (product.strip(), platform, order_id)).fetchone()
        if existing:
            v = self.view(existing[0])
            if v and v["status"] not in DONE:
                return v   # resume the live case instead of starting a new one

        cid = "c" + uuid.uuid4().hex[:12]
        state = {"case_id": cid, "product": product.strip(), "issue": issue.strip(),
                "platform": platform, "order_id": order_id,
                "amount": amount, "purchase_date": purchase_date.strip(), "name": name.strip(),
                "stage": 0, "day": 0, "stage_start": 0, "created_at": self.clock(), "skew": 0, "sent": False, "draft": "",
                "timeline": ["Day 0 · Case opened"], "status": "collecting"}
        self.graph.invoke(state, self._cfg(cid))
        self._conn.execute(
            "INSERT INTO case_index (case_id, name, product, platform, order_id, amount, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, name.strip(), product.strip(), platform, order_id, amount, self.clock()))
        self._conn.commit()
        return self.view(cid)

    def respond(self, case_id, payload):
        snap = self.graph.get_state(self._cfg(case_id))
        if not snap.values:
            return None
        payload = _clean(payload)
        before = self._fingerprint(snap.values)
        if snap.values["status"] not in DONE:
            self.graph.invoke(Command(resume=payload), self._cfg(case_id))
        v = self.view(case_id)
        if payload.get("action") in ("advance", "file", "sent", "stop") and \
                before == self._fingerprint(self.graph.get_state(self._cfg(case_id)).values):
            v["say"] = "That doesn't apply right now. " + v["say"]
        return v

    def refresh(self, case_id):
        """Re-read the real clock: escalates a waiting case whose reply window has passed."""
        snap = self.graph.get_state(self._cfg(case_id))
        if snap.values and snap.values["status"] == "waiting":
            self.graph.invoke(Command(resume={"action": "check"}), self._cfg(case_id))
        return self.view(case_id)
    
    def reopen(self, case_id):
        """Re-activate a case the user stopped, from the same stage it was stopped at."""
        snap = self.graph.get_state(self._cfg(case_id))
        if not snap.values or snap.values.get("status") != "closed":
            return None  # nothing to reopen -- either no such case, or it resolved/was filed instead
        v = dict(snap.values)
        is_filing = self.rules["stages"][v["stage"]].get("is_filing", False)
        day = int((self.clock() - v.get("created_at", self.clock())) // 86400) + v.get("skew", 0)
        v["timeline"] = v["timeline"] + [f"Day {day} · Reopened by the user"]
        if is_filing:
            v["status"], v["_resume"] = "gate", "gate"
        else:
            v["status"], v["_resume"] = "drafting", None
        self.graph.invoke(v, self._cfg(case_id))
        return self.view(case_id)

    @staticmethod
    def _fingerprint(v):
        return (v.get("status"), v.get("stage"), v.get("day"), len(v.get("timeline", [])))

    def view(self, case_id):
        snap = self.graph.get_state(self._cfg(case_id))
        if not snap.values:
            return None
        v = dict(snap.values)
        ask = next((i.value.get("ask") for t in snap.tasks for i in t.interrupts), None)
        st = self.rules["stages"][v["stage"]]
        left = None
        if v["status"] == "waiting":
            left = max(0, st["window_days"] - (v["day"] - v["stage_start"]))
        return {"case_id": case_id, "status": v["status"], "stage": st["name"], "day": v["day"],
                "days_left": left, "ask": ask, "say": self._say(v, st, ask, left),
                "draft": v.get("draft", ""), "timeline": v["timeline"]}

    @staticmethod
    def _say(v, st, ask, left):
        s = v["status"]
        if s == "draft_ready":
            return f"Your letter to {st['name']} is ready. Send it, then tell me."
        if s == "waiting":
            gone = v["day"] - v["stage_start"]
            if gone == 0:
                return f"Sent. {st['name'][0].upper() + st['name'][1:]} has {left} {_d(left)} to reply."
            return (f"It has been {gone} {_d(gone)} since you wrote to {st['name']}. "
                    f"They have {left} more {_d(left)} to reply.")
        if s == "resolved":
            return "Marked as resolved. Glad it worked out."
        if s == "gate":
            return ask
        if s == "ready_to_file":
            return "Your case file is ready to file."
        if s == "closed":
            return "Case closed without filing."
        return ask or "Working on it."

    def list_cases(self, limit=20):
        """Past cases, most recent first, with their live status."""
        rows = self._conn.execute(
            "SELECT case_id, name, product, platform, order_id, amount FROM case_index "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for cid, name, product, platform, order_id, amount in rows:
            v = self.view(cid)
            if not v:
                continue
            out.append({"case_id": cid, "name": name, "product": product, "platform": platform,
                        "order_id": order_id, "amount": amount, "status": v["status"], "stage": v["stage"]})
        return out