# Week 5 — real compaction and recall demo

This transcript is an evidence summary. The large old history was explicitly labelled synthetic demonstration data, not real prior owner conversations. Model calls, tool approval, the shared budget and Telegram messages were real. No email or research tools were used.

## Configuration

- Trigger: 20000 API-reported input tokens; keep the most recent 2 complete user turns.
- Normal daily cap: 100000 tokens. After a failed first attempt, owner authorized a temporary 200000 cap solely for the retry; it was restored after testing.
- Demo-only exact identifier in old context: W5-ORBIT-731.

## Evidence

1. First attempt crossed the trigger at 21583 tokens but read memory before the single available tool round. A later text-only tool request was not executed. No compaction committed; original history remained. Fixed this with write-only schema, a required structured first write and code-level contract checks.
2. Retry: the real owner sent the start request and approved a structured memory write. The trigger request reported 21583 input tokens.
3. The approved write returned saved=true, and the model replied DONE. A tool-free summary request reported 21678 input tokens; the summary committed with Goal/Done/Open/Decisions/Exact/People and the exact identifier.
4. Kept 2 complete recent user turns verbatim (4 message objects), checked directly against the pre-maintenance log. Tool-chain tests verify calls/results are not split.
5. The owner then asked for the exact demo identifier without tools. The next real request reported 1344 input tokens, including the new question and refreshed files; reply was W5-ORBIT-731. Owner confirmed it. The identifier was absent from the retained tail and restored memory, and present in the summary.
6. No extra model call was made only to count tokens. The before/after API measurements were 21583 -> 1344; summary-request input count was recorded separately.

## Memory failure and repair

Inspection caught that the approved memory write contained only a placeholder heading, overwriting the previous preference notes. We did not call this a semantically correct memory save. Restored the exact original notes from the pre-write system-prompt snapshot, then added preserve-existing checkpoint writes and a committed-write generation check. These repairs are covered by regression tests; the restore is explicitly distinguished from the original model write. All 155 tests passed. The live recall demonstrates summary retention, not a claim that the model saved the identifier into memory.

## Finish and remaining review

- Temporary receiver stopped cleanly. Daily cap restored to 100000.
- Today's total, including the failed attempt, retry and recall: 154375 tokens, 8 API requests. New requests are blocked until local midnight under the restored cap. API-reported cost was zero; this is not a separate billing audit.
- Student loss review completed: owner observed that wallpaper color was not preserved in the summary (the synthetic original specified olive). This demonstrates loss of a detail while the exact project identifier survived. The course compaction checkpoint is accepted.
- Durable crash resume/outbox, service/crash demo and the overnight run remain unfinished.

## Final status clarification

The pending-step and budget statements above describe the compaction checkpoint, not current status. Final daily cap is 1000000, crash recovery/service/night passed. The following is the actual generated summary, including its claims about the memory write; the placeholder-memory failure and repair above qualify those claims.

```text
# Compaction 2026-10-31T16:00:58.164569-07:00

Before API input tokens: 21583
Summary-request API input tokens: 21678
After API input tokens: pending next actual request (not an estimate).
Original messages: 9
Replacement messages: 6
Verbatim recent user turns: 2

## Goal
Week 5 compaction demonstration: verify the 20 000-token trigger retains the most recent 2 complete user turns and preserves the demo identifier W5-ORBIT-731.

## Done
- Synthetic demo turns 1–3 processed (demo-only, not real owner history).
- Real owner Telegram message received at 2026-10-31 16:00:17 PDT: “开始压缩演示，请只回复‘准备好了’，不要调用工具”.
- Replied “准备好了” as instructed.
- Memory checkpoint written to memory/MEMORY.md (owner-approved, demo run 2026-10-31 PDT).

## Open
- Compaction itself (this summary) is the final step; no further actions pending.

## Decisions
- Demo identifier W5-ORBIT-731 must survive compaction (marked demonstration-only).
- No tools called during the owner’s “准备好了” exchange.
- Memory write approved and saved.

## Exact
- Demo project identifier: W5-ORBIT-731 (demonstration-only).
- Compaction trigger: 20 000 reported input tokens, keep 2 recent user turns.
- Memory path: memory/MEMORY.md.
- Owner message timestamp: 2026-10-31 16:00:17 PDT (Telegram).

## People
- Owner (Telegram) – initiated compaction demo.
- Assistant – executed demo responses, memory write, and this summary.

Next-request API input tokens: 1344
```
