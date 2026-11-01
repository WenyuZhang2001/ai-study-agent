"""Persistent per-address send-attempt caps. No SMTP or credentials in this module."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tomllib
from zoneinfo import ZoneInfo


def automatic(message):
    return (any(str(value).strip().lower() != 'no' for value in message.get_all('Auto-Submitted', []))
            or 'List-Id' in message)


def reserve_send(recipient, root=None, now=None):
    root = Path(root or os.environ.get('AGENT_CONTROL_ROOT', Path(__file__).resolve().parents[2]))
    try:
        config = tomllib.loads((root / 'limits.toml').read_text()) if (root / 'limits.toml').exists() else {}
        cap = config.get('replies_per_sender', 2)
        if type(cap) is not int or cap < 0:
            raise ValueError()
        zone = ZoneInfo(config.get('timezone', 'America/Los_Angeles'))
    except (ValueError, TypeError, KeyError):
        raise ValueError('Invalid mail reply limits; sending refused.') from None
    day = (now or datetime.now(timezone.utc)).astimezone(zone).date().isoformat()
    state = root / 'state'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Shared across threads and plugin processes. A reserved attempt is never retried/refunded.
    fd = os.open(state / 'mail-replies.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = state / 'mail-replies.json'
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(data, dict) or any(not isinstance(counts, dict) or
                    any(type(v) is not int or v < 0 for v in counts.values()) for counts in data.values()):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Invalid mail-replies state; sending refused.') from None
        key = hashlib.sha256(recipient.strip().lower().encode()).hexdigest()
        counts = data.setdefault(day, {})
        if counts.get(key, 0) >= cap:
            raise ValueError(f'replies_per_sender reached ({cap} send attempts today); no email sent.')
        counts[key] = counts.get(key, 0) + 1
        temporary = path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
