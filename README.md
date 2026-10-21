# EscalAI — Consumer Escalation Agent

A LangGraph-based case manager for consumer complaints, exposed as a self-hosted MCP server (Streamable HTTP) with a web-based simulator standing in for the Alexa+ voice experience.

**It only drafts and tracks. It never sends anything on your behalf, and it is not legal advice.**

Built for the **Build, Ship, Shape: Amazon Developer Hackathon** (Alexa+ track).

## Quick start (for judges)

```bash
git clone https://github.com/shrutijagadale0725-rgb/EscalAI.git
cd EscalAI
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on Mac/Linux
pip install -r requirements.txt
python -m escalation_agent.mcp_server
```

Open **http://127.0.0.1:8000**. No API key is needed. Groq is optional and only improves natural-language understanding, and the app is fully functional on its deterministic fallback without it.

Run `python -m escalation_agent.mcp_server` from the folder that contains the `escalation_agent` package.

**A 2-minute tour:** click *New complaint*, describe a problem (try: "My earbuds from Amazon arrived broken, order OD-12345, Rs 2499"), and confirm the details. Tell the agent "I sent it" and the reply window starts. Then type things like "it's been 6 days" or "it's 26th oct" and watch it track the deadline and escalate. You can also turn on *Read aloud* and use the mic button for voice in and out.

## What it does

Walks a consumer complaint through a four-stage escalation ladder:

1. **Seller support**: a direct complaint letter
2. **The platform's grievance officer**: required acknowledgment/redress under the Consumer Protection (E-Commerce) Rules, 2020
3. **The National Consumer Helpline** (1800-11-4000 / consumerhelpline.gov.in)
4. **The Consumer Commission**: a formal filing at e-Jagriti (e-jagriti.gov.in), tiered by claim value (District / State / National Commission)

At each stage it drafts a letter, tracks the real reply-window deadline, and pauses for you to confirm you've sent it. If the window passes with no reply, it escalates automatically. You can stop a case at any point, or reopen a stopped case to pick back up from where you left it, including a case stopped right at the filing decision.

## Why this architecture

Devpost's hackathon FAQ confirms Alexa+'s gated developer tools are **not available to participants** this cycle. A self-hosted MCP server plus your own web-based simulator is the explicitly sanctioned stand-in, judged on equal footing. So the "voice assistant" here is a browser page with mic input and speech output, talking to the same MCP tools an Alexa+ skill would call.

## How it understands you

Free text goes through `/api/understand`, which tries Groq (an LLM) first for natural-language routing and field extraction. It silently falls back to deterministic regex matching if there's no `GROQ_API_KEY` set or the Groq call fails for any reason.

While a case is in the **waiting** stage, a second Groq pass handles free-form check-ins: relative durations ("it's been 8 days"), absolute dates ("it's 23 nov"), and fact-grounded questions ("when will I hear back?"). The model gets a real callable action (`advance_days`) instead of generating free text, and any free-text answer is only accepted if every number in it matches a known fact about the case. Anything it can't safely answer falls back to "not sure, check back" rather than guessing.

Once a case is finished (resolved, stopped or ready to file), closing remarks like "thanks" or "ok" get a friendly reply. They don't start a new complaint, but a real new complaint still does.

## Running with Groq (optional)

```powershell
$env:GROQ_API_KEY="your-key-here"       # PowerShell
$env:GROQ_MODEL="openai/gpt-oss-120b"   # optional override
```

```bash
export GROQ_API_KEY="your-key-here"     # macOS / Linux
```

## MCP tools exposed

| Tool | Purpose |
|---|---|
| `start_case` | Open a new complaint case |
| `log_response` | Record sent/replied/resolved/file/stop events |
| `get_next_step` | Current status and days left in the reply window |
| `draft_escalation` | Re-fetch the current letter/case file |
| `simulate_days` | **Demo only**: fast-forward the case clock |
| `reopen_case` | Resume a case you previously stopped |
| `delete_case` | Permanently delete a case, its letters and its history |
| `list_cases` | List past cases, most recent first |

## The simulator

- **Voice in and out**: a mic button for speech input, optional *Read aloud* with a selectable voice.
- **Case management**: every case is saved and resumable from the sidebar, and each has a delete button (with confirmation) that removes the case and its full LangGraph history.
- **Inline message editing**: hover a message you sent and click the pencil. The bubble turns into an editable box in place, and saving resends from that point. This replays the chat visually and does not rewind actions like "I sent it" that already moved a live case forward.
- **Copy message**: a copy icon on every message.
- Resizable panes, official-channel links at each escalation stage, and a landing page explaining what the agent does and doesn't do with your data.

## Built with

Python, LangGraph (with SqliteSaver persistence), FastAPI/Starlette, the `mcp` Python SDK, Groq, SQLite, and plain HTML/CSS/JavaScript.

## Testing

```bash
pytest -v
```

Covers the full escalation ladder, real-clock vs. simulated-clock behavior, the MCP Streamable HTTP endpoint end-to-end, the HTTP API's security hardening (origin checks, content-type checks), and regression tests for bugs found during development. See [`FRICTION_LOG.md`](FRICTION_LOG.md) for what went wrong along the way and how it was fixed.

## AI assistance

Built with help from Claude (Anthropic, via claude.ai) for architecture design, debugging, code generation and documentation. Groq powers the optional runtime language understanding.

## Disclaimer

This tool drafts messages and tracks deadlines. It is not legal advice. Stage windows in `rules.yaml` are verified against public sources as of the dates noted there. Always confirm current rules and fees on the official sites (e-jagriti.gov.in, consumerhelpline.gov.in) before relying on a deadline or filing.

## License

MIT, see `LICENSE`.