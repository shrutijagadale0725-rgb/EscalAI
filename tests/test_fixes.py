import json
import pytest
from escalation_agent.service import CaseService
from escalation_agent import nlu

class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def days(self, n):
        self.t += n * 86400


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock):
    return CaseService(str(tmp_path / "f.db"), polish=None, clock=clock)


def ready(svc, **kw):
    a = dict(product="earbuds", issue="Broken.", platform="amazon", order_id="OD-1", amount="2499")
    a.update(kw)
    return svc.start(**a)


def test_bad_days_value_does_not_brick_case(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    v = svc.respond(cid, {"action": "advance", "days": "abc"})  # used to raise ValueError forever
    assert v["status"] == "waiting"
    assert svc.respond(cid, {"action": "advance", "days": 8})["status"] == "draft_ready"


def test_stop_works_at_every_stage(svc):
    cid = ready(svc)["case_id"]
    assert svc.respond(cid, {"action": "stop"})["status"] == "closed"  # before sending
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    assert svc.respond(cid, {"action": "stop"})["status"] == "closed"  # while waiting


def test_real_clock_escalates_without_simulate(svc, clock):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    clock.days(3)
    v = svc.refresh(cid)
    assert v["status"] == "waiting" and v["days_left"] == 4
    clock.days(5)
    v = svc.refresh(cid)
    assert v["status"] == "draft_ready" and v["stage"].endswith("grievance officer")


def test_amount_and_blank_fields_are_cleaned(svc):
    assert "Rs. Rs." not in ready(svc, amount="Rs. 2,499")["draft"] and "Rs. 2499" in svc.view(ready(svc, amount="₹2,499")["case_id"])["draft"]
    assert ready(svc, order_id="   ")["status"] == "collecting"
    assert ready(svc, amount="free")["status"] == "collecting"


def test_ignored_action_is_explained(svc):
    cid = ready(svc)["case_id"]
    v = svc.respond(cid, {"action": "advance", "days": 8})  # letter not sent yet
    assert v["say"].startswith("That doesn't apply right now.")


def test_say_lines_grammar(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    assert "1 more day to reply" in svc.respond(cid, {"action": "advance", "days": 6})["say"]
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    svc.respond(cid, {"action": "advance", "days": 8})  # clears seller's 7-day window
    svc.respond(cid, {"action": "sent"})
    svc.respond(cid, {"action": "advance", "days": 30})  # clears grievance officer's 30-day window
    assert svc.respond(cid, {"action": "sent"})["say"].startswith("Sent. The National Consumer Helpline")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    os.environ.setdefault("ESCALATION_DB", str(tmp_path_factory.mktemp("m") / "m.db"))
    from starlette.testclient import TestClient
    from escalation_agent import mcp_server
    with TestClient(mcp_server.mcp.streamable_http_app(), base_url="http://127.0.0.1:8000") as c:
        yield c


def test_http_api_is_hardened(client):
    body = {"product": "x", "issue": "y", "platform": "Amazon", "order_id": "OD-1", "amount": "9"}
    assert client.post("/api/tool/start_case", content=json.dumps(body), headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/tool/start_case", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/tool/start_case", json={"nope": 1}).status_code == 400
    cid = client.post("/api/tool/start_case", json=body).json()["case_id"]
    client.post("/api/tool/log_response", json={"case_id": cid, "event": "sent"})
    r = client.post("/api/tool/simulate_days", json={"case_id": cid, "days": "abc"})
    assert r.status_code == 200 and r.json()["status"] == "waiting"


def test_real_mcp_endpoint_end_to_end(client):
    """What the simulator page does: initialize, then tools/call over Streamable HTTP."""
    H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def rpc(msg, sid=None):
        return client.post("/mcp", json=msg, headers={**H, **({"mcp-session-id": sid} if sid else {})})

    r = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    sid = r.headers["mcp-session-id"]
    assert rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid).status_code == 202

    def call(name, args):
        r = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}, sid)
        data = next(l for l in r.text.splitlines() if l.startswith("data:"))[5:]
        return json.loads(json.loads(data)["result"]["content"][0]["text"])

    v = call("start_case", {"product": "earbuds", "issue": "broken", "platform": "amazon", "order_id": "OD-7", "amount": "Rs 1,999"})
    assert v["status"] == "draft_ready" and "1999" in v["draft"].replace(",", "").replace(".", "")
    assert call("log_response", {"case_id": v["case_id"], "event": "sent"})["status"] == "waiting"
    assert call("get_next_step", {"case_id": v["case_id"]})["days_left"] == 7


def test_sender_name_used_in_letter(tmp_path):
    from escalation_agent.service import CaseService
    svc = CaseService(str(tmp_path / "n.db"), polish=None)
    v = svc.start("earbuds", "Broken.", "Amazon", "OD-1", "100", name="Shruti")
    assert v["draft"].rstrip().endswith("Shruti")
    v2 = svc.start("earbuds", "Broken.", "Amazon", "OD-2", "100")
    assert "[Your name]" in v2["draft"]

def test_understand_route_matches_the_simulator_page(client):
    """The web page's text box and mic call /api/understand. It was missing entirely (404) until now."""
    r = client.post("/api/understand", json={"message": "I'm Shruti", "case_id": None, "status": None, "awaiting_name": True})
    assert r.status_code == 200 and r.json() == {"name": "Shruti"}

    body = {"message": "My earbuds from Amazon arrived broken, order OD-4471, Rs 2499",
            "case_id": None, "status": None, "awaiting_name": False, "name": "Shruti"}
    r = client.post("/api/understand", json=body)
    d = r.json()
    assert r.status_code == 200 and d["tool"] == "start_case" and d["status"] == "draft_ready" and "Shruti" in d["draft"]
    cid = d["case_id"]

    r = client.post("/api/understand", json={"message": "I sent it", "case_id": cid, "status": d["status"], "awaiting_name": False})
    d2 = r.json()
    assert d2["tool"] == "log_response" and d2["status"] == "waiting"

    r = client.post("/api/understand", json={"message": "skip 8 days", "case_id": cid, "status": d2["status"], "awaiting_name": False})
    d3 = r.json()
    assert d3["tool"] == "simulate_days" and d3["status"] == "draft_ready"

    assert client.post("/api/understand", content='{"message":"hi"}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/understand", json={"message": "hi"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/understand", json={"message": ""}).status_code == 400

def _to_gate(svc, cid):
    """Advance a fresh case all the way to the filing gate."""
    svc.respond(cid, {"action": "sent"})
    svc.respond(cid, {"action": "advance", "days": 8})   # clears seller's 7-day window
    svc.respond(cid, {"action": "sent"})
    svc.respond(cid, {"action": "advance", "days": 30})  # clears grievance officer's window
    svc.respond(cid, {"action": "sent"})
    svc.respond(cid, {"action": "advance", "days": 45})  # clears helpline's window -> gate


def test_reopen_resumes_mid_ladder_stage(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    assert svc.respond(cid, {"action": "stop"})["status"] == "closed"
    v = svc.reopen(cid)
    assert v["status"] == "draft_ready" and v["stage"] == "seller support"
    assert "OD-1" in v["draft"]


def test_reopen_refused_when_resolved_or_filed(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    svc.respond(cid, {"action": "resolved"})
    assert svc.reopen(cid) is None  # resolved, not stopped -- nothing to reopen

    cid2 = ready(svc, order_id="OD-2")["case_id"]
    _to_gate(svc, cid2)
    svc.respond(cid2, {"action": "file"})
    assert svc.reopen(cid2) is None  # filed, not stopped


def test_reopen_at_filing_gate_reasks_instead_of_crashing(svc):
    """Regression test: reopening a case stopped at the filing gate used to be
    refused outright (that stage has no window_days for the normal draft/wait
    replay to crash on). It now resumes straight back into the gate question."""
    cid = ready(svc)["case_id"]
    _to_gate(svc, cid)
    assert svc.respond(cid, {"action": "stop"})["status"] == "closed"
    v = svc.reopen(cid)
    assert v["status"] == "gate" and "file or stop" in v["say"]
    assert svc.respond(cid, {"action": "file"})["status"] == "ready_to_file"


def test_regex_route_handles_existing_case_not_just_new_complaints():
    """Regression test: regex_route once always returned start_case regardless of
    case_id, because the case_id guard + its return were misplaced, leaving every
    branch below (reopen/skip/resolved/sent/file/stop/show/get_next_step) dead code."""
    tool, args = nlu.regex_route("stop", "c123", "waiting")
    assert tool == "log_response" and args == {"case_id": "c123", "event": "stop"}

    tool, args = nlu.regex_route("file it", "c123", "gate")
    assert tool == "log_response" and args["event"] == "file"

    tool, args = nlu.regex_route("show my letter", "c123", "draft_ready")
    assert tool == "draft_escalation"

    tool, args = nlu.regex_route("reopen it", "c123", "closed")
    assert tool == "reopen_case" and args == {"case_id": "c123"}

    tool, args = nlu.regex_route("get status", "c123", "waiting")
    assert tool == "get_next_step"


def test_extract_fields_never_returns_none():
    """Regression test: extract_fields used to fall off the end without a return,
    giving None whenever bare=False -- which crashed start_case's **-unpacking."""
    out = nlu.extract_fields("my earbuds from Amazon arrived broken, Rs 2499")
    assert out is not None and out["amount"] == "2499" and out["platform"] == "Amazon"

    out2 = nlu.extract_fields("2499", bare=True)
    assert out2.get("amount") == "2499"

    assert nlu.extract_fields("nothing useful here") == {}


def test_regex_route_new_complaint_strips_platform_and_facts_from_issue():
    tool, args = nlu.regex_route("my earbuds from Amazon arrived broken, order OD-123, Rs 2499", None, None)
    assert tool == "start_case"
    assert args["product"] == "earbuds"
    assert "OD-123" not in args["issue"] and "2499" not in args["issue"]


def test_draft_escalation_includes_status_for_show_letter(client):
    """Regression test: draft_escalation used to omit `status`, which made the
    client's showResult() silently reset done=False after a 'show my letter'."""
    body = {"product": "x", "issue": "y", "platform": "Amazon", "order_id": "OD-10", "amount": "9"}
    cid = client.post("/api/tool/start_case", json=body).json()["case_id"]
    r = client.post("/api/tool/draft_escalation", json={"case_id": cid})
    d = r.json()
    assert d["status"] == "draft_ready" and d["draft"]


def test_reopen_case_tool_refuses_filed_case_without_losing_status(client):
    """Regression test: reopen_case's failure reply also used to omit `status`,
    which corrupted client state for everything typed after a failed reopen."""
    body = {"product": "x", "issue": "y", "platform": "Amazon", "order_id": "OD-11", "amount": "9"}
    cid = client.post("/api/tool/start_case", json=body).json()["case_id"]
    client.post("/api/tool/log_response", json={"case_id": cid, "event": "sent"})
    client.post("/api/tool/log_response", json={"case_id": cid, "event": "resolved"})
    r = client.post("/api/tool/reopen_case", json={"case_id": cid})
    d = r.json()
    assert d["error"] == "cannot_reopen" and d["status"] == "resolved"