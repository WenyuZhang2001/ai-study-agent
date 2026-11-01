"""One reviewed self-email, with a deliberate pause after SMTP and before done.

prepare is local only. send --send-to-self needs an explicit owner request/approval.
The harness crash/restart test kills the waiting command process, then reconcile
can only inspect an existing intent; it refuses to start a fresh send.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid
import subprocess
import signal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api import local_environment
from scheduling import atomic_write

MARKER = ROOT / 'state' / 'outbox-crash-demo.json'


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'status'
    env = local_environment(ROOT)
    address = env.get('EMAIL_ADDRESS', '').strip()
    if not address:
        raise SystemExit('Configure the dedicated email locally first.')
    account = hashlib.sha256(address.lower().encode()).hexdigest()
    if mode == 'prepare':
        if MARKER.exists() and json.loads(MARKER.read_text()).get('state') != 'reconciled':
            raise SystemExit('A prepared/uncertain demo already exists; inspect it rather than creating another send key.')
        nonce = uuid.uuid4().hex[:8]
        draft = {'state': 'prepared', 'account': account,
                 'subject': f'Week5 recovery self-test {nonce}',
                 'body': f'Week5 crash-recovery verification. Test ID: {nonce}. '
                         'This message is sent once to the dedicated agent account itself. No reply needed.'}
        atomic_write(MARKER, json.dumps(draft, indent=2))
        print(json.dumps({'to': 'dedicated agent account itself', 'subject': draft['subject'], 'body': draft['body']}, indent=2))
        return
    draft = json.loads(MARKER.read_text())
    if draft['account'] != account:
        raise SystemExit('Dedicated account changed; refusing to use the old demo draft.')
    if mode == 'status':
        print(json.dumps({k: draft.get(k) for k in ('state', 'subject', 'message_id', 'sent_copies')}, indent=2))
        return
    flags = set(sys.argv[2:])
    if mode == 'send' and (not flags or '--send-to-self' not in flags or flags - {'--send-to-self', '--crash-harness'}):
        raise SystemExit('Explicit --send-to-self is required; send only after owner approval.')
    if mode not in ('send', 'reconcile'):
        raise SystemExit('Use prepare, status, send --send-to-self, or reconcile.')
    target = None
    if mode == 'send' and '--crash-harness' in flags:
        from agent import process_fingerprint
        target = json.loads((ROOT / 'state' / 'receiver.json').read_text())
        pid, ancestors = os.getpid(), set()
        for _ in range(12):
            text = subprocess.run(['ps', '-p', str(pid), '-o', 'ppid='], capture_output=True, text=True).stdout.strip()
            if not text: break
            pid = int(text); ancestors.add(pid)
            if pid <= 1: break
        if (target['pid'] not in ancestors or target.get('mode') != 'always-on' or not target.get('fingerprint')
                or process_fingerprint(target['pid']) != target['fingerprint']):
            raise SystemExit('Crash target is not this command\'s verified harness ancestor; no email sent.')
    os.environ.update({key: env[key] for key in ('EMAIL_ADDRESS', 'EMAIL_APP_PASSWORD')})
    os.environ['AGENT_CONTROL_ROOT'] = str(ROOT)
    directory = ROOT / 'plugins' / 'wenyuzhang2001-email'
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('live_email_demo', directory / 'server.py')
    server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)
    from outbox import Outbox, reconcile_sent
    box = Outbox(ROOT)
    if mode == 'send':
        def pause(intent):
            draft.update(state='sent-before-done', message_id=intent['message_id'], key=intent['key'],
                         pid=os.getpid(), smtp_returned_at=datetime.now(timezone.utc).isoformat(),
                         crashed_harness_pid=target['pid'] if target else None)
            atomic_write(MARKER, json.dumps(draft, indent=2))
            print('SMTP returned; fault-injection pause before durable done. Waiting for the crash test.', flush=True)
            if target:
                if process_fingerprint(target['pid']) != target['fingerprint']:
                    raise RuntimeError('Harness identity changed; no kill attempted. Delivery is uncertain.')
                os.kill(target['pid'], signal.SIGKILL)
            time.sleep(600)
        server.OUTBOX_AFTER_SEND = pause
    else:
        records = box.records()
        if not draft.get('key') or not any(r.get('key') == draft['key'] and r.get('state') == 'intent' for r in records):
            raise SystemExit('No matching prior intent: reconciliation refuses to initiate a send.')
    if mode == 'reconcile':
        def refuse_send(*args):
            raise RuntimeError('Reconciliation is read-only: no matching intent, so no new SMTP attempt is allowed.')
        def inspect_sent(intent, original_body):
            with server.connection('imap') as client:
                return reconcile_sent(client, intent, original_body)
        result = box.send_once(address, address.lower(), draft['subject'], draft['body'],
                               deliver=refuse_send, before_send=refuse_send, reconcile=inspect_sent)
    else:
        result = server.send_message(address, draft['subject'], draft['body'])
    if mode == 'reconcile':
        if result['status'] != 'already_sent':
            raise SystemExit('Expected a prior sent message; inspect outbox state.')
        intent = next(r for r in box.records() if r.get('key') == draft['key'] and r.get('state') == 'intent')
        with server.connection('imap') as client:
            if not reconcile_sent(client, intent, draft['body']):
                raise SystemExit('Could not verify Sent; no resend was attempted.')
            status, data = client.uid('search', None, 'HEADER', 'Message-ID', '"' + draft['message_id'] + '"')
            copies = len((data[0] or b'').split()) if status == 'OK' and data else None
        draft.update(state='reconciled', sent_copies=copies, result=result)
        atomic_write(MARKER, json.dumps(draft, indent=2))
        print(json.dumps({'status': result['status'], 'reconciled': True, 'sent_message_id_copies': copies}))
    else:
        draft.update(state='sent', result=result)
        atomic_write(MARKER, json.dumps(draft, indent=2))
        print('Send completed without the planned crash; inspect the demo state.')


if __name__ == '__main__':
    main()
