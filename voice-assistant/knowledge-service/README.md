# Claude knowledge service

Answers the voice assistant's `ask` intent (general questions) with the
`claude` CLI on the household Claude subscription. Replaced OpenRouter/GPT-5.4
on 2026-11-20, after OpenRouter ran out of credits and every question answered
"Sorry, I couldn't reach the knowledge service."

- Runs on the Beelink **host** as a systemd user unit, `claude-knowledge.service`,
  listening on `127.0.0.1:8816` only. The orchestrator container is
  host-networked and has no CLI or login of its own.
- Models: `KNOWLEDGE_MODELS=claude-fable-5-2:low,claude-opus-5-5:low`, tried in
  order. The second is used only if the first fails before any answer text.
- One process per question, pre-spawned: a warm spare holds the (static)
  system prompt, a request takes it, a new spare starts at once. The spare is
  recycled every 20 minutes.
- Each process is isolated: no settings sources (no CLAUDE.md, memory, hooks),
  no MCP servers, WebSearch as the only tool, an empty working directory.

## Wiring

    orchestrator ask.py  --POST /ask {system, prompt}-->  server.py  -->  claude -p (stream-json)
                         <--NDJSON: meta / delta... / done | error--

Orchestrator side: `ASK_BACKEND=claude` and `CLAUDE_KNOWLEDGE_URL` in
`/home/pi/voice-pipeline/docker-compose.yml`; client in
`orchestrator/claude_knowledge.py`; prompts in `orchestrator/ask.py`
(`_system_static`, `_claude_prompt`). The system prompt must stay static: the
date, room and recent Q&A go in the question. If this service fails before
any text, `ask.py` falls back to OpenRouter.

## Operating

    systemctl --user status claude-knowledge
    journalctl --user -u claude-knowledge -f
    curl -s localhost:8816/health            # spare state, warm hits, fallbacks, errors
    ./try.py "how much juice is in a lime"   # one question with timings; nothing is spoken

Install or update the unit:

    cp claude-knowledge.service ~/.config/systemd/user/ && systemctl --user daemon-reload
    systemctl --user enable --now claude-knowledge      # restart after editing server.py

Rollback to OpenRouter: set `ASK_BACKEND=openrouter` in the compose file and
`docker compose up -d` (compose backup: `docker-compose.yml.bak-20261120-claude-ask`).

## Why these models

`llm-benchmarks/knowledge/claude_run.py`, 2026-11-20, same six questions as the
September GPT-5.4 run (review in `llm-benchmarks/knowledge/review-20261120-claude.json`).
Spoken answer ready, p50: Fable 5.2 low 1.8 s, Opus 5.5 low 1.8 s, GPT-5.4
medium 3.9 s. A web search adds 5 to 15 s on any model. Fable 5.2 and Opus 5.5
medium were the only ones to get the named-source question exactly right;
Sonnet 5.5 low spoke a search-tool note aloud; Haiku 5.5 got the follow-up wrong.

It shares the subscription's rate limits with interactive Claude Code use.
A limit hit shows up as `fallbacks` (Opus answered) or `errors` in `/health`.
