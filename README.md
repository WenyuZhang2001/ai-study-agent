# AI Study Agent

A small research agent built with Codex for UCLA AI in Research. The week 2 loop keeps `bash`; week 3 adds a plugin loader, official MCP clients, and Telegram questions with reply-based approvals. No agent framework is used.

Public repository: https://github.com/WenyuZhang2001/ai-study-agent

## Configure

Install uv. The project requires Python 3.11+; uv resolves the interpreter and dependencies. The `python3` found by an approved shell command can be a different system version.

```sh
cp .env.example .env
# Enter credentials locally; never paste them into AI chat or commit .env.
uv sync
```

Configure `BASE_URL`, `API_KEY` and `MODEL` for an OpenAI-compatible Chat Completions API. For Telegram, create a bot with BotFather, send it a message, then enter `TELEGRAM_BOT_TOKEN` and your private `TELEGRAM_CHAT_ID` in `.env`. Environment variables override file values. `.env` is ignored by Git.

## Run

Web chat:

```sh
uv run server.py
```

The web server binds to http://127.0.0.1:8765. Options: `--no-browser`, `--port 8766`, or `--no-plugins` for the original bash-only mode.

Phone chat:

```sh
uv run server.py --telegram
```

Wait for the online message, then send a question to the bot. Only run one receiver at a time: both default web mode and Telegram mode load the Telegram plugin. Keep the local process running; this is not a hosted service. Stop with Ctrl+C. `/new` starts a fresh in-memory conversation. History is lost when the process stops.

Every bash command and non-allowlisted model tool call requires approval. For Telegram, reply to the **numbered approval message** with exact English `yes` or `no`. Telegram opens the corresponding reply box automatically. A normal assistant sentence asking for approval is not an approval record. Approvals expire after three minutes; old, unlinked or late confirmations cannot authorize a new command. A refusal disables all remaining tool calls for that user turn, including attempts to substitute another tool.

`settings.json` initially allows only `telegram__send_message` without prompting. Bash cannot be allowlisted. Temporary network failures trigger capped retry backoff. Retrying delivery can duplicate a message after an ambiguous timeout, but never reruns a model turn or shell command.

## Plugins and context

The loader discovers `plugins/*/.claude-plugin/plugin.json`, reads each `.mcp.json`, expands `${CLAUDE_PLUGIN_ROOT}` and environment references, and starts stdio servers. It lists their tools and forwards calls as `<server>__<tool>`. Clients stay alive for the run and close together on exit.

- `wenyuzhang2001-telegram`: `send_message` to the configured private chat and `wait_for_messages`, with chat filtering and startup-backlog skipping.
- `wenyuzhang2001-equity-research`: completed daily bars, trend/risk summaries, a fixed SMA control, a watchlist, and five-session probability research with cost-aware simulations. See each plugin README for assumptions and independent-host setup.

Tool schemas enter the API request's `tools` field beside bash. The system prompt lists only skill name, description and path; the model proposes an approved bash read to load a skill body when needed. Credentials enter server environment variables, not tool schemas or skill metadata. Known secret values are redacted from tool results. In Telegram mode the harness owns incoming polling and hides `wait_for_messages` from the model; the harness also delivers the final reply.

Startup prints loaded plugins, tools and skill metadata. The first model call prints the provider-reported input token count. Each API call reports input/output/cached token usage when available. A bounded repair handles prose-only approval requests by asking for a real tool proposal; it never executes commands parsed from prose or grants permission automatically. Final-answer repair cannot execute tools.

## Equity research scope

The configured list is AAPL, MSFT, NVDA, AMZN, GOOGL, META, AMD, AVGO, TSM and TSLA. Semiconductor comparisons use SMH; other stocks use QQQ. This selected present-day list is not a point-in-time equity universe.

The primary research tool estimates positive gross adjusted return from the next open to the open five trading sessions later. Fixed logistic regression is evaluated chronologically with train-only scaling and matured-label purging. Outputs include Brier/log-loss versus a historical-up-frequency baseline, reliability diagnostics, a non-overlapping evaluation grid, and a single-stock long/cash simulation with assumed costs. Controls include buy-and-hold, SMA50/200, unconditional entries, and explicitly labeled breakout/pullback adaptations.

TSLA and NVDA historical checks ending 2025-10-17 did **not** demonstrate a probability edge. Latest probabilities remain uncalibrated research estimates. No brokerage access, live orders, portfolio allocation, earnings/news inputs, integer-share execution model or scheduled daily run is implemented. Cash yield, taxes and minimum commissions are omitted; vendor-adjusted prices and fractional units approximate total returns. These limitations matter for small accounts.

Yahoo/yfinance is an unofficial research feed. Current New York day is conservatively excluded, even after close. Data age relative to today and coverage relative to the requested historical date are separate fields; a deliberately historical request is not automatically a stale download. Missing or invalid data are errors; no synthetic market-data fallback is used.

## Code map

- `agent.py`: conversation history, tool dispatch, approval enforcement and bounded loop.
- `api.py`: local configuration, one model request and usage reporting.
- `plugins.py`: plugin discovery, skill metadata and managed MCP lifecycle thread.
- `telegram_mode.py`: polling, questions, linked approvals, expiry and reconnect.
- `server.py` / `index.html`: authenticated localhost web chat and approval UI.
- `system_prompt.md` / `settings.json`: model instructions and explicit allow list.
- `one_call.py`: original one-request week 2 learning example.

Commands have a 60-second timeout and bounded output. The loop permits at most 20 tool rounds per question. Approved shell commands retain the local process's permissions; this is not an OS sandbox. Never approve secret-file reads or send secrets/whole files through Telegram. Bot chats are not end-to-end encrypted; revoke a leaked bot credential with BotFather `/revoke`.

## Validation

```sh
uv run --with httpx --with 'yfinance>=0.2.65,<2' --with 'pandas>=2.2,<4' --with 'numpy>=2,<3' --with 'scikit-learn>=1.7,<2' python -m unittest discover -s tests -p 'test_*.py'
```

44 automated checks cover tool dispatch, denials, linked approvals, expiry, disconnects, real fixture MCP startup/shutdown, environment isolation, metadata-only skill disclosure, web sessions/origins/concurrency/reset, and research timing/cost calculations. Live phone acceptance passed: approved file listing, explicit refusal, full TSLA research answer, sequential Python-version/disk-space task, wrong-chat filtering, and skipping a verified stopped-bot marker on restart.

Both plugins were also tested in the bundled Codex CLI 0.162.0-alpha.17.2 using ephemeral MCP configuration and normal built-in approval review. Codex sent the fixed Telegram test message and ran the historical TSLA research tool successfully. No global Codex configuration was changed. Claude Code's validator was unavailable; no Claude validation is claimed. Tests were run on macOS; other operating systems have not been tested.

Local research receipts and generated outputs are ignored under `research-output/` and are not part of publication.

## Status

Week 3 implementation and local/phone/Codex acceptance are complete. Public push and the course pull request remain pending the student's publication checkpoint. The normal Telegram receiver was restored during this session; verify the local process is still running before relying on phone access.
