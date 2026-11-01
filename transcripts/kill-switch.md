# Week 5 — real phone kill switch

Privacy-reviewed evidence summary from the live runtime's append-only log and a subsequent ps process-group check. The owner confirmed the stop reply on Telegram.

1. With normal approved limits restored, the owner asked the real Telegram bot to run `sleep 600` using bash and approved that specific command by a linked reply.
2. At 23:37:02 PDT, the harness logged process_start with PID 45913. Process creation happens only after approval.
3. The owner sent /status, then /stop at 23:37:43 PDT.
4. At 23:37:43 PDT, process_end recorded exit_code=-9, stopped=true and timed_out=false. The command ran for 41.644 seconds, less than the tool's 60-second timeout; this was the code kill switch, not timeout expiry.
5. A permitted ps check found no live process with that PID or in its process group. The receiver remained alive, repaired the interrupted tool history and returned to event waiting. No stopped work was automatically retried.
6. Stopped the temporary receiver after acceptance. Daily token cap remains 100000; no overnight service is active.

The Telegram watcher handles /stop outside the event queue, calls Agent.cancel, sets the stop event, and kills the command process group. In-flight network calls cannot be recalled, but their late results cannot start later tools. New process lifecycle records avoid the earlier external-observer timeout limitation.

## Final status clarification

Historical limits/service statements above describe the kill-switch checkpoint. Final normal daily cap is 1000000; launchd service and overnight acceptance subsequently passed.
