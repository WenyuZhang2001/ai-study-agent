from contextlib import contextmanager
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from always_on import AlwaysOn
from test_always_on import Runtime, fake_agent


DIRECTORY = Path(__file__).resolve().parents[1] / 'plugins' / 'wenyuzhang2001-email'
spec = importlib.util.spec_from_file_location('week5_mail_policy', DIRECTORY / 'mail_policy.py')
mail_policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail_policy)
spec = importlib.util.spec_from_file_location('week5_outbox', DIRECTORY / 'outbox.py')
outbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outbox)
spec = importlib.util.spec_from_file_location('week5_email_server', DIRECTORY / 'server.py')
email_server = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'mail_policy': mail_policy, 'outbox': outbox}):
    spec.loader.exec_module(email_server)


class MailSafeguardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'AGENT_CONTROL_ROOT': str(self.root)})
        self.environment.start()
        self.now = datetime(2026, 10, 30, 23, 59, tzinfo=ZoneInfo('America/Los_Angeles'))

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def message(self, extra=''):
        return BytesParser(policy=policy.default).parsebytes(
            ('From: agent-test@gmail.com\r\nSubject: unit test\r\n' + extra + '\r\n').encode())

    def test_robot_header_rules_and_header_return(self):
        for header, expected in [('', False), ('Auto-Submitted: no\r\n', False),
                ('Auto-Submitted: NO\r\n', False), ('Auto-Submitted: auto-generated\r\n', True),
                ('Auto-Submitted: \r\n', True), ('List-Id: <a.example>\r\n', True), ('List-Id:\r\n', True)]:
            with self.subTest(header=header):
                message = self.message(header)
                self.assertIs(mail_policy.automatic(message), expected)
                self.assertIs(email_server.headers(message)['automatic'], expected)

    def test_caps_persist_and_reset_at_local_midnight(self):
        for _ in range(2):
            mail_policy.reserve_send('agent-test@gmail.com', self.root, self.now)
        with self.assertRaisesRegex(ValueError, 'replies_per_sender'):
            mail_policy.reserve_send('AGENT-TEST@gmail.com', self.root, self.now)
        mail_policy.reserve_send('agent-test@gmail.com', self.root, self.now + timedelta(minutes=2))
        self.assertNotIn('agent-test@gmail.com', (self.root / 'state' / 'mail-replies.json').read_text())

    def test_multiple_auto_submitted_headers_cannot_hide_automatic_flag(self):
        message = self.message('Auto-Submitted: no\r\nAuto-Submitted: auto-generated\r\n')
        self.assertTrue(mail_policy.automatic(message))

    def test_truncated_headers_are_labelled_automatic_fail_closed(self):
        client = Mock()
        raw = b'From: agent-test@gmail.com\r\nSubject: ' + b'x' * 8192
        client.uid.side_effect = [('OK', [b'1']), ('OK', [(b'header', raw[:8192])])]
        @contextmanager
        def connection(kind):
            yield client
        with patch.object(email_server, 'connection', connection):
            result = email_server.check_inbox()
        self.assertTrue(result['messages'][0]['automatic'])
        self.assertTrue(result['messages'][0]['robot_headers_incomplete'])

    def test_concurrent_sends_cannot_bypass_cap(self):
        accepted, denied = [], []
        def reserve():
            try:
                mail_policy.reserve_send('agent-test@gmail.com', self.root, self.now)
                accepted.append(True)
            except ValueError:
                denied.append(True)
        threads = [threading.Thread(target=reserve) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(denied), 4)

    def test_corrupt_mail_state_fails_closed(self):
        folder = self.root / 'state'
        folder.mkdir()
        (folder / 'mail-replies.json').write_text('not json')
        with self.assertRaisesRegex(ValueError, 'state'):
            mail_policy.reserve_send('agent-test@gmail.com', self.root, self.now)

    def test_reply_to_automatic_message_never_reaches_smtp(self):
        client = Mock()
        client.uid.return_value = ('OK', [(b'header', self.message('Auto-Submitted: auto-replied\r\n').as_bytes())])
        opened = []
        @contextmanager
        def connection(kind):
            opened.append(kind)
            yield client
        with patch.object(email_server, 'connection', connection), patch.object(email_server, 'reserve_send') as reserve, \
                patch.object(email_server, 'credentials', return_value=('agent-test@gmail.com', 'unit-password')):
            with self.assertRaisesRegex(Exception, 'automatic/list'):
                email_server.send_message('agent-test@gmail.com', 'Re: unit test', 'test', reply_to_id='1')
        self.assertEqual(opened, ['imap'])
        reserve.assert_not_called()

    def test_missing_reply_uid_is_refused(self):
        with patch.object(email_server, 'credentials', return_value=('agent-test@gmail.com', 'unit-password')), \
                patch.object(email_server, 'connection') as connection:
            with self.assertRaisesRegex(Exception, 'reply_to_id'):
                email_server.send_message('agent-test@gmail.com', 'Re: unit test', 'test')
        connection.assert_not_called()

    def test_uncertain_smtp_attempt_is_not_refunded_or_retried(self):
        attempts = []
        @contextmanager
        def connection(kind):
            attempts.append(kind)
            client = Mock()
            client.send_message.side_effect = OSError('simulated uncertain delivery')
            yield client
        env = {'AGENT_CONTROL_ROOT': str(self.root)}
        with patch.dict(os.environ, env), patch.object(email_server, 'connection', connection), \
                patch.object(email_server, 'credentials', return_value=('agent-test@gmail.com', 'unit-password')):
            for index in range(2):
                with self.assertRaises(OSError):
                    email_server.send_message('agent-test@gmail.com', f'unit test {index}', 'test')
            with self.assertRaisesRegex(Exception, 'replies_per_sender'):
                email_server.send_message('agent-test@gmail.com', 'unit test third distinct message', 'test')
        self.assertEqual(attempts, ['smtp', 'smtp'])

    def test_inbox_fetch_includes_robot_headers(self):
        client = Mock()
        client.uid.side_effect = [('OK', [b'1']), ('OK', [(b'header', self.message('List-Id: <x>\r\n').as_bytes())])]
        @contextmanager
        def connection(kind):
            yield client
        with patch.object(email_server, 'connection', connection):
            result = email_server.check_inbox(unread_only=False)
        self.assertTrue(result['messages'][0]['automatic'])
        self.assertIn('AUTO-SUBMITTED LIST-ID', client.uid.call_args_list[1].args[2])

    def test_email_watcher_labels_robot_event(self):
        rt = Runtime()
        app = AlwaysOn(rt, self.root, agent=fake_agent(), channel=Mock())
        rt.messages = []
        app.email_once()
        rt.messages = [{'id': '1', 'From': 'robot', 'Subject': 'notification', 'automatic': True}]
        app.email_once()
        event = app.events.get_nowait()
        self.assertTrue(event.automatic)
        self.assertIn('prohibited by code', event.as_text())


if __name__ == '__main__':
    unittest.main()
