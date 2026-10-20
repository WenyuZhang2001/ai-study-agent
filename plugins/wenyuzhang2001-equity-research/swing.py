"""Five-session research model with chronological, purged walk-forward evaluation."""
import math

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HORIZON = 5
LABEL_DELAY = HORIZON + 1
MIN_TRAIN = 504
REFIT_SESSIONS = 21
ENTRY_PROBABILITY = 0.60


def feature_table(bars, benchmark):
    """All features end at the row's close; no bfill or global scaling."""
    bench = benchmark['Adj Close'].reindex(bars.index)
    if bench.isna().any():
        raise ValueError('Benchmark has missing stock sessions; do not silently bridge date gaps.')
    c = bars['Adj Close']
    r = c.pct_change()
    br = bench.pct_change()
    x = pd.DataFrame(index=bars.index)
    for lag in (1, 5, 20, 63):
        x[f'momentum_{lag}'] = c.pct_change(lag)
    x['distance_sma20'] = c / c.rolling(20).mean() - 1
    x['distance_sma50'] = c / c.rolling(50).mean() - 1
    x['volatility20'] = r.rolling(20).std()
    x['intraday_range'] = (bars.High - bars.Low) / bars.Close
    x['intraday_return'] = bars.Close / bars.Open - 1
    volume = np.log1p(bars.Volume)
    x['relative_log_volume'] = volume - volume.rolling(20).mean()
    x['relative_momentum5'] = c.pct_change(5) - bench.pct_change(5)
    x['relative_momentum20'] = c.pct_change(20) - bench.pct_change(20)
    x['benchmark_momentum5'] = bench.pct_change(5)
    x['benchmark_volatility20'] = br.rolling(20).std()
    return x.replace([np.inf, -np.inf], np.nan)


def targets(bars):
    """At close t: next open t+1 to open t+6 (five open-to-open sessions)."""
    opening = bars['Adjusted Open']
    forward = opening.shift(-LABEL_DELAY) / opening.shift(-1) - 1
    y = pd.Series(np.nan, index=bars.index)
    known = forward.notna()
    y.loc[known] = (forward.loc[known] > 0).astype(int)
    return forward, y


def eligible_training(x, y, decision_index):
    indices = np.arange(len(x))
    # Even a same-close label is conservatively excluded.
    eligible = (indices + LABEL_DELAY < decision_index) & x.notna().all(axis=1).to_numpy() & y.notna().to_numpy()
    return indices[eligible][-1260:]


def fit_model(x, y, indices):
    if len(indices) < MIN_TRAIN or y.iloc[indices].nunique() < 2:
        raise ValueError('Need 504 matured training labels with both outcomes; request more years.')
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000, random_state=0))
    model.fit(x.iloc[indices], y.iloc[indices].astype(int))
    return model


def walk_forward(x, y):
    valid = np.flatnonzero(x.notna().all(axis=1).to_numpy())
    first = next((int(i) for i in valid if len(eligible_training(x, y, i)) >= MIN_TRAIN), None)
    if first is None:
        raise ValueError('Not enough training history; use at least five years.')
    probability = pd.Series(np.nan, index=x.index)
    base = pd.Series(np.nan, index=x.index)
    folds = []
    for start in range(first, len(x), REFIT_SESSIONS):
        train = eligible_training(x, y, start)
        test = np.arange(start, min(start + REFIT_SESSIONS, len(x)))
        test = test[x.iloc[test].notna().all(axis=1).to_numpy()]
        model = fit_model(x, y, train)
        probability.iloc[test] = model.predict_proba(x.iloc[test])[:, 1]
        base.iloc[test] = float(y.iloc[train].mean())
        folds.append({'prediction_start': str(x.index[start].date()),
            'prediction_end': str(x.index[test[-1]].date()),
            'latest_training_feature_date': str(x.index[train[-1]].date()),
            'latest_training_label_end': str(x.index[train[-1] + LABEL_DELAY].date()),
            'training_rows': len(train)})
    return probability, base, folds


def probability_scores(y, p, baseline):
    y, p, baseline = np.asarray(y, int), np.asarray(p, float), np.asarray(baseline, float)
    model_brier, base_brier = brier_score_loss(y, p), brier_score_loss(y, baseline)
    bins = []
    for lo, hi in zip(np.arange(0, 1, .2), np.arange(.2, 1.01, .2)):
        mask = (p >= lo) & ((p < hi) if hi < .999 else (p <= 1))
        count = int(mask.sum())
        if count:
            bins.append({'probability_bin': [round(float(lo), 1), round(float(hi), 1)],
                'count': count, 'mean_prediction': round(float(p[mask].mean()), 4),
                'observed_up_fraction': round(float(y[mask].mean()), 4),
                'small_bin': count < 30})
    return {'observations': len(y), 'observed_up_fraction': round(float(y.mean()), 4),
        'brier_model': round(float(model_brier), 6),
        'brier_training_prevalence_baseline': round(float(base_brier), 6),
        'brier_skill_vs_baseline': round(float(1-model_brier/base_brier), 4) if base_brier else None,
        'log_loss_model': round(float(log_loss(y, p, labels=[0, 1])), 6),
        'log_loss_baseline': round(float(log_loss(y, baseline, labels=[0, 1])), 6),
        'direction_accuracy_at_0_5': round(float(accuracy_score(y, p >= .5)), 4),
        'always_up_accuracy': round(float(y.mean()), 4),
        'auc': round(float(roc_auc_score(y, p)), 4) if len(np.unique(y)) == 2 else None,
        'reliability_bins': bins}


def simulate_swing(bars, p, start, capital, cost_bps, summarize, threshold=ENTRY_PROBABILITY):
    """One non-overlapping five-session trade, next-open entry, forced terminal exit."""
    cash, units, exit_at = float(capital), 0.0, -1
    cost = cost_bps / 10000
    equity, trades = [], []
    entry = None
    for i in range(start, len(bars)):
        opening, close = float(bars['Adjusted Open'].iloc[i]), float(bars['Adj Close'].iloc[i])
        exited = False
        if units and i >= exit_at:
            cash = units * opening * (1 - cost)
            trades.append({**entry, 'exit_date': str(bars.index[i].date()), 'exit_at': 'open',
                'net_return_pct': round((cash / entry['capital_before'] - 1) * 100, 4)})
            units, exited = 0.0, True
        if not units and not exited and i < len(bars)-1 and pd.notna(p.iloc[i-1]) and p.iloc[i-1] >= threshold:
            entry = {'signal_date': str(bars.index[i-1].date()), 'entry_date': str(bars.index[i].date()),
                'entry_probability': round(float(p.iloc[i-1]), 4), 'capital_before': cash}
            units = cash / (opening * (1 + cost))
            cash = 0.0
            exit_at = i + HORIZON
        if i == len(bars)-1 and units:
            cash = units * close * (1-cost)
            trades.append({**entry, 'exit_date': str(bars.index[i].date()), 'exit_at': 'terminal_close',
                'net_return_pct': round((cash / entry['capital_before'] - 1) * 100, 4)})
            units = 0.0
        equity.append(cash + units * close)
    return {**summarize(equity, capital), 'closed_trades': len(trades),
        'trade_win_fraction': round(float(np.mean([t['net_return_pct'] > 0 for t in trades])), 4) if trades else None,
        'recent_trades': [{k: v for k, v in t.items() if k != 'capital_before'} for t in trades[-5:]],
        'first_session': str(bars.index[start].date()), 'last_session': str(bars.index[-1].date()),
        'terminal_liquidation_included': True}



def public_rule_signals(bars):
    """Pre-fixed adaptations, not replications of published portfolio studies."""
    c = bars['Adj Close']
    adjusted_high = bars.High * c / bars.Close
    # Today's completed close is compared only with the preceding 20 highs.
    breakout = c > adjusted_high.shift(1).rolling(20).max()
    # Illustrative long-only pullback, not Lehmann's weekly long/short portfolio.
    pullback = (c.pct_change(5) <= -0.05) & (c > c.rolling(200).mean())
    return {'breakout20_fixed5_adaptation': breakout.astype(float),
            'pullback5_in_uptrend_fixed5_adaptation': pullback.astype(float)}


def run_research(bars, benchmark, capital, cost_bps, summarize):
    x = feature_table(bars, benchmark)
    forward, y = targets(bars)
    p, base, folds = walk_forward(x, y)
    eligible = np.flatnonzero((p.notna() & y.notna()).to_numpy())
    if len(eligible) < 126:
        raise ValueError('Need at least 126 matured walk-forward predictions; increase years.')
    # Display all days but also a pre-fixed non-overlapping evaluation grid.
    sparse = eligible[::LABEL_DELAY]
    scores = probability_scores(y.iloc[eligible], p.iloc[eligible], base.iloc[eligible])
    nonoverlap = probability_scores(y.iloc[sparse], p.iloc[sparse], base.iloc[sparse])
    latest_train = eligible_training(x, y, len(x)-1)
    latest_model = fit_model(x, y, latest_train)
    latest_p = float(latest_model.predict_proba(x.iloc[[-1]])[0, 1])
    first = int(np.flatnonzero(p.notna().to_numpy())[0]) + 1
    sensitivity = {}
    for bps in sorted(set([0.0, float(cost_bps), 25.0])):
        sensitivity[str(bps)] = simulate_swing(bars, p, first, capital, bps, summarize)
    # Comparisons are all liquidated at the last close and include both side costs.
    baseline_p = pd.Series(1.0, index=bars.index)
    baseline = simulate_swing(bars, baseline_p, first, capital, cost_bps, summarize)
    c = cost_bps / 10000
    bh_units = capital / (float(bars['Adjusted Open'].iloc[first]) * (1+c))
    bh_equity = (bh_units * bars['Adj Close'].iloc[first:]).tolist()
    bh_equity[-1] *= 1-c
    buyhold = summarize(bh_equity, capital)
    # SMA control over exactly the same dates, with terminal liquidation.
    trend = bars['Adj Close'].rolling(50).mean() > bars['Adj Close'].rolling(200).mean()
    cash, units, trend_curve = float(capital), 0.0, []
    for i in range(first, len(bars)):
        op, close = float(bars['Adjusted Open'].iloc[i]), float(bars['Adj Close'].iloc[i])
        if trend.iloc[i-1] and units == 0:
            units, cash = cash/(op*(1+c)), 0.0
        elif not trend.iloc[i-1] and units:
            cash, units = units*op*(1-c), 0.0
        if i == len(bars)-1 and units:
            cash, units = units*close*(1-c), 0.0
        trend_curve.append(cash + units*close)
    public_controls = {name: simulate_swing(bars, signals, first, capital, cost_bps, summarize)
                       for name, signals in public_rule_signals(bars).items()}
    brier_better = scores['brier_model'] < scores['brier_training_prevalence_baseline']
    loss_better = scores['log_loss_model'] < scores['log_loss_baseline']
    cost_result = sensitivity[str(float(cost_bps))]
    heuristic = brier_better and loss_better and nonoverlap['brier_model'] < nonoverlap['brier_training_prevalence_baseline']
    recent = [{'signal_date': str(bars.index[i].date()), 'probability': round(float(p.iloc[i]), 4),
               'realized_gross_return_pct': round(float(forward.iloc[i])*100, 4),
               'label_end_date': str(bars.index[i+LABEL_DELAY].date())} for i in eligible[-5:]]
    return {'target': 'Positive adjusted return from next-session open to the open five trading sessions later (t+1 to t+6).',
        'latest_estimate': {'signal_date': str(bars.index[-1].date()),
            'gross_up_probability_pct': round(latest_p*100, 1),
            'training_up_frequency_pct': round(float(y.iloc[latest_train].mean())*100, 1),
            'latest_training_label_end': str(bars.index[latest_train[-1]+LABEL_DELAY].date()),
            'interpretation': 'Uncalibrated model estimate; not an established probability or order recommendation.'},
        'model': {'name': 'StandardScaler + L2 logistic regression', 'C': .1,
            'features': list(x.columns), 'training_rows_min': MIN_TRAIN, 'training_rows_max': 1260,
            'refit_every_sessions': REFIT_SESSIONS, 'label_delay_sessions': LABEL_DELAY,
            'training_label_end_strictly_before_prediction_block': True,
            'separate_latest_refit': True, 'hyperparameter_search': False},
        'evaluation': {'daily_overlapping_labels': scores, 'every_sixth_signal_nonoverlapping_labels': nonoverlap,
            'first_signal': str(bars.index[eligible[0]].date()), 'last_matured_signal': str(bars.index[eligible[-1]].date()),
            'latest_outcomes_not_yet_known': LABEL_DELAY,
            'calibration': 'Reliability bins are diagnostics, not post-hoc calibration; overlapping labels invalidate IID confidence claims.',
            'heuristic_beats_prevalence_on_brier_and_logloss': bool(heuristic),
            'validation_status': 'EXPERIMENTAL_REQUIRES_FORWARD_PAPER_VALIDATION' if heuristic else 'NO_DEMONSTRATED_PROBABILITY_EDGE',
            'recent_matured_predictions': recent, 'last_two_training_audits': folds[-2:]},
        'simulation': {'capital_usd': capital, 'entry_threshold': ENTRY_PROBABILITY,
            'holding_sessions': HORIZON, 'position_rule': 'One position per symbol; no overlapping trades, no same-open reentry after exit.',
            'assumed_one_way_cost_bps': cost_bps, 'by_one_way_cost_bps': sensitivity,
            'controls_same_dates_and_cost': {'always_enter_same_holding_rule': baseline,
                'buy_and_hold': buyhold, 'sma50_200': summarize(trend_curve, capital), **public_controls},
            'public_control_conventions': {
                'breakout20_fixed5_adaptation': 'Close above prior 20 adjusted daily highs; next open entry, fixed five-session hold.',
                'pullback5_in_uptrend_fixed5_adaptation': 'Five-session close return <= -5% and close above SMA200; next open entry, fixed five-session hold.',
                'replication_status': 'Simplified long-only adaptations, not published original portfolio replications; thresholds pre-fixed, not tuned.'},
            'net_profit_is_positive': cost_result['total_return_pct'] > 0},
        'limitations': ['Price/volume features only; no news, earnings, fundamentals or portfolio sizing.',
            'Fractional vendor-adjusted units, zero cash yield, no taxes, fixed costs; no whole-share execution model.',
            'Sample windows and features are research design choices; viewing these results makes this an inspected sample.',
            'Daily labels overlap; non-overlap grid is smaller and still not statistically independent.',
            'Current surviving watchlist and revised adjusted data are not point-in-time universes.',
            'Higher up probability does not establish positive expected net return or acceptable downside.',
            'Latest probability and simulated signals are research outputs; no automatic orders.']}
