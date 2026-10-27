import importlib.util
from pathlib import Path
from datetime import datetime, date, timezone
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('events_under_test',ROOT/'plugins/wenyuzhang2001-equity-research/company_events.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NOW=datetime(2026,10,21,20,30,tzinfo=timezone.utc)

class EventTests(unittest.TestCase):
 def test_dividend_midnight_epoch_preserves_civil_date(self):
  stamp=datetime(2026,11,3,tzinfo=timezone.utc).timestamp()
  r=m.event_snapshot('TEST',{}, {'exDividendDate':stamp},now=NOW)
  self.assertEqual(r['dividends']['ex_dividend']['date'],'2026-11-03')
  self.assertEqual(r['dividends']['ex_dividend']['calendar_days_from_today'],13)
  self.assertIn('EX_DIVIDEND_DATE_WITHIN_HORIZON',r['event_flags'])

 def test_past_dividend_and_missing_record_not_upcoming(self):
  r=m.event_snapshot('TEST',{'Ex-Dividend Date':date(2026,9,9),'Dividend Date':date(2026,9,30)}, {},now=NOW)
  self.assertEqual(r['dividends']['payment']['relation'],'PAST')
  self.assertFalse(r['dividends']['payment']['within_horizon'])
  self.assertIsNone(r['dividends']['record']['date'])
  self.assertIsNone(r['dividends']['declaration']['date'])
  self.assertIsNone(r['dividends']['next_dividend_announced'])

 def test_earnings_window_is_estimate_not_two_releases(self):
  r=m.event_snapshot('TEST',{'Earnings Date':[date(2026,10,25),date(2026,10,27)]},{'isEarningsDateEstimate':True},now=NOW)
  self.assertTrue(r['earnings']['is_date_window'])
  self.assertEqual(r['earnings']['confirmation_status'],'VENDOR_ESTIMATE')
  self.assertFalse(r['earnings']['issuer_verified'])
  self.assertIn('EARNINGS_DATE_OR_WINDOW_WITHIN_HORIZON',r['event_flags'])

 def test_actual_time_and_civil_date_conflict(self):
  stamp=datetime(2026,10,21,20,tzinfo=timezone.utc).timestamp()
  r=m.event_snapshot('TEST',{'Earnings Date':[date(2026,10,21)]},{'earningsTimestamp':stamp,'isEarningsDateEstimate':False},now=NOW)
  self.assertEqual(r['earnings']['vendor_time_session'],'AFTER_MARKET_CLOSE')
  self.assertTrue(r['earnings']['vendor_time_has_passed'])
  self.assertFalse(r['earnings']['issuer_verified'])
  r=m.event_snapshot('TEST',{'Earnings Date':[date(2026,10,22)]},{'earningsTimestamp':stamp},now=NOW)
  self.assertTrue(r['earnings']['date_time_conflict'])

 def test_missing_dates_are_not_no_dividend_and_invalid_horizon(self):
  r=m.event_snapshot('TEST',{}, {},now=NOW)
  self.assertEqual(r['earnings']['confirmation_status'],'UNAVAILABLE')
  self.assertEqual(r['dividends']['status'],'NO_DATES_RETURNED_NOT_PROOF_OF_NO_DIVIDEND')
  self.assertIsNone(m.date_label(float('nan')))
  self.assertIsNone(m.date_label(True))
  with self.assertRaises(ValueError):m.event_snapshot('TEST',{}, {},horizon_days=0,now=NOW)

if __name__=='__main__':unittest.main()
