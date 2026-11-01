from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest
import subprocess
import sys
import time
from unittest.mock import Mock

from test_mail_safeguards import outbox


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.box = outbox.Outbox(self.temp.name)
        self.deliver, self.reconcile, self.reserve = Mock(), Mock(return_value=False), Mock()

    def tearDown(self):
        self.temp.cleanup()

    def send(self, **kw):
        return self.box.send_once('agent@example.invalid', 'owner@example.invalid', 'demo', 'test body',
                                  deliver=self.deliver, reconcile=self.reconcile, before_send=self.reserve, **kw)

    def test_done_dedup_never_reconnects_or_counts_another_attempt(self):
        first = self.send(); second = self.send()
        self.assertEqual(first['status'], 'sent'); self.assertEqual(second['status'], 'already_sent')
        self.assertEqual(first['message_id'], second['message_id'])
        self.deliver.assert_called_once(); self.reserve.assert_called_once(); self.reconcile.assert_not_called()
        self.assertEqual(self.box.path.stat().st_mode & 0o777, 0o600)

    def test_intent_is_fsynced_before_delivery(self):
        seen = []
        def deliver(mid):
            rows = [json.loads(line) for line in self.box.path.read_text().splitlines()]
            seen.extend(rows)
            self.assertEqual(rows[-1]['state'], 'intent')
            self.assertEqual(rows[-1]['message_id'], mid)
        self.deliver.side_effect = deliver
        self.send(); self.assertEqual(len(seen), 1)

    def test_crash_after_smtp_then_found_in_sent_does_not_resend(self):
        def crash(intent):
            raise SystemExit('simulated process crash')
        with self.assertRaises(SystemExit):
            self.send(after_send=crash)
        self.reconcile.return_value = True
        result = self.send()
        self.assertEqual(result['status'], 'already_sent'); self.assertTrue(result['reconciled'])
        self.deliver.assert_called_once(); self.reserve.assert_called_once()

    def test_real_sigkill_after_mock_delivery_is_reconciled_once(self):
        directory = Path(outbox.__file__).parent
        delivered = Path(self.temp.name) / 'delivered.txt'
        marker = Path(self.temp.name) / 'after-send'
        script = f'''import sys,time
from pathlib import Path
sys.path.insert(0, {str(directory)!r})
from outbox import Outbox
def deliver(mid):
    with open({str(delivered)!r},'a') as f: f.write(mid+'\\n')
def pause(intent):
    Path({str(marker)!r}).write_text('smtp returned')
    time.sleep(600)
Outbox({self.temp.name!r}).send_once('agent@example.invalid','owner@example.invalid','demo','test body',deliver=deliver,reconcile=lambda *a:False,before_send=lambda:None,after_send=pause)
'''
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+5
            while not marker.exists() and time.monotonic()<deadline: time.sleep(.02)
            self.assertTrue(marker.exists())
            process.kill(); process.wait(3)
            self.reconcile.return_value = True
            result = self.send()
            self.assertTrue(result['reconciled'])
            self.deliver.assert_not_called()
            self.assertEqual(len(delivered.read_text().splitlines()), 1)
        finally:
            if process.poll() is None: process.kill()
            process.wait(3)
            if process.stderr: process.stderr.close()

    def test_uncertain_delivery_with_no_sent_copy_fails_closed(self):
        self.deliver.side_effect = OSError('uncertain SMTP')
        with self.assertRaises(OSError):
            self.send()
        self.deliver.side_effect = None
        with self.assertRaisesRegex(ValueError, 'Not resent'):
            self.send()
        self.deliver.assert_called_once()

    def test_concurrent_identical_requests_have_one_smtp_attempt(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.send())) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2)
        self.deliver.assert_called_once()
        self.assertEqual(len(results), 4)

    def test_tail_partial_done_reconciles_without_truncating(self):
        with self.assertRaises(SystemExit):
            self.send(after_send=lambda _: (_ for _ in ()).throw(SystemExit()))
        with self.box.path.open('ab') as stream: stream.write(b'{"state":"done"')
        prefix = self.box.path.read_bytes()
        self.reconcile.return_value = True
        self.send()
        self.assertTrue(self.box.path.read_bytes().startswith(prefix))
        self.assertEqual(self.send()['status'], 'already_sent')
        self.deliver.assert_called_once()

    def test_reply_cap_rejection_creates_no_intent(self):
        self.reserve.side_effect = ValueError('cap reached')
        with self.assertRaises(ValueError): self.send()
        self.deliver.assert_not_called()
        self.assertFalse(self.box.path.exists())

    def test_sent_message_id_match(self):
        client = Mock(); client.list.return_value = ('OK', [b'(\\HasNoChildren \\Sent) "/" "Sent folder"'])
        client.select.return_value = ('OK', [])
        client.uid.return_value = ('OK', [b'7'])
        intent = {'message_id': '<test@agent.local>', 'to': 'owner@example.invalid', 'subject': 'demo',
                  'time': datetime.now(timezone.utc).isoformat()}
        self.assertTrue(outbox.reconcile_sent(client, intent, 'body'))
        self.assertEqual(client.select.call_args.args[0], '"Sent folder"')

    def test_fallback_checks_exact_body_not_just_subject(self):
        client = Mock(); client.list.return_value = ('OK', [])
        client.select.return_value = ('OK', [])
        now = datetime.now(timezone.utc)
        message = EmailMessage(); message['To'] = 'owner@example.invalid'; message['Subject'] = 'demo'
        message['Date'] = format_datetime(now); message.set_content('wrong body')
        client.uid.side_effect = [('OK', [b'']), ('OK', [b'7']), ('OK', [(b'header', message.as_bytes())])]
        intent = {'message_id': '<test@agent.local>', 'to': 'owner@example.invalid', 'subject': 'demo', 'time': now.isoformat()}
        self.assertFalse(outbox.reconcile_sent(client, intent, 'correct body'))

    def test_fallback_can_confirm_rewritten_message_id(self):
        client = Mock(); client.list.return_value = ('OK', [])
        client.select.return_value = ('OK', [])
        now = datetime.now(timezone.utc)
        message = EmailMessage(); message['To'] = 'owner@example.invalid'; message['Subject'] = 'demo'
        message['Date'] = format_datetime(now); message.set_content('correct body')
        client.uid.side_effect = [('OK', [b'']), ('OK', [b'7']), ('OK', [(b'header', message.as_bytes())])]
        intent = {'message_id': '<test@agent.local>', 'to': 'owner@example.invalid', 'subject': 'demo', 'time': now.isoformat()}
        self.assertTrue(outbox.reconcile_sent(client, intent, 'correct body'))


if __name__ == '__main__':
    unittest.main()
