# EscalAI — Consumer Escalation Agent

A LangGraph-based case manager for consumer complaints, exposed as a self-hosted MCP server (Streamable HTTP) with a web-based simulator standing in for the Alexa+ voice experience.

**It only drafts and tracks. It never sends anything on your behalf, and it is not legal advice.**

## What it does

Walks a consumer complaint through a four-stage escalation ladder:

1. **Seller support** — a direct complaint letter
2. **The platform's grievance officer** — required acknowledgment/redress under the Consumer Protection (E-Commerce) Rules, 2020
3. **The National Consumer Helpline** (1800-11-4000 / consumerhelpline.gov.in)
4. **The Consumer Commission** — a formal filing at e-Jagriti (e-jagriti.gov.in), tiered by claim value (District / State / National Commission)

At each stage it drafts a letter, tracks the real reply-window deadline, and pauses for you to confirm you've sent it. If the window passes with no reply, it escalates automatically. You can stop a case at any point, or reopen a stopped case to pick back up from where you left it — including a case stopped right at the filing decision.

## Why this architecture

Devpost's hackathon FAQ confirms Alexa+'s gated developer tools are **not available to participants** this cycle. A self-hosted MCP server plus your own web-based simulator is the explicitly sanctioned stand-in, judged on equal footing — so the "voice assistant" here is a browser page with mic input and speech output, talking to the same MCP tools an Alexa+ skill would call.

## How it understands you

Free text goes through `/api/understand`, which tries Groq (an LLM) first for natural-language routing and field extraction, and silently falls back to deterministic regex matching if there's no `GROQ_API_KEY` set or the Groq call fails for any reason. The app is fully functional with zero API keys — Groq just makes it understand more varied phrasing.

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on Mac/Linux
pip install -r requirements.txt
python -m escalation_agent.mcp_server
```

Then open **http://127.0.0.1:8000** in a browser.

Optional, for better natural-language understanding:
```bash
$env:GROQ_API_KEY="your-key-here"     # PowerShell
$env:GROQ_MODEL="openai/gpt-oss-120b" # optional override
```

## MCP tools exposed

| Tool | Purpose |
|---|---|
| `start_case` | Open a new complaint case |
| `log_response` | Record sent/replied/resolved/file/stop events |
| `get_next_step` | Current status and days left in the reply window |
| `draft_escalation` | Re-fetch the current letter/case file |
| `simulate_days` | **Demo only** — fast-forward the case clock |
| `reopen_case` | Resume a case you previously stopped |

## Testing

```bash
pytest -v
```

Covers the full escalation ladder, real-clock vs. simulated-clock behavior, the MCP Streamable HTTP endpoint end-to-end, the HTTP API's security hardening (origin checks, content-type checks), and regression tests for every bug found during development (see `FRICTION_LOG.md`).

## Disclaimer

This tool drafts messages and tracks deadlines. It is not legal advice. Stage windows in `rules.yaml` are verified against public sources as of the dates noted there — always confirm current rules/fees on the official sites (e-jagriti.gov.in, consumerhelpline.gov.in) before relying on a deadline or filing.

## License

MIT — see `LICENSE`.

Repo: https://github.com/shrutijagadale0725-rgb/EscalAI