"""Durable FIFO, single-receiver lease, and append-only conversation recovery."""
import copy
from dataclasses import asdict
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import queue
import sqlite3
import threading
import signal


class ReceiverLease:
    def __init__(self, root):
        path = Path(root) / 'state' / 'receiver.lock'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('Another harness receiver owns this workspace; stop it before starting another.') from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class DurableQueue:
    def __init__(self, root, factory):
        self.path = Path(root) / 'state' / 'events.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.commit()
        self.lock = threading.RLock()
        self.factory, self.active, self.closed = factory, None, False

    def put(self, event, key=None):
        payload = asdict(event)
        payload['time'] = event.time.isoformat()
        with self.lock, self.db:
            if event.kind == 'schedule':
                prefix = 'schedule:' + event.source + ':'
                previous = self.db.execute('SELECT payload FROM events WHERE substr(identity,1,?)=?',
                                           (len(prefix), prefix)).fetchall()
                if any(0 <= event.time.timestamp() - datetime.fromisoformat(json.loads(row[0])['time']).timestamp() < 3600
                       for row in previous):
                    return False
            result = self.db.execute('INSERT OR IGNORE INTO events(identity,payload,status) VALUES(?,?,?)',
                                     (key or event.identity, json.dumps(payload), 'ready'))
            return result.rowcount == 1

    def _decode(self, payload):
        data = json.loads(payload)
        data['time'] = datetime.fromisoformat(data['time']).astimezone()
        return self.factory(**data)

    def get_nowait(self):
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.active is not None:
                raise RuntimeError('Only one durable event consumer is permitted.')
            row = self.db.execute("SELECT seq,payload FROM events WHERE status='ready' ORDER BY seq LIMIT 1").fetchone()
            if row is None:
                raise queue.Empty
            self.db.execute("UPDATE events SET status='active' WHERE seq=?", (row[0],))
            self.active = row[0]
            return self._decode(row[1])

    def task_done(self, outcome='done'):
        with self.lock, self.db:
            if self.active is None:
                raise ValueError('No active durable event.')
            self.db.execute('UPDATE events SET status=? WHERE seq=?', (outcome, self.active))
            self.active = None

    def recover(self, delivered=()):
        interrupted = []
        with self.lock, self.db:
            rows = self.db.execute("SELECT seq,payload FROM events WHERE status='active' ORDER BY seq").fetchall()
            for seq, payload in rows:
                event = self._decode(payload)
                status = 'done' if event.identity in delivered else 'interrupted'
                self.db.execute('UPDATE events SET status=? WHERE seq=?', (status, seq))
                if status == 'interrupted':
                    interrupted.append(event)
        return interrupted

    def qsize(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM events WHERE status='ready'").fetchone()[0]

    def empty(self):
        return self.qsize() == 0

    def set_paused(self, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('paused',?)", (json.dumps(bool(value)),))

    def paused(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE name='paused'").fetchone()
            return json.loads(row[0]) if row else False

    def close(self):
        with self.lock:
            if not self.closed:
                self.db.close()
                self.closed = True


def read_journal(path):
    """Accept an interrupted final write, preserving every byte in the journal."""
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b''
    rows, invalid, offset = [], [], 0
    for line in raw.splitlines(keepends=True):
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError()
            rows.append(value)
        except (ValueError, UnicodeError):
            invalid.append(offset)
        offset += len(line)
    ignored = {r['data']['ignored_offset'] for r in rows if r.get('event') == 'recovery_tail'}
    trailing = len(raw) - len(raw.splitlines(keepends=True)[-1]) if raw else None
    partial = trailing if raw and not raw.endswith(b'\n') and trailing in invalid else None
    if any(item not in ignored and item != partial for item in invalid):
        raise ValueError('Malformed interior journal record; restore logs/main.jsonl before starting.')
    return rows, bool(raw and not raw.endswith(b'\n')), partial


def restore_context(agent):
    rows, separator, ignored = read_journal(agent.log_path)
    messages, delivered = [], set()
    last_count, baseline, last_wake = None, (), {}
    for row in rows:
        event, data = row.get('event'), row.get('data', {})
        if event == 'message':
            messages.append(copy.deepcopy(data))
        elif event in ('compaction', 'compaction_aborted', 'recovery_checkpoint', 'context_checkpoint'):
            messages = copy.deepcopy(data['messages'])
            last_count, baseline = None, ()
            if event == 'compaction':
                agent.pending_compaction = {'path': data['path'], 'before': data['before_input_tokens']}
        elif event == 'prompt_refresh' and messages:
            messages[0] = copy.deepcopy(data['message'])
        elif event == 'usage':
            count = data.get('prompt_tokens')
            if type(count) in (int, float) and count >= 0:
                last_count = count
                baseline = tuple(json.dumps(m, ensure_ascii=True, sort_keys=True) for m in messages)
        elif event == 'compaction_measurement':
            agent.pending_compaction = None
        elif event == 'final_delivered':
            delivered.add(data['identity'])
        elif event == 'wake':
            last_wake = {key: data.get(key, '') for key in ('kind', 'source')}
    if separator:
        fd = os.open(agent.log_path, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'\n')
            stream.flush()
            os.fsync(stream.fileno())
    if ignored is not None:
        agent._log('recovery_tail', {'ignored_offset': ignored})
    pending, restored, repaired = {}, [], 0
    def interrupt():
        nonlocal repaired
        for identity in list(pending):
            restored.append({'role': 'tool', 'tool_call_id': identity,
                             'content': 'Interrupted by a restart; not re-run. Check its effects before trying again.'})
            repaired += 1
        pending.clear()
    for message in messages:
        if message.get('role') != 'tool' and pending:
            interrupt()
        restored.append(message)
        if message.get('role') == 'assistant':
            pending = {call['id']: call for call in message.get('tool_calls', [])}
        elif message.get('role') == 'tool':
            identity = message.get('tool_call_id')
            if identity not in pending:
                raise ValueError('Orphaned tool result in restored journal.')
            pending.pop(identity)
    interrupt()
    if restored:
        if restored[0].get('role') != 'system':
            raise ValueError('Restored conversation must begin with a system prompt.')
        agent.messages = restored
        agent._initial_log_written = True
        agent.last_input_tokens, agent.last_input_messages = last_count, baseline
        agent._append({'role': 'user', 'content': '[Harness restart] Conversation restored. '
                       'Previous commands, sends and approvals are not automatically retried; '
                       'background children did not survive. Check effects before resuming interrupted work.'})
        agent._log('recovery_checkpoint', {'messages': agent.messages, 'repaired_tools': repaired})
    # A SIGKILL does not run shell cleanup. Only kill a provably unchanged process group.
    from agent import process_fingerprint
    outstanding, killed, process_records = {}, 0, []
    journal_paths = [agent.log_path, *(agent.log_path.parent / 'children').glob('*/*.jsonl')]
    for path in journal_paths:
        records, _, _ = read_journal(path)
        process_records.extend(r for r in records if r.get('event') in ('process_start', 'process_end', 'orphan_command_killed'))
    for record in sorted(process_records, key=lambda r: r['time']):
        data = record['data']
        if record['event'] == 'process_start':
            outstanding[data['pid']] = data
        else:
            outstanding.pop(data['pid'], None)
    for pid, record in outstanding.items():
        fingerprint = record.get('fingerprint')
        if fingerprint and fingerprint == process_fingerprint(pid) and fingerprint.split()[-1] == str(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
                agent._log('orphan_command_killed', {'pid': pid})
                killed += 1
            except ProcessLookupError:
                pass
    return {'resumed': bool(restored), 'repaired_tools': repaired, 'delivered': delivered,
            'orphan_groups_killed': killed, 'last_wake': last_wake}
