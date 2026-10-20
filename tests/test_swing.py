import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/wenyuzhang2001-equity-research'
_swing_spec = importlib.util.spec_from_file_location('swing', PLUGIN / 'swing.py')
swing = importlib.util.module_from_spec(_swing_spec)
sys.modules['swing'] = swing
_swing_spec.loader.exec_module(swing)
spec = importlib.util.spec_from_file_location('equity_server', PLUGIN / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def fixture(n=900, seed=4):
    rng = np.random.default_rng(seed)
    close = 100*np.exp(np.cumsum(rng.normal(.0002, .02, n)))
    opening = close*np.exp(rng.normal(0, .005, n))
    return pd.DataFrame({'Open': opening, 'Close': close, 'Adj Close': close,
        'Adjusted Open': opening, 'High': np.maximum(opening, close)*1.01,
        'Low': np.minimum(opening, close)*.99, 'Volume': rng.integers(1000,100000,n)},
        index=pd.bdate_range('2018-01-01', periods=n))


class SwingTests(unittest.TestCase):
    def test_label_matches_executable_five_session_return(self):
        bars = fixture(15)
        bars['Adjusted Open'] = np.arange(10,25, dtype=float)
        forward, y = swing.targets(bars)
        self.assertAlmostEqual(forward.iloc[0], 16/11-1)
        self.assertEqual(y.iloc[0], 1)
        self.assertTrue(y.tail(6).isna().all())

    def test_features_do_not_change_when_future_changes(self):
        bars, bench = fixture(), fixture(seed=10)
        before = swing.feature_table(bars, bench)
        bars.iloc[700:, :6] *= 10
        bench.iloc[700:, :6] *= 5
        after = swing.feature_table(bars, bench)
        pd.testing.assert_frame_equal(before.iloc[:700], after.iloc[:700])

    def test_training_labels_mature_strictly_before_decision(self):
        bars = fixture()
        x = swing.feature_table(bars, fixture(seed=10))
        _, y = swing.targets(bars)
        train = swing.eligible_training(x, y, 650)
        self.assertTrue((train + 6 < 650).all())
        self.assertEqual(train[-1], 643)
        model = swing.fit_model(x, y, train)
        np.testing.assert_allclose(model[0].mean_, x.iloc[train].mean(), atol=1e-10)

    def test_out_of_sample_predictions_do_not_use_future_labels(self):
        bars, bench = fixture(), fixture(seed=10)
        x = swing.feature_table(bars, bench)
        _, y = swing.targets(bars)
        p, base, audits = swing.walk_forward(x, y)
        changed = y.copy()
        changed.iloc[700:] = 1-changed.iloc[700:]
        p2, _, _ = swing.walk_forward(x, changed)
        pd.testing.assert_series_equal(p.iloc[:700], p2.iloc[:700])
        self.assertTrue(p.dropna().between(0,1).all())
        self.assertTrue(all(a['latest_training_label_end'] < a['prediction_start'] for a in audits))

    def test_trade_holds_five_sessions_and_pays_both_costs(self):
        bars = fixture(10)
        bars['Adjusted Open'] = 10.0
        bars['Adj Close'] = 10.0
        p = pd.Series(np.nan, index=bars.index)
        p.iloc[0] = .7
        result = swing.simulate_swing(bars,p,1,1000,10,server.metrics)
        self.assertEqual(result['closed_trades'],1)
        self.assertEqual(result['recent_trades'][0]['entry_date'],str(bars.index[1].date()))
        self.assertEqual(result['recent_trades'][0]['exit_date'],str(bars.index[6].date()))
        self.assertAlmostEqual(result['final_value_usd'],round(1000/1.001*.999,2))

    def test_no_signal_no_trade_and_last_day_cannot_enter(self):
        bars = fixture(10)
        p = pd.Series(np.nan, index=bars.index)
        p.iloc[-2] = .9
        result = swing.simulate_swing(bars,p,1,1000,10,server.metrics)
        self.assertEqual(result['closed_trades'],0)
        self.assertEqual(result['final_value_usd'],1000)

    def test_forced_terminal_close_is_costed(self):
        bars = fixture(5)
        bars['Adjusted Open'] = 10.0
        bars['Adj Close'] = 20.0
        p = pd.Series([.9]*5,index=bars.index)
        result = swing.simulate_swing(bars,p,1,1000,10,server.metrics)
        self.assertEqual(result['recent_trades'][-1]['exit_at'],'terminal_close')
        self.assertEqual(result['final_value_usd'],round(1000/1.001*2*.999,2))

    def test_incomplete_benchmark_is_rejected(self):
        bars, bench = fixture(), fixture(seed=10).iloc[1:]
        with self.assertRaises(ValueError):
            swing.feature_table(bars,bench)

    def test_breakout_excludes_current_day_high(self):
        bars = fixture(230)
        bars['Adj Close'] = 100.0
        bars['Close'] = 100.0
        bars['High'] = 101.0
        bars.loc[bars.index[-1], ['Adj Close', 'Close']] = 102.0
        bars.loc[bars.index[-1], 'High'] = 110.0
        signals = swing.public_rule_signals(bars)
        self.assertEqual(signals['breakout20_fixed5_adaptation'].iloc[-1], 1)
        self.assertEqual(signals['breakout20_fixed5_adaptation'].iloc[-2], 0)

    def test_pullback_uses_uptrend_filter(self):
        bars = fixture(230)
        bars['Adj Close'] = 100.0
        bars.iloc[-6:-1, bars.columns.get_loc('Adj Close')] = 130.0
        bars.iloc[-1, bars.columns.get_loc('Adj Close')] = 120.0
        signals = swing.public_rule_signals(bars)
        self.assertEqual(signals['pullback5_in_uptrend_fixed5_adaptation'].iloc[-1], 1)
        bars.iloc[-1, bars.columns.get_loc('Adj Close')] = 90.0
        self.assertEqual(swing.public_rule_signals(bars)['pullback5_in_uptrend_fixed5_adaptation'].iloc[-1], 0)

    def test_end_to_end_returns_honest_validation_and_controls(self):
        result = swing.run_research(fixture(),fixture(seed=10),1000,10,server.metrics)
        self.assertEqual(result['simulation']['holding_sessions'],5)
        self.assertIn(result['evaluation']['validation_status'],
            ['NO_DEMONSTRATED_PROBABILITY_EDGE','EXPERIMENTAL_REQUIRES_FORWARD_PAPER_VALIDATION'])
        self.assertGreaterEqual(result['evaluation']['daily_overlapping_labels']['observations'],126)
        self.assertIn('buy_and_hold',result['simulation']['controls_same_dates_and_cost'])


if __name__ == '__main__':
    unittest.main()
