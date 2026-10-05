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