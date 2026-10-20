---
name: equity-research
description: Use for daily US tech, semiconductor and Tesla market research, trend comparisons, five-session probability diagnostics and cost-aware swing research.
---
1. Call get_daily_bars to inspect provenance, last completed date and data quality. All prices are USD; report the latest bar date, retrieval time and source. Today's New York bar is always excluded, even after close; on-demand post-close same-day reporting is not supported in this version.
2. Use analyze_trend for a stock, or daily_watchlist for the configured list. Semiconductor comparison uses SMH; other stocks use QQQ. Relative return differences are percentage points. Do not equate these benchmarks with the entire US market.
3. Call backtest_trend to evaluate the fixed SMA50/200 rule, and compare holdout net results with the same stock's buy-and-hold. Keep one-way cost assumptions explicit; inspect sensitivity rather than selecting the best historical result. Signal at preceding close, hypothetical execution at next open. Do not optimize on the holdout or present an inspected holdout as fresh validation.
4. Summarize for a phone: completed data date, factual trends, risk, research rule states, validation limitations. A long/cash rule state is not a personalized recommendation or forecast. Never invent a reason for a price movement or imply news was fetched; these tools only fetch prices and volume.

Fractional adjusted units are a research abstraction; dividend timing, taxes, minimum commission and integer-share constraints are omitted. Cash yields zero. Each test period starts flat. Latest-close whole-share counts are illustrative only. Data are vendor-revised, not point-in-time histories; the list selected today creates selection bias. Missing values are errors, not values to forward-fill. Flag stale or incomplete results; do not rank incomplete watchlists. For any actual investment decision, cross-check prices/actions with an authorized market-data source. No order placement or account access exists. Never ask for broker credentials.

## Primary workflow: five-session swing research

Use research_swing for the agreed five-session model (TSLA benchmark QQQ; NVDA/AMD/AVGO/TSM benchmark SMH). Start with the returned evaluation.validation_status and latest bar date. Report when the model fails to beat a training-only prevalence baseline; do not describe a failed validation as a trading edge. The latest probability is an uncalibrated estimate of positive gross next-open to five-sessions-later-open return, not the probability of net profit or of a personalized recommendation succeeding.

Explain Brier/log loss versus baseline, reliability bins and small-bin flags; daily overlapping labels are not independent observations. The non-overlapping grid is a robustness diagnostic, not a proof of independence. Fixed model C=0.1, threshold 0.60, horizon 5 and refit interval 21 were selected as research conventions, not optimized. No model news or earnings context exists.

Simulations use past walk-forward predictions only, one position per symbol, five open-to-open sessions, explicit round-trip cost and forced terminal close. Compare all controls on the same dates. Latest fresh refit is for the latest estimate and is not substituted into historical trades. Inspect sensitivity at default 10 and 25 bps. No individual model probability, better accuracy or positive backtest alone proves expected profitability. If results are inconclusive, recommend further research/paper tracking rather than asserting a trade.
