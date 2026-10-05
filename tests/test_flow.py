import pytest

from escalation_agent.service import CaseService


@pytest.fixture
def svc(tmp_path):
    return CaseService(str(tmp_path / "t.db"), polish=None)


def ready(svc):
    return svc.start("earbuds", "The left bud is broken.", "Amazon", "OD-123", "2499", "12 Sep")


def test_asks_for_missing_fields_then_drafts(svc):
    v = svc.start("earbuds", "Broken on arrival.")
    assert v["status"] == "collecting" and "platform" in v["ask"]
    v = svc.respond(v["case_id"], {"platform": "Amazon", "order_id": "OD-1", "amount": "2499"})
    assert v["status"] == "draft_ready" and "OD-1" in v["draft"]


def test_must_send_before_clock_starts(svc):
    cid = ready(svc)["case_id"]
    v = svc.respond(cid, {"action": "advance", "days": 8})
    assert v["status"] == "draft_ready" and v["day"] == 0  # advance ignored until the letter is sent


def test_full_ladder_to_case_file(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    v = svc.respond(cid, {"action": "advance", "days": 8})
    assert v["stage"].endswith("grievance officer") and v["status"] == "draft_ready"
    assert "Sent to seller support" in v["draft"]  # escalation cites prior contact
    svc.respond(cid, {"action": "sent"})
    v = svc.respond(cid, {"action": "advance", "days": 30})  # grievance officer's real 30-day window
    assert v["stage"] == "the National Consumer Helpline" and v["status"] == "draft_ready"
    svc.respond(cid, {"action": "sent"})
    v = svc.respond(cid, {"action": "advance", "days": 45})  # helpline's real 45-day window
    assert v["status"] == "gate"  # worth-it pause before the commission
    v = svc.respond(cid, {"action": "file"})
    assert v["status"] == "ready_to_file" and "CASE FILE" in v["draft"]


def test_partial_wait_does_not_escalate(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    v = svc.respond(cid, {"action": "advance", "days": 3})
    assert v["status"] == "waiting" and v["days_left"] == 4


def test_resolved_early_and_stop_at_gate(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    assert svc.respond(cid, {"action": "resolved"})["status"] == "resolved"
    cid2 = ready(svc)["case_id"]
    for _ in range(3):
        svc.respond(cid2, {"action": "sent"})
        svc.respond(cid2, {"action": "advance", "days": 20})
    assert svc.respond(cid2, {"action": "stop"})["status"] == "closed"


def test_state_survives_restart(tmp_path):
    db = str(tmp_path / "p.db")
    a = CaseService(db, polish=None)
    cid = a.start("earbuds", "Broken.", "Amazon", "OD-9", "999")["case_id"]
    a.respond(cid, {"action": "sent"})
    b = CaseService(db, polish=None)  # fresh process, same database
    v = b.respond(cid, {"action": "advance", "days": 8})
    assert v["day"] == 8 and v["status"] == "draft_ready"


def test_say_lines_are_speakable(svc):
    cid = ready(svc)["case_id"]
    svc.respond(cid, {"action": "sent"})
    v = svc.respond(cid, {"action": "advance", "days": 3})
    assert v["say"] == "It has been 3 days since you wrote to seller support. They have 4 more days to reply."


def test_http_api_and_simulator_page(tmp_path, monkeypatch):
    monkeypatch.setenv("ESCALATION_DB", str(tmp_path / "api.db"))
    from starlette.testclient import TestClient
    from escalation_agent import mcp_server
    client = TestClient(mcp_server.mcp.streamable_http_app())
    assert client.get("/").status_code == 200
    r = client.post("/api/tool/start_case", json={"product": "earbuds", "issue": "broken",
                    "platform": "Amazon", "order_id": "OD-1", "amount": "999"})
    assert r.status_code == 200 and r.json()["status"] == "draft_ready"
    assert client.post("/api/tool/nope", json={}).status_code == 404