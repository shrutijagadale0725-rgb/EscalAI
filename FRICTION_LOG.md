# Friction Log

Notes on real obstacles hit while building this for the Amazon Developer Hackathon's Alexa+ track.

## Alexa+ developer tools are gated, and unavailable to participants

The most significant friction: Alexa+'s own developer tooling is not accessible to hackathon participants this cycle. Confirmed directly against the hackathon's own FAQ rather than assumed. The sanctioned alternative — a self-hosted MCP server (Streamable HTTP) plus your own web-based simulator standing in for the voice device — is what this project builds, and the FAQ states this is judged on equal footing with the gated path. Still, it's a real gap between "build an Alexa+ skill" as advertised and what's actually buildable without special access.

## `mcp` package API changed between major versions

The Python `mcp` package renamed `FastMCP` to `MCPServer` and moved it from `mcp.server.fastmcp` to `mcp.server.mcpserver` between versions. Most public examples and tutorials still reference the old import path, which fails silently with a confusing `ModuleNotFoundError` rather than a deprecation warning.

## Groq model deprecation mid-build

`llama-3.3-70b-versatile`, the model used in early development, was deprecated by Groq on 2026-08-16. Had to switch to `openai/gpt-oss-120b`, configurable via `GROQ_MODEL` so it's a one-line env var change rather than a code change if Groq deprecates again.

## Groq-dependent code couldn't be tested in the dev sandbox

The development environment had no outbound network access to `api.groq.com`, so every Groq-dependent code path (NLU routing, letter polishing) had to be written defensively — wrapped in try/except with a silent fallback to deterministic regex — and verified live by the project owner rather than in-sandbox. This shaped the whole architecture: the regex fallback isn't an afterthought, it's load-bearing, since it's the only path that could actually be tested end-to-end during development.

## A dead-code regression in the regex fallback went unnoticed for a while

A mid-project edit to improve product-name extraction accidentally left an unconditional `return` at the top of the fallback router, making every branch below it (reopen, stop, file, show-letter, status-check) unreachable — and a related helper function fell off the end without a final `return`, so it returned `None` and crashed on unpacking. Both bugs were invisible during testing because a working Groq key meant the fallback path never actually ran. Fixed, and now covered by direct unit tests on the fallback router itself (not just end-to-end tests that happen to route through Groq) specifically so a future edit can't silently break it the same way again.

## Reopening a case stopped at the filing-decision stage needed special handling

The filing gate (Consumer Commission stage) has no reply-window — it's a one-time yes/no decision, not a send-and-wait cycle. The generic "reopen by replaying the ladder from this stage" mechanism assumes every stage has a window and would crash on this one. Fixed by giving the graph an explicit alternate entry point for this one case, rather than reworking the general resume mechanism.

## The waiting-stage NLU needed real tool-calling, not intent classification

Early on, anything a user said while a case was "waiting" (e.g. "it's been 28 days, when will I hear back?") hit the same canned reply regardless of content, because the router only recognized a fixed set of commands. Classifying free text into a wordlist of intents doesn't scale to real phrasing. Replaced it with Groq function-calling: the model gets one real tool (`advance_days`) and is told to answer only from the case's actual known facts, with any numeric claim it can't verify against those facts discarded in favor of "not sure, check back." This closed off hallucination by construction instead of by prompting it away.

Two subtler bugs came out of building this: (1) the general router's fallback-on-missing-args path could re-land on `get_next_step` by a different route than the one the waiting-stage check was looking for, silently skipping the new agent — fixed by moving that check after all routing/fallback resolution settles, not just after the first pass; (2) a calendar-date message ("it's 16 oct") computed against the *current* simulated day instead of the case's fixed open-date anchor, so a second date message in the same case could double-count prior clock advances and over-escalate. Fixed by anchoring calendar-date math to the real-world date the case opened on, which never moves, while relative durations ("8 days") still correctly add to the current simulated day.

## A CSS class collision silently doubled up on every message bubble

`.me`/`.bot` (navy/white message styling) were written as bare class selectors, and the message wrapper `<div>` happens to carry the same class name as the bubble it contains — so both elements picked up the background rule, with the outer one painting a second, larger, square-cornered box behind the actual rounded bubble. Same navy-on-navy color made it invisible for short messages, so it shipped unnoticed until a taller element (an inline edit box) made the mismatch obvious. Fixed by scoping the rule to the bubble class specifically (`.m.me` / `.m.bot`) instead of the bare class name — a reminder that a shared class name between a wrapper and its child is worth a second look.

## Deleting a case means deleting LangGraph's own state, not just an index row

`delete_case` isn't just a SQL `DELETE` on the app's own case table — each case is a LangGraph thread persisted via `SqliteSaver`, with its own checkpoint rows keyed by `thread_id`. Skipping those would leave orphaned graph state behind for every deleted case. There's no public "delete this thread" method on `SqliteSaver`, so this reaches into its known table names (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) directly, with each delete wrapped individually so a renamed table in some future langgraph-checkpoint-sqlite version can't block the rest of the cleanup.