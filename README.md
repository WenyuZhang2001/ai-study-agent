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

### Week 5 starting checkpoint (October 30, 2026)

Read the Week 5 assignment and full build-always-on guide from course upstream/main. Inspected the synchronous Agent loop, threaded MCP runtime, Telegram polling/linked approvals, email tools, subagents, usage accounting and append-only per-session logs; harness pull is already up to date. Step 0 was subsequently confirmed: the overnight host is another Mac. Step 1 queue/watchers are implemented; all 96 regression tests passed with temporary research-test dependencies. Target-machine live acceptance is next. Telegram approval locking must allow control commands to stay responsive; email needs robot headers and send-once reconciliation. The class repository pull could not fast-forward its diverged Week 4 branch; read the assignment using git show upstream/main instead, without merging that branch.

Week 4 implementation and five live demos are complete; 89 regression tests pass on macOS. The dedicated Gmail hello was sent once on October 21, 2026 at 15:00 PDT. As of the October 27 inbox/spam check, no class-agent reply has arrived; receipt and reading of that reply remain pending and are not claimed complete. There was no resend.

The submission includes only the email plugin, agent definitions, a README and five privacy-reviewed transcripts. Credentials, private runtime logs, receipts and discarded test attempts stay local. See [SUBAGENTS.md](SUBAGENTS.md) for threading, state, limits, approval handling and process-group termination. Existing running processes must be restarted to load the new code; the phone receiver was not restarted during these tests.

Week 4 submission: https://github.com/huangzesen/ai-in-research-homework/pull/25 (open; class-agent reply remains pending).

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

## Week 5: always-on work in progress

The overnight host is this Mac. Step 0 was confirmed on October 30. Step 1 adds `always_on.py`: two code watchers feed one `queue.Queue`; only the consumer calls `Agent.ask`. Telegram remains filtered to the configured private owner by its MCP server. One watcher owns polling and routes linked approvals separately from questions, so an approval wait does not monopolize polling. Existing web and `--telegram` modes remain available.

After stopping every other receiver for the same bot, run `uv run python server.py --always-on` on the intended host. This is an intermediate implementation, not yet ready for unattended operation: schedule watcher/tool, control commands, daily limits, compaction, recovery, send-once email and launchd setup remain pending. Slash commands are currently ignored. No live receiver was started during implementation.

Email is checked through `email__check_inbox(unread_only=False, limit=20)` every 90 seconds without a model call. The first successful poll establishes a baseline of existing messages; only later new UIDs generate header-only, explicitly untrusted events. IDs persist in ignored `state/seen.json`. The present plugin only returns the newest 20 headers: bursts beyond that can be missed, and UIDVALIDITY is not yet tracked. Queued events are currently in memory, so a crash can lose a queued event even though its UID was saved; durable queue/recovery is still required in Step 5.

Next checkpoint: run idle, Telegram and new-mail acceptance checks on the target Mac, verify no token increase while idle, and explain where email becomes a user message. Tests using simulated services are not live demos.

### Target Mac migration check (October 30, 2026)

Reviewed TRANSFER.md and verified the ZIP source matches staging before applying Step 1. Existing settings.json, plugin configuration and ignore rules were preserved. This checkout initially has no .env, no uv executable on PATH and no configured API/Telegram/email environment variables. Live idle, Telegram and new-email acceptance remain pending; no receiver has been started and no overnight readiness is claimed. Configure credentials locally and stop the other bot receiver before live verification.

Local migration validation: installed uv and runtime/test dependencies into the ignored project .venv without changing pyproject.toml or uv.lock; all 96 regression tests passed on this Mac (including seven watcher tests). The first sandboxed run blocked two localhost test servers; the permitted rerun passed. Created an ignored mode-600 .env template because no local .env existed. Fill it locally, then use `PATH="$PWD/.venv/bin:$PATH" .venv/bin/uv run python server.py --always-on` after confirming the old receiver is stopped. Live acceptance remains pending.

### Step 1 target Mac live acceptance — October 30, 2026 (PDT)

Live acceptance passed: all seven required variables were present (values suppressed); the owner confirmed the other Mac receiver was stopped. One real Telegram question and one newly delivered test email each produced exactly one wake and one model call, and the owner confirmed both Telegram replies. The email event contained only From/Subject/UID with an explicit untrusted-data label; neither turn invoked tools or sent email. Between the Telegram completion and email wake, 174.7 seconds elapsed with no additional model call or token usage. A subsequent successful email poll produced no duplicate wake. The successful live session recorded zero watcher/outer errors. All 96 regression tests also passed after the TLS adjustment.

The first live attempt could not establish the email baseline: this Mac's Python default CA store failed TLS verification. Gmail TLS verified successfully using the installed certifi bundle. The receiver was restarted with `SSL_CERT_FILE` set to that bundle, and plugins.py now forwards SSL_CERT_FILE/SSL_CERT_DIR to subprocesses. Certificate verification stays enabled. For this Mac, launch using `PATH="$PWD/.venv/bin:$PATH" SSL_CERT_FILE="$(.venv/bin/python -m certifi)" .venv/bin/uv run python server.py --always-on` after confirming no other receiver is running. The test receiver was stopped after acceptance. Overnight readiness still requires the later control, budget, persistence/recovery and service checkpoints.

### Phone controls implementation — October 30, 2026

Course ordering correction: schedule is Step 2 and control is Step 3. We are implementing the agreed phone-command slice first; this does not complete the full control checkpoint (budgets and the robot rule are still pending).

The owner-only Telegram watcher handles `/status`, `/pause`, `/resume` and `/stop` directly without queueing a model request, including when the command is a reply to an approval prompt. `/pause` holds incoming events in FIFO order while the current task continues; `/resume` allows queued events to run. `/stop` cancels the current task and every subagent, kills running bash process groups, interrupts pending approvals, and blocks actions from late model results. A network request already underway cannot be recalled. The single consumer waits for it to settle before accepting more work. Pending events are retained, and the previous pause setting is unchanged. Normal unpaused operation returns to waiting/processing the next event.

After cancellation, a replacement agent carries the conversation forward with explicit results for interrupted tool calls and a harness note that the stopped task must not be resumed without an owner request. Private old logs remain intact. `/status` reports asleep/running/waiting/paused, event preview, queue depth, today's wakes/tokens/API-reported cost since this process started, and active subagents. Cost is labelled unknown when the API omits it; next scheduled job is explicitly not yet implemented. Daily persistent accounting, limits, scheduling and crash resume remain unfinished.

Validation: 102 regression tests passed, including a real main-task sleep process group, stopped background children, pending approval cancellation, late model result suppression, FIFO pause/resume and usage after cancellation. Real Telegram pause/resume and kill-switch acceptance is in progress; no overnight run is claimed.

### Phone-control live results — October 30, 2026 (PDT)

Owner confirmed pause/resume and stop replies on Telegram. Live logs show /pause at 22:36:51, /status with queue depth 1 at 22:37:53, then /resume at 22:38:05; the queued message woke the model only after resume. A later Telegram task requested bash sleep 600; /status arrived at 22:48:12 and /stop at 22:48:17. The turn changed to stopped, a new context retained one interrupted-tool result plus the explicit stop note, and no model call retried the stopped task. A subsequent ps check found no remaining sleep 600, while the receiver was still alive.

Evidence limitation: the 240-second process observer timed out before the owner performed the stop test, so it did not capture the sleep PID while running. This verifies the live cancellation/recovery path but is not claimed as the complete formal kill-switch demo. Repeat the formal demo with durable process-start/exit evidence at the controls checkpoint. Pause/resume is accepted; scheduling, budgets, robot-mail protection and the full control checkpoint remain pending. The test receiver is stopped after this session; no overnight service is installed.

### Step 2 schedule implementation — October 30, 2026

`schedule.toml` now contains the owner-selected weekday stock research morning report at 08:00 America/Los_Angeles (`0 8 * * 1-5`). It uses the existing completed-trading-day watchlist tool for tech, semiconductors and Tesla, with data dates/missingness explicit; it does not trade or send mail. The IANA timezone follows daylight saving, including the November 1 transition. The next normal report is November 2 at 08:00 PST.

A third code watcher checks schedules every 60 seconds and enqueues labelled schedule events through the same consumer; it makes no model call itself. The main agent has an internal `schedule` tool: `list`, `add(name, task, cron OR every)` and `remove(name)`. Listing is free; both add and remove require the existing owner approval path, and children cannot use this tool. Mutations atomically rewrite schedule.toml and are reloaded on the next tick. Files edited directly must satisfy the same validation.

At most ten uniquely named jobs are allowed. Cron uses five numeric fields and must select one minute per hour; expressions such as `0 8 * * 1-5` are accepted and `*/30 * * * *` rejected. `every` accepts whole hours or days (for example 2h or 1d). Interval jobs begin from the first successful poll, rounded up to a minute; they are not immediately run on creation. Each job's timing/last occurrence is saved atomically in ignored state/schedule.json before enqueueing to prevent duplicate ticks/restarts, including remove/re-add within an hour. Paused occurrences are consumed without queueing. Missed occurrences while off are skipped rather than caught up; an unconsumed current-minute occurrence can run after restart in that minute. Editing a job's task does not reset its timing.

`/status` shows the next job in local time; invalid TOML is surfaced as a schedule configuration error and the watcher runs no jobs from it. Remaining limitation: the queue is still in memory. A crash between saving occurrence state and finishing queued work may lose that occurrence; durable recovery is reserved for Step 5. This is not an exactly-once job guarantee or overnight readiness.

Validation: 115 regression tests passed, including 13 schedule tests for cron, DST, approval, limits, pause, reload, concurrent ticks, disk-write failure and restart deduplication. A temporary near-term notification is being used for real Telegram acceptance and will be removed afterward. Full daily limits, robot protection, compaction, crash recovery and launchd setup remain pending.

### Step 2 live scheduled wake — October 30, 2026 (PDT)

A temporary cron notification was scheduled for 22:57 and fired at the watcher's 22:57:30 poll: exactly one schedule wake, one model call, and a final answer containing the expected test phrase, with no watcher/outer errors. Reopening the saved schedule state and checking the same minute emitted zero additional events. Removed the temporary test job; only weekday-stock-morning remains. The temporary receiver was stopped after validation. Phone receipt confirmation is the remaining Step 2 checkpoint; no overnight or full control checkpoint completion is claimed. Next work after that confirmation is limits.toml, per-call budgets, the ten-minute approval timeout and the robot-mail rule.

Step 2 checkpoint accepted: the owner confirmed receipt of the real scheduled Telegram notification. The temporary job is removed and receiver stopped; weekday-stock-morning remains configured for 08:00 America/Los_Angeles on weekdays. Next: complete the control safeguards (per-call/daily limits, approval timeout and robot-mail rule) before unattended operation.

### Control safeguards implementation — October 30, 2026

Owner approved limits.toml defaults: 8 model calls and 5 minutes per wake, 100,000 tokens per Los Angeles local day, and 2 send attempts per recipient per day. These counts are shared with delegated/background children; a resumed idle child joins the current wake's allowance. Every model call reserves a conservative serialized-input byte estimate plus output allowance before reaching the API. Reported usage settles the reservation; missing/failed usage is charged at the reserved estimate. In-flight reservations survive restart and are not silently released. The private state/usage.json ledger retains daily calls, wakes, tokens and API-reported dollars across restart. Old pre-budget experiment logs are not imported (they also contain test fixtures). A request is charged to the day it started, even if it finishes after midnight. Cost is unknown when the API omits it; tokens are the enforced daily budget, not dollars. The reservation is conservative accounting, not a provider-side billing hard cap.

The per-wake watchdog cancels overdue work, interrupts approvals and kills command process groups. The shared call limit is checked before every main/child model request. On a limit, code logs the reason, stops children, repairs interrupted tool history, sends an explanatory Telegram notice and does not retry the stopped task. New requests can proceed after the limit/configuration permits; commands remain usable. Edits to limits.toml are loaded at the next wake. /status now reports persistent today's usage, pending reserves and daily cap. Corrupt state/configuration fails closed.

Always-on approval timeout is 600 seconds and the tool result explicitly says `declined: nobody answered within 10 minutes`; no alternative tool is allowed after denial. With the default 5-minute wake budget, the time budget can cancel approval first. Existing non-always-on UI timeout behavior is unchanged.

The email plugin fetches Auto-Submitted and List-Id, marks automatic/list notifications, and returns the flag with headers. The main/child code refuses every email send originating from an automatic-mail wake, regardless of tool allowlists. Explicit replies pass reply_to_id; the send tool refetches the original headers, rejects automatic/list messages and mismatched recipients, and refuses truncated robot headers. Re: subjects without an original UID are refused. All sends, including new mail and uncertain SMTP failures, consume the per-recipient attempt cap atomically before SMTP under a cross-process file lock. The private state/mail-replies.json uses hashed normalized recipient keys and local-day counts. Outgoing agent mail carries Auto-Submitted: auto-generated. This cap does not provide outbox/send-once reconciliation, which is still Step 5 work.

New command process-start/end logs record private PIDs and stopped/timeout status so future formal kill-switch evidence does not depend on a short external observation window. Unit tests use mocked SMTP only: no real test email is sent during safeguards development. The real tiny-budget Telegram check is in progress; its temporary 1-token cap will be restored to the approved default after the check. Overnight readiness, compaction, crash recovery and launchd remain pending.

Control-limit live acceptance: the owner confirmed the real Telegram tiny-budget notice. Session evidence and the persistent ledger both show zero model requests/tokens for the 1-token test. The receiver was stopped, the approved 100,000-token daily limit restored, and a single new receiver started for the formal kill-switch demo. See transcripts/limit.md. All 138 regression tests passed; the ten-minute timeout and email safeguards were verified with controlled clocks/mock SMTP, with no real test sends. Formal kill-switch evidence is pending the owner phone test.

### Control checkpoint accepted — October 30, 2026 (PDT)

Owner confirmed the formal Telegram /stop reply. Durable evidence logged sleep 600 PID 45913 starting at 23:37:02 PDT and exiting at 23:37:43 PDT with exit_code=-9, stopped=true, timed_out=false. A subsequent ps check found no remaining process in that command group. Recovery context includes an interrupted-tool result plus the explicit stop note; it has zero post-stop model calls. The formal kill-switch and tiny-budget demos are now recorded in transcripts/kill-switch.md and transcripts/limit.md. Normal limits are restored (100000 tokens/day), all 138 regression tests passed, and the temporary receiver exited cleanly. No real test mail was sent; ten-minute expiry and robot/reply-cap protections were verified with controlled clocks and mocked SMTP. This completes the control checkpoint, not overnight readiness.

Next checkpoint: compaction and memory (choose a real-use threshold and use 20000 input tokens for the demo), then durable log resume/send-once, launchd/crash verification, and the eight-hour night. The earlier missing-PID demo is superseded by the complete recorded demonstration above.

### Step 4 compaction implementation — October 31, 2026

Owner selected compaction.toml input_tokens=20000 (API-reported input count) and keep_recent_turns=2; approved daily token cap remains 100000. An always-on wake refreshes system_prompt.md, AGENTS.md and private memory/MEMORY.md before the model runs. Standing rules and factual memory live in the system prompt, outside compressed history. Memory is limited to 200 lines/32000 characters; the internal memory tool reads freely but writes always require owner approval, including during compaction. Generated personal memory is gitignored.

After a completed wake, the compactor first asks the agent to save essential facts to memory with the usual approval, using only the bounded memory tool, then requires DONE. One additional tool-free model call summarizes the history using Goal/Done/Open/Decisions/Exact/People. The replacement contains the system prompt, a timestamped summary and the most recent two complete user turns verbatim. Boundaries begin at user messages; tool-call/result pairs are validated and never split. Compaction waits if background children are active or insufficient older turns exist. It never runs from the idle watcher or in the middle of a tool call.

Both memory/checkpoint and summary model calls use the same shared wake/day budget and watchdog. Reservations can reuse the last API-reported count for an unchanged message prefix, conservatively reserving serialized bytes for new messages/tool definitions plus output/framing overhead. Changed prefixes fall back to full serialized-byte reservation. This avoids treating all existing history as fresh bytes every maintenance call without raising the budget.

The summary and before count are saved privately under logs/compactions/. The append-only session log receives a compacted=true checkpoint with the complete replacement context before the in-memory swap. /stop cannot intervene between these two commit steps. Empty, malformed/truncated or non-reducing summaries, declined memory writes and I/O/API errors retain the original history and log an abort marker; already-approved memory edits remain on disk. A limit/stop still reaches the outer controller. These markers prepare for Step 5 resume, which is not implemented yet.

After-token numbers are not guessed: the next actual request appends its API input count to the summary artifact and logs compaction_measurement. That count includes the follow-up question and freshly reloaded files. No extra call is made solely to measure. The first trigger count and summary-request count are both recorded separately.

Validation: all 151 regression tests passed, including 13 new compaction/memory tests for exact retained tails, intact tool chains, approval denial, file reload, symlinks/line caps, summary errors, disk failure, stop-before-commit and shared budget enforcement. Real compaction acceptance is in progress with scripts/compaction_demo.py: a clearly labelled synthetic, secret-free old history, real API/approvals/budget, Telegram-only watcher, and no external model tools except memory. Demo sessions are under logs/demos/; they must not be mistaken for real prior owner conversations or production resume sessions. The standard production entry point remains server.py --always-on. No overnight/service readiness is claimed.

### October 31 — live Step 4 failure and checkpoint fix

The real labelled demo reached 21583 input tokens, so the configured trigger worked. The memory checkpoint then used read before its only permitted tool round; its forced-final response contained a text tool-call representation. The harness did not execute that text or replace history: all nine pre-maintenance messages remained and memory was unchanged. Fixed the checkpoint to expose a write-only schema, require a structured first tool call, and enforce exactly one memory write in code. Read/text-only responses now abort after one request, without extra API retries, and logs identify the failure stage. All 153 regression tests passed.

The failed live attempt used 65412 tokens, leaving 34588 of today's approved 100000-token cap. Receiver is stopped; cap is unchanged. A complete live retry at the agreed 20000 threshold, plus the follow-up measurement/recall question, cannot fit in that remaining allowance. Await the owner's choice to wait for local midnight or temporarily increase the day's allowance. Compaction live acceptance remains pending; do not call Step 4 complete.

October 31 live retest: owner temporarily authorized 200000 tokens/day. The demo committed one summary after an approved structured memory write, at a trigger count of 21583; it kept two recent turns and the exact demo ID. Inspection then caught a semantic memory issue: the model wrote a heading only, erasing existing preference notes. Restored those exact notes from the pre-write system snapshot and added preserve-existing maintenance writes plus a committed-write generation check. All 155 tests passed. The demo ID is in the summary, not the restored memory. Owner follow-up is pending to verify recall and the next-request API count; the temporary budget is not yet restored and Step 4 is not yet accepted. After that test stop the receiver and restore 100000; today's already-used allowance may then exceed the restored cap until midnight.

October 31 compaction evidence: owner confirmed W5-ORBIT-731 on the follow-up. Real API input measurements were 21583 before and 1344 on the next actual request; the summary request itself used 21678. The retained 2 user turns were verified verbatim; the ID existed in the summary but not the tail or restored memory. See transcripts/compaction.md for the successful summary/recall and the distinct memory placeholder failure/repair. All 155 tests passed. Receiver stopped and the daily cap restored to 100000; 154375 tokens were already used today, so new model calls are blocked until local midnight. Student summary-loss review remains the Step 4 checkpoint item. Do not claim crash recovery/service/overnight readiness.

Step 4 checkpoint accepted (October 31): the owner reviewed the summary and identified that wallpaper color was omitted (synthetic original: olive). Exact identifier recall and 21583 -> 1344 API input measurements passed; the memory placeholder overwrite and preservation fix remain documented rather than hidden. Next: Step 5 append-only log resume, interrupted-tool recovery, durable event handling and send-once email reconciliation. Receiver remains stopped, normal cap remains 100000; today's accumulated tokens exceed that cap until local midnight.

October 31 owner configuration update: normal daily token allowance increased from 100000 to 1000000. All other limits are unchanged (8 shared calls and 5 minutes per wake; 2 send attempts per recipient/day). This supersedes earlier references to a 100000 normal cap. The receiver remains stopped; future starts load the new limit.

### Step 5 crash recovery implementation — October 31, 2026

Production always-on mode now uses the canonical append-only logs/main.jsonl and a FULL-synchronous SQLite WAL FIFO in state/events.sqlite3. Old per-session/test/demo logs are not guessed as production history. Unstarted events and pause state survive restart. Telegram source-message IDs and account-scoped email UID keys deduplicate persisted events. A delivered marker can finish a claimed event on recovery; other claimed events become interrupted and are not automatically replayed. The restart notice reports context repair, interrupted/queued counts, the prior event and verified orphan command cleanup. Commands/approvals are not replayed. A fresh owner request may continue using restored history.

The loader applies the latest compaction/abort/context checkpoint and later messages, refreshes the system prompt from files at the next wake, and inserts explicit not-re-run results for interrupted calls. It preserves an incomplete final log fragment and appends an offset marker rather than truncating original bytes. Malformed interior records without a documented recovery marker fail closed. Log lines are flushed/fsynced. Background workers are not restored; new child log namespaces avoid ID-file reuse across restarts. Orphan POSIX command groups are killed only when the recorded PID/start fingerprint still matches and the PID is its group leader, protecting unrelated reused PIDs.

The server acquires a workspace receiver lease before loading plugins. In --always-on mode, Telegram uses a private persistent cursor and only advances it through a harness-only acknowledgement after durable enqueue; the model never sees that tool. Ordinary non-durable modes keep their prior startup behavior. Durable inbox polling now fetches oldest UIDs above the watermark in pages of 20 (up to ten pages per polling batch), preventing >20-mail bursts from silently losing notifications. Account/UIDVALIDITY and seen IDs share one atomic state record; a new epoch establishes a fresh baseline. state/seen.json remains a compatibility export. Schedule jobs enter the durable queue before their standalone cursor advances; queue identity/hour guards prevent replay in the handoff gap.

The email tool now journals state/outbox.jsonl intent before SMTP and done afterward, using a deterministic Message-ID/key derived from sender/recipient/subject/body. Done keys return already_sent without SMTP or another quota attempt. Intent-only keys search Sent by Message-ID; if the server rewrote it, fallback checks recipient, exact subject/body and intent time. No confirmation means no resend: a missing/index-delayed Sent copy is not proof of no delivery. File locks serialize concurrent send decisions. This is conservative at-most-once automatic SMTP attempts, not an unconditional exactly-once delivery guarantee. Uncertain mail may require owner investigation. Existing recipient, robot-header, secret, approval and per-address cap safeguards remain.

Validation: 181 full regression tests passed, including actual SIGKILL recovery of an orphan sleep group, interrupted journal tails, FIFO/pause persistence, compaction checkpoint restore, durable-before-ack ordering, Telegram cursor restart, mailbox bursts/epoch changes, and crash-after-mocked-SMTP deduplication; final outbox targeted tests also passed after UTF-8 fallback-search support. No real mail has been sent by these tests.

Live crash demo prepared in state/outbox-crash-demo.json: one reviewed self-email, subject Week5 recovery self-test c610cd43. scripts/outbox_crash_demo.py send --send-to-self --crash-harness requires an explicit owner request/command approval. It validates its harness ancestor, sends only to the dedicated account itself, then kills that harness after SMTP and before done. Restart must terminate the verified orphan command; the reconcile subcommand refuses to create a new send and checks the existing intent/Sent. The receiver is running solely for this pending owner-approved test. Daily limit remains 1000000. Service auto-restart and the combined launchd crash/overnight demo remain later checkpoints; Step 5 live acceptance is not yet claimed.

Step 5 real crash checks passed (October 31): owner requested and approved the prepared self-email. SMTP returned before the deliberate harness SIGKILL; outbox had intent only. Manual restart repaired one unfinished tool result, killed the verified orphan command group, and sent the restart notification (owner confirmed). Read-only Sent reconciliation returned already_sent and found exactly one Message-ID copy, with one local intent and reconciled done. Owner also confirmed exact pre-crash command recall with no tool calls. A later yes approved only reading the automatic self-email, not another send. See transcripts/crash.md. The temporary receiver exited cleanly; daily cap remains 1000000. Student explanation is pending before accepting the checkpoint. Auto-service restart and overnight acceptance are still not claimed.

Step 5 checkpoint accepted (October 31): owner explained that a recorded intent requires checking how far the operation actually progressed. Clarified that SMTP may already have succeeded before done was written; inspect Sent, confirm/record done if found, and never resend automatically on uncertainty. Live crash, Sent reconciliation and exact-command recall are recorded in transcripts/crash.md. Receiver is stopped; daily limit remains 1000000. Next: Step 6 macOS launchd service and automatic crash/restart verification, followed by the eight-hour overnight run.

### Step 6 macOS service — October 31, 2026

Generated and installed the credential-free user LaunchAgent com.wenyuzhang.ai-study-agent with absolute project venv/Python/script paths, explicit uv PATH and verified CA file, RunAtLoad/KeepAlive, private logs/umask, ten-second spawn throttling and a caffeinate -i -s lifetime assertion. Local .env, settings, limits, schedule and plugin configuration hashes were unchanged. The current Mac is on AC; its original sleep=1-minute setting was preserved, with the service's assertions preventing idle sleep. See service/README.md and service_control.py for status/start/stop; Telegram /stop cancels work rather than unloading launchd.

Real idle SIGKILL test: receiver 86442 -> 86593, launchd runs 1 -> 2, ready in 11.391 seconds without manual bootstrap/restart, zero model calls during the test. Verified the new service's power assertions. The old Telegram subprocess logged a BrokenPipe during the deliberate kill; replacement startup/polling has no conflict errors. All 189 tests passed, including actual graceful SIGTERM cleanup in an isolated runtime. Service is currently active; owner Telegram /status and simple-reply checks remain the Step 6 checkpoint. No real logout/login or eight-hour night is claimed. Daily cap stays 1000000; weekday research remains 08:00 America/Los_Angeles. The next normal weekday job is Monday November 2 at 08:00 PST, so a weekend night demo will need a separately agreed temporary job.

Step 6 checkpoint accepted: owner confirmed asleep/queue 0, daily limit 1000000 and the exact Telegram reply 后台服务正常. Canonical log confirms one tool-free request (7004 tokens, API cost $0). See transcripts/service.md. Service remains active. Eight-hour overnight verification is next; a temporary weekend job still requires the owner’s choice.

Step 7 overnight window started October 31 at 17:50:37 PDT (00:50:37 UTC November 1). Owner authorized temporary week5-overnight-sunday-20261101, cron 0 8 1 11 *, for November 1 08:00 PST (16:00 UTC), using completed-day stock data. Existing weekday job unchanged. Remove temporary job after verification (cron has no year/one-shot support). Acceptance remains pending; UTC elapsed duration, message/scheduled wakes, idle model calls, usage and delivery must be inspected. See state/overnight-live-acceptance.json and transcripts/night.md.

Step 7 checkpoint accepted November 1: owner confirmed both scheduled summary and fresh Telegram message reply. 17.39 elapsed UTC hours, 2 wakes, 3 model requests, 31925 tokens, API-reported cost $0, zero idle model requests. Receiver PID/runs unchanged. Temporary Sunday task removed; weekday 08:00 America/Los_Angeles report and 1000000-token cap preserved. See transcripts/night.md. Next: review submission artifacts and privacy before any publication.

## Status — November 1 submission preparation

Steps 1–7 are accepted; the always-on service remains active with the owner-approved weekday 08:00 report and 1000000-token daily cap. Local submission preview is prepared at /tmp/week5-submission-review/week-5/submissions/WenyuZhang2001/ with a sanitized service export, per-wake night accounting, actual compaction summary, five demos and failures. Candidate files passed the bounded credentials/address/path review. Publication is pending the owner's Step 8 privacy checkpoint; nothing has been pushed and no Week 5 PR has been opened.
