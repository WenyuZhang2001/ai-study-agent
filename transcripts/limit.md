# Week 5 — daily token limit demo

This is a privacy-reviewed evidence summary from the local runtime log, not a fabricated terminal transcript. The owner confirmed receipt of the limit notice on Telegram.

1. Temporarily changed tokens_per_day from 100000 to 1; retained all other approved settings.
2. Owner sent the small-budget test question through the real owner-only Telegram chat.
3. At 23:35:00 PDT, the harness logged: `tokens_per_day reached or insufficient remaining allowance (1 tokens; next-call reserve 23510).`.
4. The harness sent an explanatory limit notice without calling the model. The test session has zero model usage records; persistent accounting showed zero requests and zero tokens, with one wake.
5. Stopped the test receiver, restored tokens_per_day to 100000, and restarted only one receiver for the next demo. The failed task was not retried automatically.

The limit is checked in code before every main/child model request, using shared persistent reservations. This test demonstrates pre-call refusal, not the cost of an already-running API request. No email was sent.

## Final status clarification

The 100000-token restore above was the historical test setting. Owner later raised the normal daily cap to 1000000; see limits.toml.
