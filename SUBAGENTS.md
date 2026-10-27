# Subagents in AI Study Agent

Each child runs the same Agent loop as main with a fresh system prompt and task-only context. Agent definitions live in agents/*.md. The main prompt receives type names and descriptions, not child prompts or conversations. Tool names are checked at startup; delegate and subagent are removed from every child. The supplied mail-reader can only list/read mail; quant-researcher has the existing nine read-only research tools.

## Hand-off and background control

`delegate(agent, task)` runs one new child and waits for its report. `subagent(action, ...)` supports:

| Action | Arguments | Result |
| --- | --- | --- |
| start | agent, task | Returns a new id immediately; a daemon thread runs the child. |
| list | none | Type, state, turns, tokens, accumulated active time, task preview and report. |
| send | id, message | Queues the message before the next model call, or resumes an idle child's existing conversation. |
| wait | ids, timeout=60 | Waits for all requested states to settle or timeout; maximum 600 seconds. |
| read | id, last=5 | Last 1–50 log steps, each capped at about 500 characters. |
| stop | id | Permanently marks stopped, cancels pending work and kills the active command group. |

`start` goes through Delegations.start → _create → _launch. `_launch` starts one threading.Thread; `_work` calls child.ask on the shared loop implementation. When the child reports, _work marks it idle, stores the report and queues a completion note. Before the next model call, main drains notes into one user message. The interface also receives a notice while main is idle. Only report/statistics return automatically; `read` is the explicit way to inspect detailed logs.

Running follow-ups use a queue.Queue and are combined before the next model call. A message arriving after the final call is picked up by the worker before it declares itself idle. Idle follow-ups launch a new worker on the same child object; they do not reset its conversation or cumulative model-call budget. Stopped/failed children cannot be resumed.

## Limits, approvals and stopping

A condition lock protects worker records and reservations. At most four workers are active, including synchronous delegates and cancelled workers whose in-flight request is still finishing. Children have a default 20-model-call budget (mail-reader uses 10) and a 10-minute active-episode deadline. A watchdog marks expired background work failed and prevents any late result from causing another action. Runtime errors and limit failures are reported explicitly. Active time excludes idle intervals; the deadline resets on idle continuation, but turns/tokens accumulate.

One shared approval lock serializes main/child questions. Child questions identify `[sa-N · type]`. Original allowlists still apply, and bash always requires approval. Web/Telegram approvals accept cancellation so a stopped child cannot use a later approval. Telegram polling releases its channel lock between short polls, allowing notices while the user is idle.

`stop` reaches Agent.cancel, sets a threading.Event and calls kill_process_group on the saved Popen handle. A child command is started in its own POSIX process group (Windows uses a new process group and taskkill /T /F). The stop event is checked before model/tool calls, after approval and after model results. Commands are killed immediately; an HTTP model call already in flight may finish at its socket timeout, but its late result cannot trigger tools. Session reset/exit stops all children before closing plugins. Runtime transcripts are private, mode 0600, gitignored under logs/<session>/<id>.jsonl.

## Shared MCP connections and usage

All children share the existing PluginRuntime sessions, owned by one asyncio loop. Requests are scheduled concurrently as tasks on that loop rather than processed serially; threads submit through asyncio.run_coroutine_threadsafe. No extra MCP connection is opened per child. Closing the runtime cancels pending calls before sessions close. Remote side effects already underway cannot be recalled; this does not authorize retries.

Every returned model usage record updates the child's totals and main.session_usage['subagent_tokens']. list and UI usage events expose that usage. Main's own API usage remains separately available; session totals are their sum. New logs and publication candidates must be checked for private content before publishing.

## Interface and validation

The web page has a background-agent status panel; web and Telegram accept `/agents`. Completion notes show reports, and approvals carry child labels. Existing running processes need restart to load this code; no running phone receiver was restarted during development.

The 88-test regression run passed after this stage. New tests use fixture model responses, a real local MCP server with overlapping requests, and a real sleep subprocess that is terminated by stop. They cover three concurrent workers, context-preserving follow-up without rereading, queued follow-ups, the four-worker cap, wait/read bounds, turn/deadline failure, late-result blocking, serialized approvals, cancelled approvals, idle notifications and session cleanup. These automated checks are not a claim that the remaining four live homework demos have been completed; those demo transcripts are still pending the course checkpoint.
