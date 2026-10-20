# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2,<3", "yfinance>=0.2.65,<2", "pandas>=2.2,<4", "numpy>=2,<3", "scikit-learn>=1.7,<2"]
# ///
"""Read-only daily equity research. No brokerage or order-placement code."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import wraps
import json
import logging
import math
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
ROOT = Path(__file__).resolve().parent
WATCHLIST = json.loads((ROOT / 'watchlist.json').read_text())
SOURCE = 'Yahoo Finance via yfinance; unofficial research data'
mcp = MCPServer('wenyuzhang2001-equity-research')


def explain_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ValueError as error:
            raise ToolError(str(error)) from None
    return wrapped


def symbol_name(symbol):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', symbol):
        raise ValueError('Use a US-listed ticker, for example TSLA or NVDA.')
    return symbol


def load_bars(symbol, years=5, as_of=''):
    symbol = symbol_name(symbol)
    if not 2 <= years <= 10:
        raise ValueError('years must be between 2 and 10.')
    today = datetime.now(ZoneInfo('America/New_York')).date()
    final = date.fromisoformat(as_of) if as_of else today - timedelta(days=1)
    if final >= today:
        raise ValueError('as_of must precede today in New York; current-day bars are intentionally excluded.')
    try:
        bars = yf.Ticker(symbol).history(
            start=(final - timedelta(days=years * 366)).isoformat(),
            end=(final + timedelta(days=1)).isoformat(), interval='1d',
            auto_adjust=False, actions=True, repair=False, timeout=15,
        )
    except Exception:
        raise ValueError(f'{symbol}: market-data request failed; retry later or check the ticker. No substitute data were used.') from None
    if bars.empty:
        raise ValueError(f'{symbol}: no bars returned; check the ticker and date.')
    bars.index = pd.to_datetime(bars.index).tz_localize(None).normalize()
    needed = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
    if not set(needed).issubset(bars.columns):
        raise ValueError(f'{symbol}: required adjusted OHLCV fields are missing.')
    bars = bars[needed].copy()
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError(f'{symbol}: duplicated or unordered dates; reject this download.')
    values = bars.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (bars[needed[:-1]] <= 0).any().any() or (bars.Volume < 0).any():
        raise ValueError(f'{symbol}: missing or invalid price/volume data; do not forward-fill.')
    if ((bars.High < bars[['Open', 'Close', 'Low']].max(axis=1)) |
        (bars.Low > bars[['Open', 'Close', 'High']].min(axis=1))).any():
        raise ValueError(f'{symbol}: inconsistent OHLC prices; reject this download.')
    bars['Adjusted Open'] = bars.Open * bars['Adj Close'] / bars.Close
    if len(bars) < 252:
        raise ValueError(f'{symbol}: need at least 252 valid daily bars; got {len(bars)}.')
    return bars, {'symbol': symbol, 'source': SOURCE,
        'source_url': f'https://finance.yahoo.com/quote/{symbol}/history/',
        'retrieved_utc': datetime.now(timezone.utc).isoformat(),
        'requested_as_of': final.isoformat(), 'latest_bar': bars.index[-1].date().isoformat(),
        'bars': len(bars), 'currency': 'USD (US-listed ticker required)',
        'calendar_days_since_latest_bar': (today - bars.index[-1].date()).days,
        'calendar_days_from_requested_as_of': (final - bars.index[-1].date()).days,
        'historical_request': bool(as_of),
        'freshness_warning': 'Latest bar falls more than four calendar days before requested as_of; check exchange holidays and provider coverage.' if (final - bars.index[-1].date()).days > 4 else None,
        'adjustments': 'Adj Close and proportional adjusted Open; vendor revisions may change history.',
        'current_day_excluded': True}


def metrics(equity, initial):
    values = np.asarray([initial, *equity], dtype=float)
    returns = values[1:] / values[:-1] - 1
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0
    return {'final_value_usd': round(float(values[-1]), 2),
        'total_return_pct': round(float(values[-1] / initial - 1) * 100, 3),
        'annualized_return_pct': round(float((values[-1] / initial) ** (252 / len(returns)) - 1) * 100, 3),
        'annualized_volatility_pct': round(std * math.sqrt(252) * 100, 3),
        'sharpe_zero_cash_rate': round(float(np.mean(returns)) / std * math.sqrt(252), 3) if std > 1e-12 else None,
        'max_drawdown_pct': round(float(np.min(values / np.maximum.accumulate(values) - 1)) * 100, 3),
        'sessions': len(returns)}


def simulate(bars, target, start, stop, capital, cost_bps):
    cash, units, trades = float(capital), 0.0, 0
    cost = cost_bps / 10000
    equity = []
    for i in range(start, stop):
        opening, closing = float(bars['Adjusted Open'].iloc[i]), float(bars['Adj Close'].iloc[i])
        # target[i-1] uses information from the preceding close only.
        desired = bool(target.iloc[i - 1])
        if desired and units == 0:
            units = cash / (opening * (1 + cost))
            cash = 0.0
            trades += 1
        elif not desired and units > 0:
            cash = units * opening * (1 - cost)
            units = 0.0
            trades += 1
        equity.append(cash + units * closing)
    return {**metrics(equity, capital), 'one_way_trades': trades,
            'starts': bars.index[start].date().isoformat(),
            'ends': bars.index[stop - 1].date().isoformat(),
            'terminal_position': 'long' if units else 'cash'}


def trend_summary(symbol, as_of='', benchmark='QQQ'):
    bars, meta = load_bars(symbol, 3, as_of)
    close = bars['Adj Close']
    fast, slow = close.rolling(50).mean(), close.rolling(200).mean()
    ret = close.pct_change()
    result = {**meta, 'close_usd': round(float(bars.Close.iloc[-1]), 4),
        'adjusted_close_usd': round(float(close.iloc[-1]), 4),
        'sma50_adjusted': round(float(fast.iloc[-1]), 4),
        'sma200_adjusted': round(float(slow.iloc[-1]), 4),
        'trend_rule_state': 'long' if fast.iloc[-1] > slow.iloc[-1] else 'cash',
        'rule': 'SMA50 > SMA200; descriptive research signal, earliest hypothetical execution next session open',
        'return_1_session_pct': round(float(ret.iloc[-1]) * 100, 3),
        'return_20_sessions_pct': round(float(close.iloc[-1] / close.iloc[-21] - 1) * 100, 3),
        'annualized_volatility_20_sessions_pct': round(float(ret.tail(20).std()) * math.sqrt(252) * 100, 3),
        'drawdown_from_sample_high_pct': round(float(close.iloc[-1] / close.max() - 1) * 100, 3)}
    if benchmark:
        other, _ = load_bars(benchmark, 3, as_of)
        paired = pd.concat([close, other['Adj Close']], axis=1, join='inner').dropna()
        if len(paired) < 21 or paired.index[-1] != bars.index[-1] or paired.index[-1] != other.index[-1]:
            raise ValueError('Benchmark dates do not align; retry data instead of comparing stale dates.')
        gains = paired.iloc[-1] / paired.iloc[-21] - 1
        result['relative_20_common_sessions'] = {'benchmark': symbol_name(benchmark),
            'excess_return_percentage_points': round(float(gains.iloc[0] - gains.iloc[1]) * 100, 3),
            'from': paired.index[-21].date().isoformat(), 'to': paired.index[-1].date().isoformat()}
    return result


@mcp.tool()
@explain_errors
def get_daily_bars(symbol: str, as_of: str = '', recent_rows: int = 5) -> str:
    """Fetch completed daily US-listed OHLCV bars and provenance for checking source data.

    symbol example 'TSLA'; as_of optional inclusive YYYY-MM-DD before today New York.
    recent_rows 1–20. USD prices; volume is shares as reported by provider.
    Current day is always excluded. Return recent rows and data quality metadata;
    do not use this unofficial research feed for order execution or live quotes.
    """
    if not 1 <= recent_rows <= 20:
        raise ValueError('recent_rows must be 1–20.')
    bars, meta = load_bars(symbol, 3, as_of)
    rows = bars.tail(recent_rows).reset_index()
    rows.iloc[:, 0] = rows.iloc[:, 0].astype(str)
    return json.dumps({**meta, 'recent_rows': json.loads(rows.to_json(orient='records', date_format='iso'))}, allow_nan=False)


@mcp.tool()
@explain_errors
def analyze_trend(symbol: str, as_of: str = '', benchmark: str = 'QQQ') -> str:
    """Describe daily trend, 20-session risk, and relative performance without predicting tomorrow.

    symbol e.g. 'NVDA', benchmark e.g. 'SMH' or 'QQQ', optional as_of YYYY-MM-DD.
    Means use dividend/split-adjusted prices; percentages and percentage points
    are labeled. Use after inspecting data; results are research signals, not orders.
    """
    return json.dumps(trend_summary(symbol, as_of, benchmark), allow_nan=False)


@mcp.tool()
@explain_errors
def backtest_trend(symbol: str, years: int = 5, as_of: str = '', capital_usd: float = 1000,
                   one_way_cost_bps: float = 10) -> str:
    """Test a fixed long/cash SMA50/200 daily strategy against same-stock buy-and-hold.

    symbol e.g. 'TSLA'; years 2–10; as_of optional YYYY-MM-DD before today New York.
    capital_usd 100–1000000 is hypothetical, not the owner's account balance.
    one_way_cost_bps 0–100 (10 = 0.10% per side) combines fees and slippage.
    Prior-close signals execute at next adjusted open. Last 20% is a chronological
    holdout; no parameter optimization. Fractional adjusted units, zero cash yield,
    no taxes, no shorting/leverage, marked-to-close without final liquidation.
    Not an integer-share broker simulation or proof of future profitability.
    """
    if not math.isfinite(capital_usd) or not 100 <= capital_usd <= 1000000:
        raise ValueError('capital_usd must be finite and between 100 and 1000000.')
    if not math.isfinite(one_way_cost_bps) or not 0 <= one_way_cost_bps <= 100:
        raise ValueError('one_way_cost_bps must be finite and between 0 and 100.')
    bars, meta = load_bars(symbol, years, as_of)
    close = bars['Adj Close']
    signal = close.rolling(50).mean() > close.rolling(200).mean()
    first = 200
    split = first + int((len(bars) - first) * .8)
    if len(bars) - split < 30:
        raise ValueError('Need at least 30 holdout sessions after 200-session warmup; increase years.')
    buy = pd.Series(True, index=bars.index)
    periods = {}
    for name, start, stop in [('development', first, split), ('holdout', split, len(bars))]:
        periods[name] = {
            'strategy': simulate(bars, signal, start, stop, capital_usd, one_way_cost_bps),
            'buy_and_hold_same_stock': simulate(bars, buy, start, stop, capital_usd, one_way_cost_bps)}
    last_price = float(bars.Close.iloc[-1])
    return json.dumps({**meta, 'rule': 'SMA50 > SMA200, long/cash, next-session open',
        'hypothetical_initial_usd_per_period': capital_usd, 'one_way_cost_bps': one_way_cost_bps,
        'periods': periods,
        'illustrative_whole_shares_at_latest_close_before_costs': math.floor(capital_usd / last_price),
        'limitations': ['Fractional adjusted units; whole-share count is illustrative only, not backtest sizing.',
            'Cash earns zero; taxes, minimum commissions and dividend payment timing not modeled.',
            'Each period restarts in cash; current watchlist is selected today (survivorship/selection bias).',
            'Holdout ceases to be untouched once inspected; do not tune rules to its result.',
            'Corporate-action-adjusted prices are vendor-revised; not point-in-time vintages.',
            'No final forced sale/cost; no live quotes or orders.']}, allow_nan=False)


@mcp.tool()
@explain_errors
def daily_watchlist(as_of: str = '') -> str:
    """Summarize the configured tech, semiconductor and Tesla daily research watchlist.

    as_of optional inclusive YYYY-MM-DD before today New York, e.g. '2025-10-17'.
    Uses SMH for semiconductor relative returns and QQQ for other stocks.
    Returns short numeric rows, per-symbol errors and freshness dates. Missing
    symbols stay explicit; do not rank incomplete results or invent price data.
    Does not schedule runs, message Telegram, or place orders.
    """
    def one(item):
        try:
            return trend_summary(item['symbol'], as_of, item['benchmark'])
        except Exception as error:
            return {'symbol': item['symbol'], 'error': str(error)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(one, WATCHLIST['stocks']))
    return json.dumps({'watchlist': rows,
        'complete': not any('error' in row for row in rows),
        'disclaimer': 'Descriptive completed-day research; no forecast or portfolio allocation.'}, allow_nan=False)


@mcp.tool()
@explain_errors
def research_swing(symbol: str, years: int = 8, as_of: str = '', benchmark: str = 'QQQ',
                   capital_usd: float = 1000, one_way_cost_bps: float = 10) -> str:
    """Research five-session up probabilities and a cost-aware hypothetical swing strategy.

    symbol e.g. 'TSLA' or 'NVDA'; benchmark 'QQQ' (general) or 'SMH' (semiconductors).
    years 5–10; as_of optional inclusive YYYY-MM-DD strictly before today New York.
    capital_usd 100–1000000 is hypothetical; one_way_cost_bps 0–100 per side.
    Logistic regression is refitted every 21 sessions using matured historical
    labels only, with train-only scaling and purged label boundaries. Predicts
    next-open to five-sessions-later-open positive gross return. Returns an
    uncalibrated latest estimate, out-of-sample Brier/log-loss/reliability and
    non-overlapping five-session simulations at fixed p>=0.60, plus controls.
    No strategy optimization, live quotes, broker access or orders. Inspect
    validation_status before interpreting the probability; report failure honestly.
    """
    from swing import run_research
    if not 5 <= years <= 10:
        raise ValueError('years must be 5–10 for training and walk-forward evaluation.')
    if not math.isfinite(capital_usd) or not 100 <= capital_usd <= 1000000:
        raise ValueError('capital_usd must be finite and between 100 and 1000000.')
    if not math.isfinite(one_way_cost_bps) or not 0 <= one_way_cost_bps <= 100:
        raise ValueError('one_way_cost_bps must be finite and between 0 and 100.')
    bars, meta = load_bars(symbol, years, as_of)
    other, bench_meta = load_bars(benchmark, years, as_of)
    if bars.index[-1] != other.index[-1]:
        raise ValueError('Stock and benchmark latest dates differ; retry instead of using stale data.')
    result = run_research(bars, other, capital_usd, one_way_cost_bps, metrics)
    return json.dumps({**meta, 'benchmark': bench_meta, **result}, allow_nan=False)


if __name__ == '__main__':
    mcp.run(transport='stdio')
