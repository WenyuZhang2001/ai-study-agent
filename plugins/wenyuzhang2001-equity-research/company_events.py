"""Current vendor event calendar; date-only labels are not UTC instants."""
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo
import yfinance as yf

NY = ZoneInfo('America/New_York')


def date_label(value):
    """Yahoo dividend epochs label a civil date at UTC midnight: do not shift to NY."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            if not math.isfinite(value) or value <= 0:
                return None
            return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
        if isinstance(value, (date, datetime)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def event_snapshot(symbol, calendar, info, horizon_days=14, now=None, errors=None):
    if not 1 <= horizon_days <= 90:
        raise ValueError('horizon_days must be 1–90 calendar days.')
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    calendar = calendar if isinstance(calendar, dict) else {}
    info = info if isinstance(info, dict) else {}
    def labeled(value, source_field):
        day = date_label(value)
        delta = (date.fromisoformat(day) - today).days if day else None
        return {'date': day, 'source_field': source_field if day else None,
                'calendar_days_from_today': delta,
                'relation': 'UNKNOWN' if delta is None else ('PAST' if delta < 0 else ('TODAY' if delta == 0 else 'FUTURE')),
                'within_horizon': delta is not None and 0 <= delta <= horizon_days}
    raw_dates = calendar.get('Earnings Date', [])
    if not isinstance(raw_dates, list):
        raw_dates = [raw_dates]
    dates = sorted({date_label(value) for value in raw_dates if date_label(value)})
    timestamp = info.get('earningsTimestamp')
    instant = None
    try:
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and timestamp > 0:
            instant = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(NY)
    except (ValueError, OSError, OverflowError):
        pass
    if not dates and instant:
        dates = [instant.date().isoformat()]
    estimate = info.get('isEarningsDateEstimate')
    status = ('VENDOR_ESTIMATE' if estimate is True else
              ('VENDOR_MARKED_NON_ESTIMATED_NOT_ISSUER_VERIFIED' if estimate is False else 'CONFIRMATION_UNKNOWN')) if dates else 'UNAVAILABLE'
    timing = None
    if instant:
        minutes = instant.hour * 60 + instant.minute
        timing = 'BEFORE_MARKET_OPEN' if minutes < 570 else ('AFTER_MARKET_CLOSE' if minutes >= 960 else 'DURING_REGULAR_HOURS')
    conflict = bool(instant and dates and instant.date().isoformat() not in dates)
    earnings = {'date_candidates': [labeled(day, 'calendar.Earnings Date') for day in dates],
                'is_date_window': len(dates) > 1, 'confirmation_status': status,
                'issuer_verified': False, 'vendor_time_new_york': instant.isoformat() if instant else None,
                'vendor_time_session': timing, 'vendor_time_has_passed': instant < now if instant else None,
                'date_time_conflict': conflict,
                'note': 'A date window is not multiple earnings releases; date/time and issuer confirmation need cross-checking.'}
    ex_date = labeled(calendar.get('Ex-Dividend Date') or info.get('exDividendDate'), 'calendar.Ex-Dividend Date / info.exDividendDate')
    payment = labeled(calendar.get('Dividend Date') or info.get('dividendDate'), 'calendar.Dividend Date / info.dividendDate')
    dividends = {'ex_dividend': ex_date, 'payment': payment,
                 'record': labeled(calendar.get('Record Date') or info.get('dividendRecordDate'), 'calendar.Record Date / info.dividendRecordDate'),
                 'declaration': labeled(calendar.get('Declaration Date') or info.get('dividendDeclarationDate'), 'calendar.Declaration Date / info.dividendDeclarationDate'),
                 'status': 'VENDOR_DATES_REPORTED' if ex_date['date'] or payment['date'] else 'NO_DATES_RETURNED_NOT_PROOF_OF_NO_DIVIDEND',
                 'next_dividend_announced': None,
                 'note': 'Past dates are last-known dates, not the next dividend. Missing dates mean unavailable, not no dividend. Do not infer payment/record/declaration from ex-date or a past payment schedule.'}
    risks = []
    if any(row['within_horizon'] for row in earnings['date_candidates']):
        risks.append('EARNINGS_DATE_OR_WINDOW_WITHIN_HORIZON')
    if ex_date['within_horizon']:
        risks.append('EX_DIVIDEND_DATE_WITHIN_HORIZON')
    if payment['within_horizon']:
        risks.append('PAYMENT_DATE_WITHIN_HORIZON')
    return {'symbol': symbol, 'retrieved_utc': now.astimezone(timezone.utc).isoformat(),
            'today_new_york': today.isoformat(), 'date_timezone': 'America/New_York; date-only dividend epochs retain UTC civil-date labels',
            'horizon_calendar_days': horizon_days, 'earnings': earnings, 'dividends': dividends,
            'event_flags': risks, 'source': 'Yahoo Finance via yfinance; vendor calendar, not issuer verification',
            'source_urls': [f'https://finance.yahoo.com/quote/{symbol}/calendar/', f'https://finance.yahoo.com/quote/{symbol}/key-statistics/'],
            'source_errors': errors or [], 'historical_backtest_eligible': False,
            'limitations': ['Current snapshot; schedules may change. No historical availability or issuer-confirmation audit.',
                           'Missing announcement/record/payment dates stay unknown; no dates inferred from settlement rules.',
                           'Event flags use calendar days, not exchange trading sessions; no automatic trading or schedule change.',
                           'An earnings date is a release date, not the quarter end or necessarily an SEC filing date.']}


def get_events(symbol, horizon_days=14, ticker=None, info=None):
    if not 1 <= horizon_days <= 90:
        raise ValueError('horizon_days must be 1–90 calendar days.')
    ticker = ticker or yf.Ticker(symbol)
    errors = []
    try:
        calendar = ticker.calendar
    except Exception:
        calendar = {}
        errors.append('Vendor event calendar unavailable.')
    if info is None:
        try:
            info = ticker.get_info()
        except Exception:
            info = {}
            errors.append('Vendor event metadata unavailable.')
    return event_snapshot(symbol, calendar, info, horizon_days, errors=errors)
