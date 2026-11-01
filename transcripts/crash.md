# Week 5 — real crash recovery and one self-email

Privacy-reviewed evidence summary from the actual production-mode journal, outbox, Sent inspection and owner confirmations. The receiver was restarted manually; launchd auto-restart belongs to the next checkpoint.

## Event sequence

1. Owner explicitly requested the prepared self-email and approved exactly this bash command by a linked Telegram yes reply:
   `.venv/bin/python scripts/outbox_crash_demo.py send --send-to-self --crash-harness`
2. Before SMTP, the sending tool fsynced one outbox intent with a deterministic Message-ID. Recipient was only the dedicated agent account itself. No teacher/third party received a test email.
3. SMTP returned at 16:59:55 PDT. The explicit fault-injection hook killed its verified harness ancestor before done was written. The harness process exited 137; inspection found intent only and one orphan send command waiting before done.
4. Manually restarted the receiver. At 17:00:42 PDT, it restored history and inserted one tool result: interrupted by restart, not re-run, check effects before retrying. Claimed work became interrupted rather than requeued. At 17:00:42 PDT, it killed the original command group only after matching its recorded PID/start fingerprint.
5. The owner confirmed the Telegram restart notification arrived before the later read-mail approval. The claimed test event was retained as interrupted, not replayed; no model request was used just to announce restart. Separate regression tests cover pause/FIFO/cursor persistence.
6. Reconciliation used a read-only callback that cannot initiate SMTP. Sent confirmed the existing Message-ID, and the original intent gained one reconciled done record. Sent Message-ID search found exactly 1 copy; local outbox had exactly 1 intent and 1 done for this message. No automatic resend occurred.
7. The self-email's arrival generated a labelled automatic-mail wake. The owner approved its read_message request; this only read the email and marked it read, not another send. No second intent or SMTP send was added.
8. The owner asked which bash command had been approved before restart. The agent recalled the exact command above with zero tool calls, and the owner confirmed it.
9. Stopped the temporary receiver cleanly. Daily token cap remains 1000000. No unattended service is installed yet.

## What this proves and what it does not

This was a real harness SIGKILL after an actual SMTP send, followed by journal repair, verified orphan cleanup, Sent reconciliation and command recall. Local and Sent evidence agree on one send. An intent without done does not prove delivery failed: if Sent is unavailable or has no confirmed copy, the code stops and refuses to resend automatically. This conservative at-most-once policy cannot promise unconditional exactly-once delivery.

The live test used a separate manual restart. Automatic service restart and the eight-hour overnight run remain pending. The student explained: ‘因为现在的序列里已经有了这个，只是没完成而已，需要检查进行到哪里了’. Clarification recorded: missing done means local confirmation is incomplete, not necessarily that SMTP delivery failed. Inspect Sent first; confirm and mark done when found; otherwise do not resend automatically. The Step 5 understanding checkpoint is accepted.

## Final status clarification

The manual SMTP-crash recovery and automatic launchd restart were tested separately, not as one combined SMTP-under-launchd fault injection. Later idle SIGKILL under launchd restarted receiver 86442 → 86593 in 11.391 seconds, runs 1 → 2, zero idle model requests; see service.md. The overnight run subsequently passed.
