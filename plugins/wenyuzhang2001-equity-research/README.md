# wenyuzhang2001-equity-research

Read-only daily US technology, semiconductor and Tesla research for AI in Research week 3, built with Codex. The question is whether a fixed five-session probability model or simple public-rule adaptations add value after assumed trading costs. No broker account, orders or API key is required.

## Run and load

Requires uv and Python 3.11+. Inline dependencies: official MCP SDK v2, yfinance, pandas, numpy and scikit-learn.

```sh
uv run --script /absolute/path/to/wenyuzhang2001-equity-research/server.py
```

The harness discovers the folder automatically under plugins/: `uv run server.py --telegram` starts phone questions and approvals. Full setup: https://github.com/WenyuZhang2001/ai-study-agent . Bash and research tools require explicit approval by default.

Claude Code: `claude --plugin-dir /absolute/path/to/this/plugin`; validate with `claude plugin validate /absolute/path/to/this/plugin` if installed. Codex: `codex mcp add class_equities -- uv run --script /absolute/path/to/this/plugin/server.py`, using the installed CLI's help. The plugin was actually tested in Codex with ephemeral equivalent stdio configuration; it returned the historical TSLA research answer. No Claude validation is claimed.

## Five tools

- get_daily_bars(symbol, as_of='', recent_rows=5): recent OHLCV/adjusted bars and provenance; 1–20 returned rows.
- analyze_trend(symbol, as_of='', benchmark='QQQ'): SMA50/200, returns, volatility and aligned relative performance.
- backtest_trend(symbol, years=5, as_of='', capital_usd=1000, one_way_cost_bps=10): fixed long/cash SMA control with development/holdout segments and same-stock buy-and-hold.
- daily_watchlist(as_of=''): configured AAPL, MSFT, NVDA, AMZN, GOOGL, META, AMD, AVGO, TSM and TSLA; SMH compares semiconductors and QQQ compares other stocks. Edit watchlist.json to change it.
- research_swing(symbol, years=8, as_of='', benchmark='QQQ', capital_usd=1000, one_way_cost_bps=10): five-session model estimates, chronological probability diagnostics and cost-aware simulations. Use SMH for semiconductor comparisons.

Dates are inclusive YYYY-MM-DD. Current New York day is always excluded, even after the close. Data age relative to today is separate from freshness/coverage relative to the requested historical date. Prices are USD for the required US-listed ticker; volume is provider-reported shares. Invalid/missing prices and duplicate/unsorted dates fail; no forward filling or synthetic fallback. Each data request has a 15-second network timeout; allow a longer host tool timeout for watchlists. stdout is exclusively MCP.

## Five-session research method

Target at close t: positive gross adjusted return from open t+1 to open t+6, five open-to-open trading sessions. This is not a next-day forecast or the probability of net profit. Ties count as not up.

Features use trailing 1/5/20/63-session returns, SMA distances, 20-session volatility, intraday range/return, relative log volume, and benchmark-relative returns/volatility. Fixed StandardScaler plus L2 logistic regression (C=0.1) is refitted every 21 sessions on 504–1260 matured labels. Scaling uses training data only. Every training label ends strictly before the first prediction date of its block; unmatured outcomes are purged. No shuffled cross-validation or parameter/threshold optimization is used. The latest estimate uses a separate fresh fit; historical trades use original walk-forward predictions.

Brier/log-loss, direction accuracy, AUC and reliability bins are compared with training-only historical-up-frequency and always-up baselines. Lower Brier/log loss is better. Daily labels overlap; diagnostics also use every sixth signal. That grid is smaller and not guaranteed independent. Bins are calibration diagnostics; no post-hoc recalibration is fitted. Latest probabilities are explicitly uncalibrated. No IID confidence interval, significance or established trading edge is claimed. Even a favorable heuristic validation status remains experimental and requires fresh forward paper validation.

Simulation: p >= 0.60, next-open entry, one long position per symbol, five open-to-open sessions, no overlap or same-open re-entry after exit. Terminal positions are liquidated at the last completed close, including exit cost. This may shorten the final trade. Cost sensitivity includes 0, selected cost (default 10), and 25 basis points per side. Costs combine assumed commission/spread/slippage. Units are fractional adjusted units; no leverage, shorting, taxes, minimum commissions, integer-share sizing or interest on cash. Adjusted Open is Open times Adj Close / Close. Dividend payment timing is approximated, not actual broker accounting.

Controls use the same dates and selected cost: buy-and-hold, SMA50/200, unconditional five-session entry, and the two adaptations below. SMA retains trend-based exits and buy-and-hold remains continuously invested, so risk/exposure is not matched. Inspect drawdown and trading frequency alongside return. This is not a combined multi-stock portfolio simulation.

The older backtest_trend tool uses a 200-session warmup, 80/20 chronological development/holdout, resets each period to cash, and marks terminal holdings without forced liquidation. Its convention differs from research_swing's explicit terminal exit. Returns use 252 sessions/year; Sharpe assumes zero cash yield. Whole-share affordability is illustrative only.

## Public strategy references and adaptations

- [Original Turtle rules](https://tradingblox.com/originalturtles/originalturtlerules.htm): complete systems include breakouts, volatility sizing, stops, pyramiding and portfolio limits. Our breakout20_fixed5_adaptation uses completed Close above the preceding 20 adjusted Highs, next Open entry and fixed five-session holding. It omits the original futures/intraday/risk rules.
- [Lehmann, Fads, Martingales, and Market Efficiency](https://www.nber.org/papers/w2533): weekly cross-sectional reversal research. Our pullback5_in_uptrend_fixed5_adaptation is a separate single-stock rule: five-session return <= -5%, Close above SMA200, next Open entry, fixed five-session hold. The threshold/filter are our illustrative pre-fixed choices, not claimed paper parameters.
- [Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum): past-12-month futures evidence does not establish our five-session equities rules or SMA control.
- [Cross-sectional momentum research](https://www.nber.org/papers/w7159): months-scale winner/loser portfolios are not implemented here; the current ten-stock watchlist is not an unbiased historical universe.

These are adaptations, not original-paper replications. Parameters were not tuned after looking at comparison results.

## Evidence and limitations

Actual historical checks on TSLA/QQQ and NVDA/SMH, eight years ending 2025-10-17, produced NO_DEMONSTRATED_PROBABILITY_EDGE: the model did not beat its simple probability baseline. TSLA's hypothetical USD 1,000 simulation at 10 bps/side ended at USD 1,170.53 with a large drawdown; positive sample profit did not establish a useful prediction model. The student received and reviewed this result from the phone, and Codex independently ran the same research tool.

Only prices/volume are fetched. No news, earnings, fundamentals, personalized portfolio sizing or daily scheduling exists. The selected surviving universe introduces selection bias. Vendor-adjusted histories are revised and not point-in-time vintages. An inspected evaluation is not a new untouched test. High up probability alone does not establish positive expected net return or acceptable downside.

Source: [yfinance documentation](https://ranaroussi.github.io/yfinance/) and [project](https://github.com/ranaroussi/yfinance). Yahoo/yfinance is an unofficial personal-research feed, not a licensed execution source. Check provider rights before redistribution/commercial use. Generated data/receipts remain local and are not submitted. No secrets or whole files should be sent through Telegram.

The harness regression suite verifies future-data isolation, label maturity, train-only scaling, executable holding horizons, costs, controls, and historical coverage warnings. See its README for commands and completed acceptance tests.
