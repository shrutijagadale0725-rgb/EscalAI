"""MCP server (Streamable HTTP). Tool replies include a short, speakable `say` line for voice."""
import inspect
import os
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

from starlette.responses import FileResponse, JSONResponse

from . import nlu
from .service import CaseService

mcp = MCPServer(
    "consumer-escalation-agent",
    instructions=("Case manager for consumer complaints. Start a case, answer its questions, and log events. "
                  "It drafts letters but never sends anything. It is not legal advice."),
)
svc = CaseService(os.getenv("ESCALATION_DB", "cases.db"))
NOT_FOUND = {"error": "case_not_found", "say": "I can't find that case."}


@mcp.tool()
def start_case(product: str, issue: str, platform: str = "", order_id: str = "",
               amount: str = "", purchase_date: str = "", name: str = "") -> dict:
    """Open a complaint case. Missing platform, order_id or amount are asked for next."""
    return svc.start(product, issue, platform, order_id, amount, purchase_date, name)


@mcp.tool()
def log_response(case_id: str, event: str, platform: str = "", order_id: str = "",
                 amount: str = "", note: str = "") -> dict:
    """Record what happened. event is one of: answer, sent, seller_replied, resolved, file, stop."""
    payloads = {
        "answer": {"platform": platform, "order_id": order_id, "amount": amount},
        "sent": {"action": "sent"},
        "seller_replied": {"action": "reply", "text": note},
        "resolved": {"action": "resolved"},
        "file": {"action": "file"},
        "stop": {"action": "stop"},
    }
    if event not in payloads:
        return {"error": "bad_event", "say": "I didn't catch that. Was it sent, a reply, resolved, file or stop?"}
    return svc.respond(case_id, payloads[event]) or NOT_FOUND


@mcp.tool()
def get_next_step(case_id: str) -> dict:
    """Current status, days left in the reply window, and what the agent needs next."""
    return svc.refresh(case_id) or NOT_FOUND


@mcp.tool()
def draft_escalation(case_id: str) -> dict:
    """The current drafted letter (or case file) for the user to copy and send."""
    v = svc.view(case_id)
    return v if v else NOT_FOUND

@mcp.tool()
def list_cases(limit: int = 20) -> dict:
    """List past cases, most recent first."""
    return {"cases": svc.list_cases(limit)}

@mcp.tool()
def simulate_days(case_id: str, days: int = 8) -> dict:  # days is validated by MCP; HTTP path is sanitised in the service
    """DEMO ONLY: jump the case clock forward so the demo does not need real waiting."""
    return svc.respond(case_id, {"action": "advance", "days": days}) or NOT_FOUND

@mcp.tool()
def delete_case(case_id: str) -> dict:
    """Permanently delete a case, its letters and its history. Cannot be undone."""
    if not svc.delete(case_id):
        return NOT_FOUND
    return {"case_id": case_id, "deleted": True, "say": "That complaint has been deleted."}


@mcp.tool()
def reopen_case(case_id: str) -> dict:
    """Re-activate a case the user previously stopped, so it can be worked again from the same stage."""
    result = svc.reopen(case_id)
    if result is not None:
        return result
    current = svc.view(case_id)
    if current is None:
        return NOT_FOUND
    return {**current, "error": "cannot_reopen",
            "say": "I can't reopen that one — it either resolved or was already filed, not stopped."}

TOOLS = {f.__name__: f for f in (start_case, log_response, get_next_step, draft_escalation, simulate_days, reopen_case, delete_case, list_cases)}
WEB = Path(__file__).resolve().parent.parent / "web"


@mcp.custom_route("/", methods=["GET"])
async def simulator_page(request):
    """The voice-style simulator front end (no Alexa device needed)."""
    return FileResponse(WEB / "simulator.html")


@mcp.custom_route("/api/understand", methods=["POST"])
async def understand_route(request):
    """What the simulator page's text box and mic call: free text in, one tool call out."""
    origin, host = request.headers.get("origin"), request.headers.get("host", "")
    if origin and urlparse(origin).netloc != host:
        return JSONResponse({"error": "forbidden_origin", "say": "That request came from another site."}, status_code=403)
    if "application/json" not in request.headers.get("content-type", ""):
        return JSONResponse({"error": "json_required", "say": "That request was not valid."}, status_code=415)
    try:
        body = await request.json()
    except Exception:
        body = {}
    message = str((body or {}).get("message", "")).strip()
    if not isinstance(body, dict) or not message:
        return JSONResponse({"error": "empty_message", "say": "I didn't catch that."}, status_code=400)

    status, case_id, awaiting_name = body.get("status"), body.get("case_id"), bool(body.get("awaiting_name"))
    if status == "waiting" and case_id and not awaiting_name:
        # Deterministic routing only while waiting. Groq's general router sometimes misreads
        # a bare date/number ("it's 16 oct") as "the user is answering a missing field" and
        # silently calls log_response with nothing useful in it -- a no-op that masks the
        # waiting-stage agent below entirely. The regex matches here are unambiguous and
        # already proven reliable (sent/resolved/stop/replied/skip/letter); anything that
        # doesn't match one of those goes to the waiting-stage agent instead of the general
        # router's guesswork.
        tool, args = nlu.regex_route(message, case_id, status)
    else:
        tool, args = nlu.understand(message, case_id, status, awaiting_name)
        if tool == "__name__":
            return JSONResponse({"name": args.get("name")})

    if tool == "start_case" and body.get("name"):
        args = {**args, "name": body["name"]}

    fn = TOOLS.get(tool)
    if fn is None:
        return JSONResponse({"error": "unknown_tool", "say": "I didn't understand that."}, status_code=502)

    # If the model's extracted args are missing something required, fall back to the
    # deterministic extractor instead of crashing on a TypeError.
    sig = inspect.signature(fn)
    missing = [n for n, p in sig.parameters.items() if p.default is inspect.Parameter.empty and n not in args]
    if missing:
        tool, args = nlu.regex_route(message, body.get("case_id"), body.get("status"))
        if tool == "start_case" and body.get("name"):
            args = {**args, "name": body["name"]}
        fn = TOOLS.get(tool)

    # Waiting-stage agent: this must run AFTER the missing-args fallback above, since that
    # fallback is itself a common way to land on get_next_step (a tool choice re-routed here
    # for a missing required arg). Checking only the original `tool` before that fallback
    # would mean this branch gets skipped even on a plain get_next_step case.
    override_say = None
    if tool == "get_next_step" and status == "waiting" and args.get("case_id"):
        current = svc.view(args["case_id"])
        if current and current["status"] == "waiting" and current["days_left"] is not None:
            state = {"day": current["day"], "days_left": current["days_left"], "stage": current["stage"]}
            outcome = nlu.try_waiting_reply(message, state)
            if outcome:
                kind, payload = outcome
                if kind == "advance":
                    tool, args = "simulate_days", {"case_id": args["case_id"], "days": payload}
                    fn = TOOLS.get(tool)
                elif kind == "say":
                    override_say = payload

    # Belt and suspenders: a null here (from the model, or any future caller) must not
    # reach a tool's .strip() call and crash -- drop it so the tool's own default applies.
    args = {k: v for k, v in args.items() if k in inspect.signature(fn).parameters and v is not None}
    try:
        result = fn(**args)
    except Exception:
        return JSONResponse({"error": "server_error", "say": "Something went wrong on my side."}, status_code=500)
    if override_say:
        result = {**result, "say": override_say}
    return JSONResponse({**result, "tool": tool, "args": args})


@mcp.custom_route("/api/tool/{name}", methods=["POST"])
async def call_tool_http(request):
    """Lets the simulator page call the same tool functions the MCP endpoint exposes."""
    origin, host = request.headers.get("origin"), request.headers.get("host", "")
    if origin and urlparse(origin).netloc != host:  # block other websites from driving a local server
        return JSONResponse({"error": "forbidden_origin", "say": "That request came from another site."}, status_code=403)
    if "application/json" not in request.headers.get("content-type", ""):
        return JSONResponse({"error": "json_required", "say": "That request was not valid."}, status_code=415)
    fn = TOOLS.get(request.path_params["name"])
    if fn is None:
        return JSONResponse({"error": "unknown_tool", "say": "I don't know that action."}, status_code=404)
    try:
        args = await request.json()
    except Exception:
        args = {}
    if not isinstance(args, dict) or set(args) - set(inspect.signature(fn).parameters):
        return JSONResponse({"error": "bad_arguments", "say": "That request was missing something."}, status_code=400)
    try:
        return JSONResponse(fn(**args))
    except Exception:
        return JSONResponse({"error": "server_error", "say": "Something went wrong on my side."}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")