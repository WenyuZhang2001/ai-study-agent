"""Shared, persistent token accounting for the always-on main agent and children."""
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
import tomllib
import uuid
from zoneinfo import ZoneInfo

from scheduling import atomic_write


DEFAULTS = {'timezone': 'America/Los_Angeles', 'turns_per_wake': 8,
            'minutes_per_wake': 5, 'tokens_per_day': 100000, 'replies_per_sender': 2}


def load_limits(root):
    path = Path(root) / 'limits.toml'
    try:
        data = tomllib.loads(path.read_text()) if path.exists() else {}
        if set(data) - set(DEFAULTS):
            raise ValueError()
        result = {**DEFAULTS, **data}
        ZoneInfo(result['timezone'])
        for key in ('turns_per_wake', 'tokens_per_day', 'replies_per_sender'):
            if type(result[key]) is not int or result[key] < (1 if key == 'turns_per_wake' else 0):
                raise ValueError()
        minutes = result['minutes_per_wake']
        if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0:
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError):
        raise ValueError('Invalid limits.toml; limits must be finite, nonnegative, and turns/minutes positive.') from None


def exceeded(message):
    # Lazy import avoids a dependency cycle with the inner loop.
    from agent import AgentLimitError
    raise AgentLimitError(message)


class Budget:
    def __init__(self, root, clock=None, monotonic=None):
        self.root = Path(root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.lock = threading.RLock()
        self.path = self.root / 'state' / 'usage.json'
        self.config = load_limits(self.root)
        try:
            self.days = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(self.days, dict):
                raise ValueError()
            for day, entry in self.days.items():
                datetime.strptime(day, '%Y-%m-%d')
                if (not isinstance(entry, dict) or not isinstance(entry['pending'], dict) or
                        type(entry['unknown_cost']) is not bool):
                    raise ValueError()
                for number in [entry[k] for k in ('tokens', 'dollars', 'calls', 'wakes')] + list(entry['pending'].values()):
                    if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
                        raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ValueError('Invalid state/usage.json; restore it before starting.') from None

    def _day(self):
        return self.clock().astimezone(ZoneInfo(self.config['timezone'])).date().isoformat()

    @staticmethod
    def _entry(days, day):
        return days.setdefault(day, {'tokens': 0, 'dollars': 0., 'calls': 0, 'wakes': 0,
                                     'unknown_cost': False, 'pending': {}})

    def _save(self, days):
        atomic_write(self.path, json.dumps(days, indent=2))
        self.days = days

    def start_wake(self, automatic_email=False):
        with self.lock:
            self.config = load_limits(self.root)
            days = copy.deepcopy(self.days)
            self._entry(days, self._day())['wakes'] += 1
            self._save(days)
            return WakeBudget(self, dict(self.config), self.monotonic() + self.config['minutes_per_wake'] * 60,
                              automatic_email=automatic_email)

    def status(self):
        with self.lock:
            entry = self.days.get(self._day(), {})
            return {**{k: entry.get(k, 0) for k in ('tokens', 'dollars', 'calls', 'wakes')},
                    'unknown_cost': entry.get('unknown_cost', False),
                    'reserved': sum(entry.get('pending', {}).values()),
                    'limit': self.config['tokens_per_day']}


@dataclass
class WakeBudget:
    budget: Budget
    limits: dict
    deadline: float
    calls: int = 0
    automatic_email: bool = False

    def check(self):
        if self.budget.monotonic() >= self.deadline:
            exceeded(f"minutes_per_wake reached ({self.limits['minutes_per_wake']} minutes).")

    def remaining(self):
        self.check()
        return self.deadline - self.budget.monotonic()

    def reserve(self, messages, tools, output_tokens, input_estimate=None):
        with self.budget.lock:
            self.check()
            if self.calls >= self.limits['turns_per_wake']:
                exceeded(f"turns_per_wake reached ({self.limits['turns_per_wake']} shared model calls).")
            # Conservative byte estimate plus output allowance and framing overhead.
            estimate = len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=True).encode()) + output_tokens + 1024
            if input_estimate is not None:
                if type(input_estimate) not in (int, float) or not math.isfinite(input_estimate) or input_estimate < 0:
                    raise ValueError('Invalid input-token reservation estimate.')
                estimate = math.ceil(input_estimate) + output_tokens + 1024
            days = copy.deepcopy(self.budget.days)
            day = self.budget._day()
            entry = self.budget._entry(days, day)
            if entry['tokens'] + sum(entry['pending'].values()) + estimate > self.limits['tokens_per_day']:
                exceeded(f"tokens_per_day reached or insufficient remaining allowance ({self.limits['tokens_per_day']} tokens; next-call reserve {estimate}).")
            identity = uuid.uuid4().hex
            entry['pending'][identity] = estimate
            entry['calls'] += 1
            self.budget._save(days)
            self.calls += 1
            return day, identity

    def settle(self, ticket, usage):
        with self.budget.lock:
            day, identity = ticket
            days = copy.deepcopy(self.budget.days)
            entry = days[day]
            reserve = entry['pending'].pop(identity)
            usage = usage or {}
            tokens = usage.get('total_tokens')
            valid = lambda n: type(n) in (int, float) and math.isfinite(n) and n >= 0
            if tokens is None and valid(usage.get('prompt_tokens')) and valid(usage.get('completion_tokens')):
                tokens = usage['prompt_tokens'] + usage['completion_tokens']
            entry['tokens'] += tokens if valid(tokens) else reserve
            cost = usage.get('cost')
            if valid(cost):
                entry['dollars'] += cost
            else:
                entry['unknown_cost'] = True
            self.budget._save(days)

    def check_mail(self):
        self.check()
        if self.automatic_email:
            raise ValueError('Email send refused: this wake originated from an automatic/list message.')
