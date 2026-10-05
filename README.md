# Consumer Escalation Agent

A case manager for consumer complaints, exposed as an MCP server (Streamable HTTP).
It walks a complaint up an escalation ladder (seller support, grievance officer,
National Consumer Helpline, Consumer Commission), drafts each letter, waits out the
reply window, and escalates with the full history attached. State is saved in SQLite,
so a case survives restarts and days of waiting.

It drafts and tracks. It never sends anything. It is not legal advice.

## Run

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    python -m pytest -q
    python -m escalation_agent.mcp_server      # MCP endpoint: http://127.0.0.1:8000/mcp

Then open http://127.0.0.1:8000/ for the voice simulator (chat box, mic button and spoken replies).
The simulator calls the tool functions directly over a lightweight HTTP endpoint (`/api/understand`), so the
browser demo needs no MCP session handling or SSE parsing. The actual MCP server — the one any real MCP client,
including judges testing this submission, connects to — is the `/mcp` endpoint, built on the MCP Python SDK's
Streamable HTTP transport (see `mcp_server.py`). The side panel shows each tool call made by the demo page.
The mic uses the browser's built-in speech recognition (Chrome or Edge).

Optional: set `GROQ_API_KEY` to have Groq polish the letters (falls back to templates).

## Tools

| Tool | What it does |
|------|--------------|
| `start_case` | Open a case; asks for any missing platform, order ID or amount |
| `log_response` | Record `answer`, `sent`, `seller_replied`, `resolved`, `file` or `stop` |
| `get_next_step` | Status, days left in the reply window, what is needed next (also re-reads the real clock and escalates when a window has passed) |
| `draft_escalation` | The current letter or case file |
| `simulate_days` | Demo only: adds days on top of the real elapsed time |

Every reply includes a short `say` line meant to be spoken by a voice assistant.

## Alexa+ track

Alexa+ gated developer tools are not available to participants, so this is a self-hosted MCP
server (Streamable HTTP) plus a web simulator that stands in for the voice experience.
See `FRICTION_LOG.md`.

## How it works

Each case is one LangGraph thread. Every wait (missing details, "have you sent it?",
the reply window, the "is filing worth it?" gate) is an interrupt, and progress is
checkpointed with `SqliteSaver`. The clock only starts once the user confirms the
letter was sent. Case days are real elapsed days plus any demo skew.

## Status

- Stage windows in `rules.yaml` are **unverified placeholders**. Check each against the
  official source before relying on them.
- Groq polishing is implemented but not tested against the live API.
