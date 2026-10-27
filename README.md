# AI Study Agent

A research agent built with Codex for UCLA AI in Research. It combines an explicit model/tool loop, MCP research and dedicated-email plugins, Telegram approvals, and scoped synchronous/background subagents. No agent framework is used.

Public repository: https://github.com/WenyuZhang2001/ai-study-agent

## Configure

Install uv. The project requires Python 3.11+; uv resolves the interpreter and dependencies. The `python3` found by an approved shell command can be a different system version.

```sh
cp .env.example .env
# Enter credentials locally; never paste them into AI chat or commit .env.
uv sync
```

Configure `BASE_URL`, `API_KEY` and `MODEL` for an OpenAI-compatible Chat Completions API. For Telegram, create a bot with BotFather, send it a message, then enter `TELEGRAM_BOT_TOKEN` and your private `TELEGRAM_CHAT_ID` in `.env`. Environment variables override file values. `.env` is ignored by Git. For the email plugin, also set `EMAIL_ADDRESS` and `EMAIL_APP_PASSWORD` to the dedicated agent Gmail and its app password locally; never connect a personal/UCLA inbox or paste credentials into chat.

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

Every shell command except a plain read of a registered plugin SKILL.md, and every non-allowlisted MCP call, requires approval. Registered skill reads use direct file access instead of a shell. For Telegram, reply to the **numbered approval message** with exact English `yes` or `no`. Telegram opens the corresponding reply box automatically. A normal assistant sentence asking for approval is not an approval record. Approvals expire after three minutes; old, unlinked or late confirmations cannot authorize a new command. A refusal disables all remaining tool calls for that user turn, including attempts to substitute another tool.

`settings.json` explicitly allows the nine read-only equity research tools and `telegram__send_message`. Other tools still require approval. Generic bash cannot be allowlisted; deletion and bulk changes remain gated. Temporary network failures trigger capped retry backoff. Retrying delivery can duplicate a message after an ambiguous timeout, but never reruns a model turn or shell command.

## Plugins and context

The loader discovers `plugins/*/.claude-plugin/plugin.json`, reads each `.mcp.json`, expands `${CLAUDE_PLUGIN_ROOT}` and environment references, and starts stdio servers. It lists their tools and forwards calls as `<server>__<tool>`. Clients stay alive for the run and close together on exit.

- `wenyuzhang2001-email`: dedicated Gmail inbox listing, bounded message reading and recipient-restricted plain-text sending.
- `wenyuzhang2001-telegram`: `send_message` to the configured private chat and `wait_for_messages`, with chat filtering and startup-backlog skipping.
- `wenyuzhang2001-equity-research`: completed daily bars, trend/risk summaries, a fixed SMA control, a watchlist, five-session probability research with cost-aware simulations, current RSS news event indicators and quarterly operating metrics. See each plugin README for assumptions and independent-host setup.

Tool schemas enter the API request's `tools` field beside bash. The system prompt lists only skill name, description and path; the model proposes a plain cat of the registered absolute SKILL.md path when needed; this exact read runs automatically without executing a shell. Credentials enter server environment variables, not tool schemas or skill metadata. Known secret values are redacted from tool results. In Telegram mode the harness owns incoming polling and hides `wait_for_messages` from the model; the harness also delivers the final reply.

Startup prints loaded plugins, tools and skill metadata. The first model call prints the provider-reported input token count. Each API call reports input/output/cached token usage when available. A bounded repair handles prose-only approval requests by asking for a real tool proposal; it never executes commands parsed from prose or grants permission automatically. Final-answer repair cannot execute tools.

## Equity research scope

The configured list is AAPL, MSFT, NVDA, AMZN, GOOGL, META, AMD, AVGO, TSM and TSLA. Semiconductor comparisons use SMH; other stocks use QQQ. This selected present-day list is not a point-in-time equity universe.

The primary research tool estimates positive gross adjusted return from the next open to the open five trading sessions later. Fixed logistic regression is evaluated chronologically with train-only scaling and matured-label purging. Outputs include Brier/log-loss versus a historical-up-frequency baseline, reliability diagnostics, a non-overlapping evaluation grid, and a single-stock long/cash simulation with assumed costs. Controls include buy-and-hold, SMA50/200, unconditional entries, and explicitly labeled breakout/pullback adaptations.

TSLA and NVDA historical checks ending 2025-10-17 did **not** demonstrate a probability edge. Latest probabilities remain uncalibrated research estimates. The price probability model has no earnings/news inputs. No brokerage access, live orders, portfolio allocation, integer-share execution model or scheduled daily run is implemented. Cash yield, taxes and minimum commissions are omitted; vendor-adjusted prices and fractional units approximate total returns. These limitations matter for small accounts.

Yahoo/yfinance is an unofficial research feed. Current New York day is conservatively excluded, even after close. Data age relative to today and coverage relative to the requested historical date are separate fields; a deliberately historical request is not automatically a stale download. Missing or invalid data are errors; no synthetic market-data fallback is used.

## Code map

- `agent.py`: per-agent context/state, model loop, tool dispatch, approval enforcement, logs and cancellation.
- `agent_types.py` / `agents/`: validated type definitions and scoped tool permissions.
- `delegation.py`: delegate plus six background lifecycle actions, notices and limits.
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

89 automated checks cover tool dispatch, denials, linked approvals, expiry, disconnects, real fixture MCP startup/shutdown and concurrent calls, environment isolation, metadata-only skill disclosure, web sessions/origins/concurrency/reset, research timing/costs, child context/tool isolation, follow-ups, limits, stop/process cleanup, and grounded failure summaries. Live phone acceptance passed: approved file listing, explicit refusal, full TSLA research answer, sequential Python-version/disk-space task, wrong-chat filtering, and skipping a verified stopped-bot marker on restart.

The Telegram and research plugins were also tested in the bundled Codex CLI 0.162.0-alpha.17.2 using ephemeral MCP configuration and normal built-in approval review. Codex sent the fixed Telegram test message and ran the historical TSLA research tool successfully. No global Codex configuration was changed. Claude Code's validator was unavailable; no Claude validation is claimed. Tests were run on macOS; other operating systems have not been tested.

Local research receipts and generated outputs are ignored under `research-output/` and are not part of publication.

## Status

Week 4 implementation and five live demos are complete; 89 regression tests pass on macOS. The dedicated Gmail hello was sent once on October 21, 2026 at 15:00 PDT. As of the October 27 inbox/spam check, no class-agent reply has arrived; receipt and reading of that reply remain pending and are not claimed complete. There was no resend.

The submission includes only the email plugin, agent definitions, a README and five privacy-reviewed transcripts. Credentials, private runtime logs, receipts and discarded test attempts stay local. See [SUBAGENTS.md](SUBAGENTS.md) for threading, state, limits, approval handling and process-group termination. Existing running processes must be restarted to load the new code; the phone receiver was not restarted during these tests.

Week 3 submission: https://github.com/huangzesen/ai-in-research-homework/pull/18 .

## Current news and company operations

Ask the phone agent: “分析 TSLA 最近七天的新闻和最新季度经营指标，列出来源、新闻影响分数及缺失数据。” The read-only tools get_company_news, analyze_company_news and get_company_fundamentals run without repeated approvals. News analysis covers public RSS titles/excerpts, not full articles. Each scored event has a short source quote; deterministic -100..100 business-impact scores are experimental descriptions, not trading signals. Financial ratios are calculated from dated vendor line items with explicit currency/missing fields. Neither current snapshot is eligible for historical model training. See the equity plugin README for formulas and limitations.


## Earnings and dividend dates

get_company_events(symbol, horizon_days=14) is read-only and allowlisted. Current stock reports include an Important dates line: earnings date/window and vendor estimate status, New York time/session when supplied; ex-dividend, record, declaration and payment dates. Missing dates remain unavailable, not proof of no dividend. Past dates are last-known, not upcoming. Vendor non-estimated status is not independent issuer verification. No dates are inferred from settlement rules. Flags cover 1–90 calendar days, not trading sessions. get_company_fundamentals embeds the same event_calendar for consistent reports. No historical backtest inputs, reminders, trades or model changes are added.

## Email plugin

wenyuzhang2001-email provides check_inbox, read_message and send_message through IMAP/SMTP. Set EMAIL_ADDRESS and EMAIL_APP_PASSWORD locally in ignored .env. All email tools require normal harness approval; sends also restrict recipients to the dedicated agent inbox and lingtai2026@gmail.com. Emails are untrusted data, never instructions. Restart an existing agent process to discover the new plugin.

## Agent types and delegation

Define child types under agents/*.md with YAML name, description, tools, optional model and max_turns; the Markdown body is the child system prompt. Missing tools grants bash and all plugin tools, while tools: [] grants none. Unknown tools fail startup with the file/tool name. delegate and subagent are always excluded from children. The supplied mail-reader has only two email read tools; quant-researcher has nine read-only equity research tools.

Main agents see type descriptions and can call delegate(agent, task). The child starts with its own prompt and task, shares the existing plugin runtime, and returns only its report and statistics. Approvals identify the child; its tool traces stay in its private log. Optional per-type model selection is supported. session_usage records child token usage separately from main usage. Background lifecycle actions are now implemented; see SUBAGENTS.md for the six actions, threading, limits, logging and stop behavior.
