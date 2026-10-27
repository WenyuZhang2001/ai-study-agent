import importlib.util
from pathlib import Path
import json
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import xml.etree.ElementTree as ET
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'plugins/wenyuzhang2001-equity-research/company_context.py'
spec = importlib.util.spec_from_file_location('context_under_test', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def item(title='Company raises revenue outlook', link='https://example.com/news',
         published='Tue, 20 Oct 2026 12:00:00 +0000'):
    row = ET.Element('item')
    for name, value in [('title', title), ('link', link), ('description', '<b>Company</b> raises its revenue outlook.'), ('pubDate', published)]:
        ET.SubElement(row, name).text = value
    return row


def snapshot():
    return mod.collect_news('TSLA', now=NOW, fetch=lambda _: [item()])


def assessment(snap, **overrides):
    return {'article_id': snap['articles'][0]['article_id'], 'relevance': 'direct', 'event_type': 'guidance',
            'direction': 1, 'magnitude': 3, 'evidence_quote': 'Company raises revenue outlook',
            'reason_zh': '摘要显示公司上调营收指引。', **overrides}


class NewsTests(unittest.TestCase):
    def test_deduplication_and_time_cutoffs(self):
        rows = [item(), item(link='https://example.com/news?tracking=1'),
                item(title='future story', published='Thu, 22 Oct 2026 12:00:00 +0000'),
                item(title='old story', published='Wed, 01 Oct 2026 12:00:00 +0000'),
                item(title='undated', published='bad'), item(title='local URL', link='file:///etc/passwd')]
        result = mod.collect_news('TSLA', now=NOW, fetch=lambda _: rows)
        self.assertEqual(result['article_count'], 1)
        self.assertEqual(result['dropped'], {'duplicate': 1, 'outside_window_or_future': 2, 'invalid': 2})
        self.assertEqual(result['articles'][0]['summary'], 'Company raises its revenue outlook.')

    def test_feed_failures_and_no_articles_are_not_neutral_scores(self):
        def unavailable(_): raise RuntimeError('network')
        result = mod.collect_news('NVDA', now=NOW, fetch=unavailable)
        self.assertEqual(result['coverage_status'], 'PARTIAL')
        self.assertEqual(len(result['source_errors']), 2)
        classifier = unittest.mock.Mock()
        analyzed = mod.analyze_news(result, classifier)
        classifier.assert_not_called()
        self.assertIsNone(analyzed['analysis']['news_business_impact_score'])

    def test_direct_vs_unrelated_and_known_score(self):
        snap = snapshot()
        self.assertEqual(mod.score_assessments(snap, [assessment(snap)])['news_business_impact_score'], 100)
        result = mod.score_assessments(snap, [assessment(snap, relevance='unrelated')])
        self.assertIsNone(result['news_business_impact_score'])
        self.assertEqual(result['status'], 'NO_DIRECT_COMPANY_EVIDENCE')
        negative = mod.score_assessments(snap, [assessment(snap, direction=-1, magnitude=2)])
        self.assertAlmostEqual(negative['news_business_impact_score'], -66.67)

    def test_fabricated_evidence_duplicate_missing_and_bad_types_fail_closed(self):
        snap = snapshot()
        cases = [[assessment(snap, evidence_quote='fabricated evidence text')], [],
                 [assessment(snap), assessment(snap)], [assessment(snap, relevance=[])],
                 [assessment(snap, direction=True)], [assessment(snap, magnitude=99)],
                 [assessment(snap, direction=0, magnitude=2)], [assessment(snap, article_id='fake')]]
        for rows in cases:
            with self.subTest(rows=rows):
                result = mod.score_assessments(snap, rows)
                self.assertIsNone(result['news_business_impact_score'])
                self.assertEqual(result['status'], 'INCOMPLETE_CLASSIFICATION')

    def test_three_day_half_life_and_mixed_coverage(self):
        snap = mod.collect_news('TSLA', now=NOW, fetch=lambda _: [
            item(title='Company revenue declines', link='https://example.com/a', published='Wed, 21 Oct 2026 12:00:00 +0000'),
            item(title='Company raises revenue outlook', link='https://example.com/b', published='Sun, 18 Oct 2026 12:00:00 +0000')])
        a, b = snap['articles']
        rows = [assessment(snap, article_id=a['article_id'], direction=-1, evidence_quote=a['title']),
                assessment(snap, article_id=b['article_id'], direction=1, evidence_quote=b['title'])]
        result = mod.score_assessments(snap, rows)
        self.assertAlmostEqual(result['news_business_impact_score'], -33.33)
        self.assertEqual([r['recency_weight'] for r in result['assessed_articles']], [1., .5])

    def test_classification_failure_retains_sources(self):
        def fail(*_): raise ValueError('model unavailable')
        snap = snapshot()
        result = mod.analyze_news(snap, fail)
        self.assertEqual(result['articles'], snap['articles'])
        self.assertIsNone(result['analysis']['news_business_impact_score'])
        self.assertEqual(result['analysis']['status'], 'CLASSIFICATION_UNAVAILABLE')

    def test_model_request_has_no_tools_or_secrets_in_prompt_and_no_fallback(self):
        snap = snapshot()
        captured = []
        def network(request, **kwargs):
            captured.append(json.loads(request.data))
            raise TimeoutError()
        with patch.dict(mod.os.environ, {'BASE_URL': 'https://openrouter.ai/api/v1', 'API_KEY': 'test-secret',
                                         'MODEL': 'nvidia/nemotron-3-ultra-550b-a55b:free'}), patch.object(mod.urllib.request, 'urlopen', side_effect=network):
            with self.assertRaises(ValueError): mod.classify_news('TSLA', snap['articles'])
        self.assertEqual(len(captured), 1)
        self.assertNotIn('test-secret', json.dumps(captured))
        self.assertNotIn('tools', captured[0])
        self.assertTrue(captured[0]['model'].endswith(':free'))


class FinancialTests(unittest.TestCase):
    def tables(self):
        dates = pd.to_datetime(['2026-06-30', '2026-03-31', '2025-06-30'])
        income = pd.DataFrame([[120, 100, 80], [60, 45, 32], [24, 15, 8], [12, 10, 4]],
                              index=['TotalRevenue', 'GrossProfit', 'OperatingIncome', 'NetIncome'], columns=dates)
        cash = pd.DataFrame([[30, 20, 10], [-5, -4, -2]], index=['OperatingCashFlow', 'CapitalExpenditure'], columns=dates)
        balance = pd.DataFrame([[70, 60, 50], [20, 20, 20]], index=['CashAndCashEquivalents', 'TotalDebt'], columns=dates)
        return income, cash, balance

    def test_known_financial_values_and_units(self):
        result = mod.fundamental_snapshot('TEST', *self.tables(), currency='TWD', now=NOW)
        self.assertEqual(result['financial_currency'], 'TWD')
        self.assertEqual(result['indicators']['revenue_yoy_pct'], 50)
        self.assertEqual(result['indicators']['revenue_qoq_pct'], 20)
        self.assertEqual(result['latest_quarter']['gross_margin_pct'], 50)
        self.assertEqual(result['indicators']['gross_margin_yoy_change_pp'], 10)
        self.assertEqual(result['latest_quarter']['free_cash_flow'], 25)
        self.assertEqual(result['latest_quarter']['net_cash'], 50)
        self.assertFalse(result['historical_backtest_eligible'])
        self.assertIsNone(result['available_at_utc'])
        json.dumps(result, allow_nan=False)

    def test_missing_same_date_rows_and_comparisons_remain_null(self):
        income, cash, balance = self.tables()
        result = mod.fundamental_snapshot('TEST', income.iloc[:, :2], cash.iloc[:, 1:], balance.iloc[:, 1:], now=NOW)
        self.assertIsNone(result['latest_quarter']['free_cash_flow'])
        self.assertIsNone(result['latest_quarter']['net_cash'])
        self.assertIsNone(result['indicators']['revenue_yoy_pct'])
        self.assertEqual(result['status'], 'PARTIAL')

    def test_invalid_denominator_capex_and_nan(self):
        income, cash, balance = self.tables()
        income.iloc[0, 2] = 0
        cash.iloc[1, 0] = 5
        income.iloc[2, 0] = float('nan')
        result = mod.fundamental_snapshot('TEST', income, cash, balance, now=NOW)
        self.assertIsNone(result['indicators']['revenue_yoy_pct'])
        self.assertIsNone(result['latest_quarter']['free_cash_flow'])
        self.assertIsNone(result['latest_quarter']['operating_margin_pct'])
        json.dumps(result, allow_nan=False)

    def test_duplicate_income_and_future_periods_rejected(self):
        income, cash, balance = self.tables()
        with self.assertRaises(ValueError): mod.fundamental_snapshot('TEST', pd.concat([income, income]), cash, balance, now=NOW)
        with self.assertRaises(ValueError): mod.fundamental_snapshot('TEST', income, cash, balance, now=datetime(2020, 1, 1, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
