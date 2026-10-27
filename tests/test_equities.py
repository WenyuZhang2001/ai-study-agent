import asyncio
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'plugins/wenyuzhang2001-equity-research/server.py'
spec = importlib.util.spec_from_file_location('equity_server', SERVER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(opens, closes):
    return pd.DataFrame({'Adjusted Open': opens, 'Adj Close': closes},
                        index=pd.bdate_range('2020-01-01', periods=len(opens)))


class ResearchTests(unittest.TestCase):
    def test_signal_cannot_fill_same_day(self):
        bars = fixture([10, 10, 100], [10, 100, 100])
        signal = pd.Series([False, True, True])
        result = mod.simulate(bars, signal, 1, 3, 1000, 0)
        self.assertEqual(result['final_value_usd'], 1000)
        self.assertEqual(result['one_way_trades'], 1)

    def test_overnight_and_exit_cost(self):
        bars = fixture([10, 10, 20], [10, 10, 20])
        signal = pd.Series([True, False, False])
        result = mod.simulate(bars, signal, 1, 3, 1000, 10)
        self.assertAlmostEqual(result['final_value_usd'], round(1000/1.001*2*.999, 2))
        self.assertEqual(result['one_way_trades'], 2)

    def test_flat_market_cost_and_initial_drawdown(self):
        bars = fixture([10]*5, [10]*5)
        result = mod.simulate(bars, pd.Series([True]*5), 1, 5, 1000, 10)
        self.assertEqual(result['final_value_usd'], round(1000/1.001, 2))
        self.assertLess(result['max_drawdown_pct'], 0)

    def test_rising_benchmark_known_result(self):
        bars = fixture([10, 10, 20], [10, 10, 30])
        result = mod.simulate(bars, pd.Series([True]*3), 1, 3, 1000, 0)
        self.assertEqual(result['final_value_usd'], 3000)
        self.assertEqual(result['total_return_pct'], 200)

    def test_historical_date_is_not_misreported_as_stale_download(self):
        bars=pd.DataFrame({'Open':100.0,'High':101.0,'Low':99.0,'Close':100.0,
                           'Adj Close':100.0,'Volume':1000},
                          index=pd.bdate_range(end='2025-10-17',periods=300))
        with patch.object(mod.yf,'Ticker') as ticker:
            ticker.return_value.history.side_effect=lambda **kw: bars.copy()
            _,meta=mod.load_bars('TSLA',3,'2025-10-17')
            self.assertTrue(meta['historical_request'])
            self.assertEqual(meta['calendar_days_from_requested_as_of'],0)
            self.assertIsNone(meta['freshness_warning'])
            _,gap=mod.load_bars('TSLA',3,'2025-10-31')
            self.assertEqual(gap['calendar_days_from_requested_as_of'],14)
            self.assertIsNotNone(gap['freshness_warning'])

    def test_cash_and_invalid_inputs(self):
        result = mod.simulate(fixture([10]*3, [10]*3), pd.Series([False]*3), 1, 3, 1000, 10)
        self.assertEqual(result['final_value_usd'], 1000)
        self.assertIsNone(result['sharpe_zero_cash_rate'])
        with self.assertRaises(ValueError):
            mod.symbol_name('../.env')
        with self.assertRaises(mod.ToolError):
            mod.backtest_trend('TSLA', capital_usd=float('nan'))


async def smoke():
    import os
    target = ROOT / 'research-output'
    target.mkdir(exist_ok=True)
    async with Client(StdioServerParameters(command='uv', args=['run', '--script', str(SERVER)]), read_timeout_seconds=240) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {'get_daily_bars', 'analyze_trend', 'backtest_trend', 'daily_watchlist', 'research_swing', 'get_company_news', 'analyze_company_news', 'get_company_fundamentals', 'get_company_events'}
        print('Research MCP tools/list passed.', flush=True)
        results = {}
        for name, args in [
            ('get_daily_bars', {'symbol': 'TSLA', 'as_of': '2025-10-17', 'recent_rows': 3}),
            ('analyze_trend', {'symbol': 'NVDA', 'as_of': '2025-10-17', 'benchmark': 'SMH'}),
            ('backtest_trend', {'symbol': 'TSLA', 'as_of': '2025-10-17', 'capital_usd': 1000}),
            ('daily_watchlist', {'as_of': '2025-10-17'}),
        ]:
            result = await client.call_tool(name, args)
            text = '\n'.join(b.text for b in result.content if getattr(b, 'type', None) == 'text')
            if result.is_error:
                raise RuntimeError(name + ': ' + text)
            data = json.loads(text)
            if name == 'daily_watchlist':
                assert data['complete'], json.dumps(data)
            results[name] = data
            print(name + ' real-data MCP call passed.', flush=True)
        (target / 'equity-smoke-2025-10-17.json').write_text(json.dumps(results, indent=2))
        print('Saved reproducible historical test outputs (not current quotes).', flush=True)


if __name__ == '__main__':
    import sys
    if '--live' in sys.argv:
        asyncio.run(smoke())
    else:
        unittest.main()
