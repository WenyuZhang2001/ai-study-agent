"""File-backed schedules. The clock does no model work; due jobs join the queue."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


SCHEDULE = {'type': 'function', 'function': {
    'name': 'schedule',
    'description': 'Manage schedule.toml in the always-on harness. list is read-only; add and remove require owner approval. '
                   'At most 10 jobs; each job runs at most hourly. cron has five numeric fields in the configured local timezone; '
                   'every accepts whole hours/days such as 2h or 1d. Missed or paused occurrences are skipped, not caught up.',
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['list', 'add', 'remove']},
        'name': {'type': 'string'}, 'cron': {'type': 'string'},
        'every': {'type': 'string'}, 'task': {'type': 'string'}},
        'required': ['action'], 'additionalProperties': False}}}


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass(frozen=True)
class Job:
    name: str
    task: str
    cron: str | None = None
    every: str | None = None

    @property
    def seconds(self):
        return int(self.every[:-1]) * (3600 if self.every[-1] == 'h' else 86400)

    def as_dict(self):
        return {'name': self.name, 'task': self.task,
                **({'cron': self.cron} if self.cron else {'every': self.every})}


class Scheduler:
    def __init__(self, root, clock=None):
        self.root = Path(root)
        self.path = self.root / 'schedule.toml'
        self.state_path = self.root / 'state' / 'schedule.json'
        self.lock = threading.RLock()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = {}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text())
                if not isinstance(self.state, dict):
                    raise ValueError()
                for record in self.state.values():
                    if (not isinstance(record, dict) or not isinstance(record.get('timing'), str) or
                            any(not isinstance(record.get(k, 0), (int, float)) or
                                not math.isfinite(record.get(k, 0)) or record.get(k, 0) < 0 or
                                isinstance(record.get(k), bool) for k in ['last_slot', 'last_fired', 'next_due'])):
                        raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('Invalid state/schedule.json; restore it before starting.') from None

    @staticmethod
    def job(data):
        if not isinstance(data, dict) or set(data) - {'name', 'task', 'cron', 'every'}:
            raise ValueError('Each job requires name, task, and exactly one of cron/every.')
        if not isinstance(data.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', data['name']):
            raise ValueError('Job name must be 1–64 letters, digits, underscores or hyphens.')
        if not isinstance(data.get('task'), str) or not data['task'].strip() or len(data['task']) > 8000:
            raise ValueError('Job task must be nonempty and at most 8000 characters.')
        if ('cron' in data) == ('every' in data):
            raise ValueError('Specify exactly one of cron or every.')
        if 'every' in data:
            if not isinstance(data['every'], str) or not re.fullmatch(r'[1-9][0-9]{0,3}[hd]', data['every']):
                raise ValueError('every must be whole hours/days, at least 1h (for example 2h or 1d).')
        else:
            expr = data['cron']
            if (not isinstance(expr, str) or len(expr) > 200 or len(expr.split()) != 5 or
                    not re.fullmatch(r'[0-9*/ ,\-]+', expr) or not croniter.is_valid(expr)):
                raise ValueError('cron requires five valid numeric fields: minute hour day month weekday.')
            minutes = croniter.expand(expr)[0][0]
            if len(minutes) != 1 or not isinstance(minutes[0], int):
                raise ValueError('Jobs may not run more often than hourly; choose one minute per hour.')
            try:
                croniter(expr, datetime(2026, 1, 1), max_years_between_matches=8).get_next(datetime)
            except ValueError:
                raise ValueError('cron has no matching date in the next eight years.') from None
        return Job(**data)

    def _load(self):
        try:
            data = tomllib.loads(self.path.read_text()) if self.path.exists() else {}
        except tomllib.TOMLDecodeError:
            raise ValueError('Invalid schedule.toml syntax; no jobs were changed or run.') from None
        if set(data) - {'timezone', 'job'}:
            raise ValueError('schedule.toml only accepts timezone and [[job]] entries.')
        try:
            zone = ZoneInfo(data.get('timezone', 'America/Los_Angeles'))
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            raise ValueError('schedule.toml requires a valid IANA timezone.') from None
        entries = data.get('job', [])
        if not isinstance(entries, list) or len(entries) > 10:
            raise ValueError('At most 10 scheduled jobs are allowed.')
        jobs = [self.job(entry) for entry in entries]
        if len({job.name for job in jobs}) != len(jobs):
            raise ValueError('Job names must be unique; remove before replacing a job.')
        return zone, jobs

    def dispatch(self, action, **args):
        with self.lock:
            zone, jobs = self._load()
            if action == 'list' and not args:
                return {'timezone': str(zone), 'jobs': [job.as_dict() for job in jobs]}
            if action == 'add':
                job = self.job(args)
                if len(jobs) >= 10:
                    raise ValueError('At most 10 scheduled jobs are allowed.')
                if any(j.name == job.name for j in jobs):
                    raise ValueError('Job already exists; remove it before adding a replacement.')
                jobs.append(job)
            elif action == 'remove' and set(args) == {'name'}:
                if not any(job.name == args['name'] for job in jobs):
                    raise ValueError('Unknown scheduled job name.')
                jobs = [job for job in jobs if job.name != args['name']]
            else:
                raise ValueError('Use list(), add(name, task, cron OR every), or remove(name).')
            # JSON-quoted basic strings are TOML-compatible and protect newlines/quotes.
            quote = lambda s: json.dumps(s, ensure_ascii=False)
            text = '# Managed schedule; edits reload on the next minute tick.\n'
            text += 'timezone = ' + quote(str(zone)) + '\n'
            for job in jobs:
                text += '\n[[job]]\n' + '\n'.join(k + ' = ' + quote(v) for k, v in job.as_dict().items()) + '\n'
            atomic_write(self.path, text)
            return {'action': action, 'name': args['name'], 'jobs': len(jobs)}

    def _now(self, now):
        now = self.clock() if now is None else now
        if now.tzinfo is None:
            raise ValueError('Scheduler requires a timezone-aware clock.')
        return now

    def due(self, *, paused=False, now=None, enqueue=None):
        """Consume this minute only. Save dedup state before returning any event."""
        with self.lock:
            now = self._now(now)
            stamp = now.timestamp()
            slot = int(stamp // 60) * 60
            zone, jobs = self._load()
            local = now.astimezone(zone).replace(second=0, microsecond=0)
            state = {name: record.copy() for name, record in self.state.items()}
            due = []
            for job in jobs:
                timing = hashlib.sha256(json.dumps([str(zone), job.cron, job.every]).encode()).hexdigest()
                record = state.setdefault(job.name, {})
                if record.get('timing') != timing:
                    record.update(timing=timing, next_due=math.ceil((stamp + job.seconds) / 60) * 60 if job.every else 0)
                if record.get('last_slot', -1) >= slot:
                    continue
                if job.cron:
                    ready = croniter.match(job.cron, local)
                else:
                    target = record['next_due']
                    ready = slot <= target <= stamp
                    if target <= stamp:
                        record['next_due'] = target + (int((stamp - target) // job.seconds) + 1) * job.seconds
                if ready:
                    record['last_slot'] = slot
                    if not paused and slot - record.get('last_fired', 0) >= 3600:
                        record['last_fired'] = slot
                        due.append((job, local))
            if enqueue:
                for job, stamp in due:
                    enqueue(job, stamp)  # Durable queue deduplication precedes cursor advancement.
            if state != self.state:
                atomic_write(self.state_path, json.dumps(state, indent=2))
                self.state = state
            return due

    def next_job(self, now=None):
        with self.lock:
            now = self._now(now)
            zone, jobs = self._load()
            next_jobs = []
            for job in jobs:
                record = self.state.get(job.name, {})
                timing = hashlib.sha256(json.dumps([str(zone), job.cron, job.every]).encode()).hexdigest()
                if job.cron:
                    iterator = croniter(job.cron, now.astimezone(zone))
                    due = iterator.get_next(datetime)
                    while due.timestamp() < record.get('last_fired', 0) + 3600:
                        due = iterator.get_next(datetime)
                else:
                    target = (record['next_due'] if record.get('timing') == timing else
                              math.ceil((now.timestamp() + job.seconds) / 60) * 60)
                    if target <= now.timestamp():
                        target += (int((now.timestamp() - target) // job.seconds) + 1) * job.seconds
                    due = datetime.fromtimestamp(target, zone)
                next_jobs.append((due.timestamp(), job.name, due))
            if not next_jobs:
                return '无'
            _, name, due = min(next_jobs)
            return f'{name} · {due:%Y-%m-%d %H:%M %Z}'
