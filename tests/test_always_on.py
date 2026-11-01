import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from always_on import AlwaysOn, Event, WatcherChannel


class Runtime:
    routes = {'telegram__send_message', 'telegram__wait_for_messages', 'email__check_inbox'}

    def __init__(self):
        self.messages = []
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {'is_error': False, 'text': json.dumps({'messages': self.messages})}


def fake_agent():
    return Mock(log_path=Path("/tmp/unused-week5-test-log/main.jsonl"), delegations=None)


class AlwaysOnTests(unittest.TestCase):
    def test_empty_queue_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as folder, patch('agent.complete') as complete:
            agent, channel = fake_agent(), Mock()
            app = AlwaysOn(Runtime(), folder, agent=agent, channel=channel)
            self.assertFalse(app.process_one(timeout=.02))
            agent.ask.assert_not_called()
            complete.assert_not_called()

    def test_mail_baseline_new_event_and_persistent_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            rt, agent, channel = Runtime(), fake_agent(), Mock()
            app = AlwaysOn(rt, folder, agent=agent, channel=channel)
            rt.messages = [{'id': '1', 'From': 'old', 'Subject': 'old mail'}]
            app.email_once()
            self.assertTrue(app.events.empty())
            rt.messages.insert(0, {'id': '2', 'From': 'sender', 'Subject': 'new mail'})
            with patch('agent.complete') as complete:
                app.email_once()
                app.email_once()
                complete.assert_not_called()
            self.assertEqual(app.events.qsize(), 1)
            app.process_one(timeout=.1)
            prompt = agent.ask.call_args.args[0]
            self.assertIn('Untrusted email', prompt)
            self.assertIn('INBOX UID: 2', prompt)
            self.assertEqual(rt.calls[0], ('email__check_inbox', {'unread_only': False, 'limit': 20}))
            restarted = AlwaysOn(rt, folder, agent=fake_agent(), channel=Mock())
            restarted.email_once()
            self.assertTrue(restarted.events.empty())
            self.assertEqual(json.loads(Path(folder, 'state/seen.json').read_text()), ['1', '2'])

    def test_telegram_and_schedule_share_consumer_and_commands_bypass_model(self):
        with tempfile.TemporaryDirectory() as folder:
            channel, agent = Mock(), fake_agent()
            channel.poll.return_value = [{'text': '/status'}, {'text': 'question'}]
            channel.route_reply.return_value = False
            app = AlwaysOn(Runtime(), folder, channel=channel, agent=agent)
            app.telegram_once()
            app.events.put(Event('schedule', 'future-job', 'scheduled task'))
            self.assertEqual(app.events.qsize(), 2)
            app.process_one(timeout=.1)
            app.process_one(timeout=.1)
            prompts = [call.args[0] for call in agent.ask.call_args_list]
            self.assertIn('telegram owner', prompts[0])
            self.assertIn('schedule future-job', prompts[1])
            self.assertEqual(channel.send.call_count, 3)

    def test_local_timezone_label_and_reject_naive_time(self):
        stamp = datetime(2026, 10, 30, 8, tzinfo=ZoneInfo('America/Los_Angeles'))
        self.assertIn('2026-10-30 08:00:00 PDT', Event('telegram', 'owner', 'hi', stamp).as_text())
        with self.assertRaises(ValueError):
            Event('email', 'sender', 'hi', datetime(2026, 10, 30)).as_text()

    def test_mail_failure_preserves_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            rt = Runtime()
            rt.call = Mock(return_value={'is_error': True, 'text': 'unavailable'})
            app = AlwaysOn(rt, folder, agent=fake_agent(), channel=Mock())
            with self.assertRaises(RuntimeError):
                app.email_once()
            self.assertIsNone(app.seen)
            self.assertFalse(app.seen_path.exists())

    def test_pending_approval_does_not_own_poll_lock(self):
        stop = threading.Event()
        channel = WatcherChannel(Mock(), stop, approval_timeout=2)
        channel.send = Mock(return_value=[100])
        result = []
        thread = threading.Thread(target=lambda: result.append(channel.approve('pwd')))
        thread.start()
        try:
            until = time.monotonic() + 1
            while channel.pending is None and time.monotonic() < until:
                time.sleep(.005)
            self.assertIsNotNone(channel.pending)
            self.assertTrue(channel.io_lock.acquire(timeout=.1))
            channel.io_lock.release()
            self.assertTrue(channel.route_reply({'text': 'yes', 'reply_to_message_id': 99}))
            self.assertIsNone(channel.pending['decision'])
            self.assertFalse(channel.route_reply({'text': 'another question'}))
            channel.route_reply({'text': 'yes', 'reply_to_message_id': 100})
            channel.route_reply({'text': 'no', 'reply_to_message_id': 100})
            thread.join(timeout=1)
            self.assertEqual(result, [True])
        finally:
            stop.set()
            thread.join(timeout=3)

    def test_approval_timeout_and_shutdown_deny(self):
        stop = threading.Event()
        channel = WatcherChannel(Mock(), stop, approval_timeout=.01)
        channel.send = Mock(return_value=[100])
        self.assertFalse(channel.approve('pwd'))
        stop.set()
        self.assertFalse(channel.approve('pwd'))


if __name__ == '__main__':
    unittest.main()
