# Week 5 failures and limitations

## October 30 — course branch update
- What happened: `git pull --ff-only upstream main` reported `Not possible to fast-forward, aborting.`
- How noticed: Week 5 files were absent from the current Week 4 submission branch.
- What changed: fetched upstream successfully and read the assignment and full guide using `git show upstream/main:<path>`. Left the existing branch unmerged.

## October 30 — incomplete test environment
- What happened: the harness venv lacked pandas, numpy, yfinance httpx and scikit-learn. Five regression modules failed to import; 62 tests passed in that run, including all seven new watcher tests.
- How noticed: unittest discovery reported ModuleNotFoundError.
- What changed: reran with temporary `uv run --with` dependencies rather than changing runtime requirements. The first retry exposed missing scikit-learn; after including it, all 96 tests passed.

## Known unfinished Step 1 behavior
The current inbox tool returns only the newest 20 headers. A burst of more than 20 messages between polls may lose notifications; UIDVALIDITY changes are not yet detected. The event queue is in memory: a crash can lose queued mail already saved as seen. These are unresolved limitations, not demonstrated fixes; inbox pagination and durable recovery remain to be implemented. No live demo or overnight run is claimed.

## October 30 — target Mac migration
- ZIP/staging source matched; merged Step 1 while preserving settings and plugin configuration.
- Target initially lacked .env, uv and MCP dependencies. Installed dependencies in ignored .venv and created a private .env template, with no secrets entered.
- Initial regression run had two localhost socket permission errors under sandbox; rerun with permitted local access passed all 96 tests.
- Real Telegram/email wakes, idle token measurements and cross-Mac receiver shutdown confirmation remain pending. No receiver or unattended service was started.

## October 30 — live TLS and Step 1 acceptance
- Initial email watcher failed before baseline because the local Python default trust store could not verify TLS. Verified Gmail with certifi, passed SSL_CERT_FILE into the receiver, and forwarded SSL_CERT_FILE/SSL_CERT_DIR to plugin subprocesses; verification was never disabled.
- Restarted only after stopping the first receiver. Real Telegram and email each triggered one wake and one model call; owner confirmed both replies. Mail was labelled untrusted and header-only. An idle gap of 174.7 seconds added no model call; a later mailbox poll caused no duplicate wake. Successful session had no watcher or outer errors. All 96 regression tests passed after adjustment.
- Stopped after acceptance; this intermediate version is still not ready for unattended overnight operation.

## October 30 — phone control implementation
- Corrected the milestone numbering: course Step 2 is scheduling, Step 3 is control; the agreed next slice is phone commands only.
- Inspection found the main-agent bash call did not register its process with Agent.cancel. It now uses the existing process-group cancellation path, as children already did, without imposing the child's active deadline on main.
- Commands are parsed before approval replies, so replying /stop to an approval prompt stops work rather than merely declining that approval. A stopped turn is replaced only after it unwinds, with interrupted tool results and an explicit conversation note; stopped work is not retried automatically.
- Initial tests exposed a test-fixture type registration omission and sandbox denial of ps. Fixed the fixture and ran permitted process checks. All 102 regression tests passed. Live phone acceptance is pending.

## October 30 — live stop evidence window
- What happened: the temporary process observer ran for 240 seconds and expired before the owner performed the Telegram stop test. No running sleep PID was captured.
- How noticed: state/control-live-process.json had an empty observed list. Control logs confirmed stop and interruption handling; a later ps check showed no remaining sleep, but those do not prove the process was running before the stop.
- What changed: recorded the live cancellation path separately from the incomplete formal kill-switch demo. The formal demo must be repeated with durable process-start/exit evidence rather than relying on a short observer window.

## October 30 — scheduler regression cleanup race
- What happened: the first 115-test run failed while deleting a temporary directory (Directory not empty).
- How noticed: the control test's cancelled child was still completing a log write after its delayed model response was released.
- What changed: the test now joins that known worker before deleting its temporary logs. The permitted complete rerun passed all 115 tests. Scheduler tests also cover persistent deduplication and failed state writes; crash loss from the in-memory queue remains unresolved.

## October 30 — safeguards inspection and validation
- The original outer loop had no daily/per-wake guard; added shared main/child pre-call reservations, persistent daily accounting and a watchdog for running commands/approval waits. Missing or uncertain API usage remains conservatively charged rather than silently free.
- The email header fetch originally omitted Auto-Submitted/List-Id. Added robot classification, source-context send refusal, original-UID validation for replies, and a persistent per-address attempt cap. Duplicate auto headers and truncated headers fail closed.
- New process-start/end PID records replace the earlier short-lived observer as the formal kill-switch evidence source. Full regression passed 138 tests; the real 1-token limit test produced a phone notice with zero model calls. The normal daily limit was restored afterward.

Resolution of the earlier stop evidence gap: repeated the real Telegram demo after adding persistent PID start/end records. The recorded process exited with signal 9, stopped=true and timed_out=false; ps found the entire group gone. Owner confirmed the stop reply. Both control transcripts are now saved locally, normal budget restored, and test receiver stopped.

## October 31 — compaction accounting and failure handling
- Inspection found repeated full serialized-byte reservations would unnecessarily block maintenance on a growing history. Model calls now reuse the API count only for an unchanged prefix, reserving new material conservatively; changed prefixes retain the full-byte fallback. The daily cap remains unchanged.
- A before/after message count or byte estimate is not an API token measurement. Compaction records the trigger count and waits for the next real request to measure the retained context, with no counting-only API call.
- Added transactional context checkpoints: failures preserve the old history, while approved memory writes remain real side effects. Original append-only log lines are never deleted. All 151 regression tests passed; live acceptance is pending.

## October 31 — live compaction memory checkpoint failed
- What happened: the real demo exceeded the 20000 trigger (21583 reported input tokens), but the checkpoint exposed both memory read/write with only one tool round. The model first used read. The following forced-final call emitted a textual tool-call representation instead of DONE. No text was parsed/executed as a tool.
- How noticed: the owner saw the compaction-aborted notice. Private logs showed a structured read followed by text-only output; the aborted checkpoint retained the nine original messages. No compaction commit occurred and memory was unchanged.
- What changed: checkpoint schema is now write-only with required content, first response requires a structured tool call, and code validates exactly one memory write before execution. Unexpected read/text responses fail after that one call without retries. Failure markers now include phase/error type, without exposing arbitrary exception payloads. Added regressions for the observed read-first path and text-only tool output. All 153 tests passed.
- Cost/status: the failed real attempt consumed 65412 tokens across three calls. Stopped the receiver; kept the approved daily cap at 100000, leaving 34588. A full real retry needs a budget decision or the next local day; Step 4 live acceptance is not claimed complete.

## October 31 — placeholder memory write after successful compaction
- What happened: the fixed live flow produced a valid structured owner-approved memory write, but its content was only a 197-character heading. It replaced the useful preexisting preference notes. The summary itself committed correctly and retained the demo identifier.
- How noticed: inspection showed saved=true with one line; memory lacked both the prior preferences and the demo ID. This was not accepted as a semantically successful memory checkpoint.
- What changed: restored the exact original notes from the committed context's pre-write system-prompt snapshot (repair audited privately). Compaction memory writes now preserve existing lines and append additions; a generation counter requires an actually committed write before DONE can authorize summary. Added tests for heading-only writes and a failed 200-line-cap write followed by false DONE. All 155 tests passed. Normal explicit memory replacement remains owner-approved; maintenance cannot delete existing notes.
- Live status: summary recall and post-compaction input measurement remain pending the owner follow-up. The original model did not save the demo identifier to memory; the recall test must use the committed summary. This distinction is retained in the final transcript.

## October 31 — crash-window inspection
- Found separate durability gaps in the in-memory queue, Telegram startup backlog discard, schedule cursor-before-enqueue handoff, and email intent-after-SMTP behavior. Added durable-before-ack/enqueue sequencing and outbox intent-before-SMTP with conservative Sent reconciliation.
- UID-only newest-20 inbox polling could miss bursts or collide after mailbox changes. Added watermark pagination and an atomically paired account/UIDVALIDITY/seen state; changed mailboxes are baselined explicitly.
- SIGKILL bypasses shell cleanup. Added verified start-fingerprint process-group cleanup, with tests that reject PID reuse and kill a real orphan sleep group.
- Full regression passed 181 tests; no real SMTP sends in tests. Live owner-approved self-email crash/reconciliation remains pending. Do not claim service restart or overnight acceptance yet.

## October 31 — real crash window exercised
- Deliberately killed the verified harness ancestor after SMTP returned and before outbox done. This produced the intended uncertain-delivery window, not an accidental untracked retry.
- Manual restart repaired one pending tool result and killed the fingerprint-matched orphan send command. Reconciliation confirmed one Sent copy and marked the original intent done without SMTP.
- The self-email generated an additional read approval; owner answered yes. Logs showed read_message only and unchanged single-send outbox state. Exact-command recall passed; receiver stopped afterward.
- Inspected the demo reconcile helper to ensure it cannot initiate fresh SMTP even if its draft changes: both delivery and pre-send callbacks now refuse new sending. No real duplicate was sent. Auto-restart service testing remains next.

## October 31 — launchd service setup
- Initial service unit run exposed a test expectation using the macOS /var alias while generation canonicalized the root to /private/var. Corrected the expected root path and explicitly tested that the venv Python symlink itself is preserved. Final full run passed 189 tests.
- During deliberate idle SIGKILL, the old Telegram MCP subprocess logged BrokenPipeError when flushing to its dead parent. Kept that evidence; new launchd instance recovered in 11.391 seconds, with no 409 conflict/polling errors and no idle model calls. Its caffeinate assertions were re-established.
- Installation preserved existing local configuration and avoided secrets in plist/environment output. User LaunchAgent is active; phone confirmation and overnight test remain pending.

## November 1 — final resolution and overnight inspection
- Earlier pending/incomplete sections are chronological: durable queue, mailbox watermark pagination and UIDVALIDITY scoping were implemented in Step 5; real phone/service/night checks subsequently passed.
- Overnight crossed the daylight-saving fallback; measured elapsed time in UTC to avoid a one-hour error. The temporary weekend cron has no year/one-shot field; removed it after the owner-confirmed run and verified the original weekday schedule.
- No overnight restart or idle model request was observed. Scheduled and message wakes delivered; 31925 tokens across 3 requests, API-reported cost $0.
- Inspection helper initially assumed all assistant content was text; structured tool-call messages have content=null. Used event/role-aware extraction without changing runtime behavior.
- Crash SMTP and service restart were separate real tests; no single combined SMTP/launchd fault-injection is claimed.
