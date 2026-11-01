"""At-most-once SMTP attempts, with Sent reconciliation after an uncertain outcome."""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re


def canonical_body(body):
    return body.replace('\r\n', '\n').rstrip('\n')


class Outbox:
    def __init__(self, root=None):
        root = Path(root or os.environ.get('AGENT_CONTROL_ROOT', Path(__file__).resolve().parents[2]))
        self.path = root / 'state' / 'outbox.jsonl'

    def append(self, record):
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(record) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def records(self):
        raw = self.path.read_bytes() if self.path.exists() else b''
        rows, bad, offset = [], [], 0
        for line in raw.splitlines(keepends=True):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError()
                rows.append(value)
            except (ValueError, UnicodeError):
                bad.append(offset)
            offset += len(line)
        ignored = {r.get('offset') for r in rows if r.get('state') == 'tail_ignored'}
        for record in rows:
            if record.get('state') == 'tail_ignored':
                if type(record.get('offset')) is not int:
                    raise ValueError('Invalid outbox repair record; sending refused.')
                continue
            if (record.get('state') not in ('intent', 'done') or not isinstance(record.get('key'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', record['key'])
                    or record.get('message_id') != '<' + record['key'] + '@ai-study-agent.local>'):
                raise ValueError('Invalid outbox record; sending refused.')
            if record['state'] == 'intent':
                try:
                    if (not isinstance(record['to'], str) or not isinstance(record['subject'], str)
                            or datetime.fromisoformat(record['time']).tzinfo is None):
                        raise ValueError()
                except (KeyError, TypeError, ValueError):
                    raise ValueError('Invalid outbox intent; sending refused.') from None
        tail = len(raw) - len(raw.splitlines(keepends=True)[-1]) if raw else None
        if any(position not in ignored and not (position == tail and not raw.endswith(b'\n')) for position in bad):
            raise ValueError('Corrupt interior outbox record; sending refused.')
        if raw and not raw.endswith(b'\n'):
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'\n'); stream.flush(); os.fsync(stream.fileno())
            if tail in bad:
                self.append({'state': 'tail_ignored', 'offset': tail})
        return rows

    def send_once(self, sender, to, subject, body, *, deliver, reconcile, before_send, after_send=None):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = hashlib.sha256(json.dumps([sender.lower(), to.lower(), subject, canonical_body(body)],
                                        ensure_ascii=False).encode()).hexdigest()
        message_id = f'<{key}@ai-study-agent.local>'
        fd = os.open(self.path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            history = [r for r in self.records() if r.get('key') == key]
            if any(r.get('state') == 'done' for r in history):
                return {'status': 'already_sent', 'message_id': message_id}
            intents = [r for r in history if r.get('state') == 'intent']
            if intents:
                if reconcile(intents[-1], body):
                    self.append({'state': 'done', 'key': key, 'message_id': message_id, 'reconciled': True})
                    return {'status': 'already_sent', 'message_id': message_id, 'reconciled': True}
                # A missing Sent copy is not proof of no delivery (indexing/network delays).
                raise ValueError('Previous delivery is uncertain; Sent did not confirm it. Not resent. Inspect Sent before any new sending decision.')
            before_send()
            intent = {'state': 'intent', 'key': key, 'message_id': message_id, 'to': to,
                      'subject': subject, 'time': datetime.now(timezone.utc).isoformat()}
            self.append(intent)  # Must be flushed before SMTP is allowed.
            deliver(message_id)
            if after_send:
                after_send(intent)  # Fault-injection hook is Python-only, never a model argument.
            self.append({'state': 'done', 'key': key, 'message_id': message_id})
            return {'status': 'sent', 'message_id': message_id}


def reconcile_sent(client, intent, body):
    """Locate Gmail's Sent mailbox; confirm Message-ID or exact fallback content/time."""
    status, listing = client.list()
    folders = []
    if status == 'OK':
        for row in listing or []:
            if isinstance(row, bytes):
                match = re.match(rb'\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', row)
                if match and b'\\Sent' in match[1].split():
                    folders.append(match[2].decode('ascii'))
    folders += ['"[Gmail]/Sent Mail"', '"[Google Mail]/Sent Mail"']
    for folder in dict.fromkeys(folders):
        status, _ = client.select(folder, readonly=True)
        if status == 'OK':
            break
    else:
        raise ValueError('Cannot inspect Sent; delivery remains uncertain and will not be retried.')
    status, data = client.uid('search', None, 'HEADER', 'Message-ID', '"' + intent['message_id'] + '"')
    if status != 'OK':
        raise ValueError('Sent Message-ID search failed; not resent.')
    if data and data[0] and data[0].split():
        return True
    quote = lambda text: '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    since = datetime.fromisoformat(intent['time']).strftime('%d-%b-%Y')
    status, data = client.uid('search', 'CHARSET', 'UTF-8', 'SINCE', since, 'HEADER', 'To', quote(intent['to']),
                              'HEADER', 'Subject', quote(intent['subject']).encode('utf-8'))
    if status != 'OK':
        raise ValueError('Sent fallback search failed; not resent.')
    ids = (data[0] or b'').split() if data else []
    if len(ids) > 200:
        raise ValueError('Too many Sent candidates to reconcile safely; not resent.')
    for uid in ids:
        status, fetched = client.uid('fetch', uid, '(BODY.PEEK[]<0.262144>)')
        if status != 'OK':
            raise ValueError('Could not inspect Sent candidate; not resent.')
        raw = b''.join(part[1] for part in fetched if isinstance(part, tuple))
        if not raw or len(raw) >= 262144:
            continue
        message = BytesParser(policy=policy.default).parsebytes(raw)
        try:
            date = parsedate_to_datetime(str(message['Date']))
            text = message.get_body(preferencelist=('plain',))
            if (date is not None and date.tzinfo is not None and
                    date.timestamp() >= datetime.fromisoformat(intent['time']).timestamp() - 300 and
                    parseaddr(str(message['To']))[1].lower() == intent['to'].lower() and
                    str(message['Subject']) == intent['subject'] and text is not None and
                    canonical_body(text.get_content()) == canonical_body(body)):
                return True
        except (ValueError, TypeError, LookupError):
            continue
    return False
