from datetime import datetime
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
import subprocess
import sys
import time
from unittest.mock import Mock, patch

from agent import Agent
from always_on import AlwaysOn, Event
from recovery import DurableQueue, ReceiverLease, read_journal, restore_context
from test_harness import FakeRuntime, call, completion


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / 'logs' / 'main.jsonl'

    def tearDown(self):
        self.temp.cleanup()

    def agent(self):
        return Agent(self.root, FakeRuntime(), log_path=self.log)

    def test_fifo_restart_dedup_and_paused_state(self):
        q = DurableQueue(self.root, Event)
        one, two = Event('telegram', 'owner', 'one'), Event('email', 'sender', 'two')
        self.assertTrue(q.put(one, 'msg1'))
        self.assertFalse(q.put(one, 'msg1'))
        q.put(two, 'msg2'); q.set_paused(True); q.close()
        q = DurableQueue(self.root, Event)
        self.assertTrue(q.paused())
        self.assertEqual(q.get_nowait().text, 'one'); q.task_done()
        self.assertEqual(q.get_nowait().text, 'two'); q.task_done()
        self.assertTrue(q.empty())
        self.assertFalse(q.put(one, 'msg1'))
        self.assertEqual(q.path.stat().st_mode & 0o777, 0o600)
        q.close()

    def test_claimed_work_is_interrupted_never_replayed_but_ready_work_survives(self):
        q = DurableQueue(self.root, Event)
        first, second = Event('telegram', 'owner', 'running'), Event('telegram', 'owner', 'pending')
        q.put(first); q.put(second); q.get_nowait(); q.close()
        q = DurableQueue(self.root, Event)
        interrupted = q.recover()
        self.assertEqual([e.identity for e in interrupted], [first.identity])
        self.assertEqual(q.get_nowait().identity, second.identity)
        q.task_done(); self.assertTrue(q.empty()); q.close()

    def test_delivered_marker_finishes_claim_without_replaying(self):
        q = DurableQueue(self.root, Event)
        event = Event('telegram', 'owner', 'done')
        q.put(event); q.get_nowait(); q.close()
        q = DurableQueue(self.root, Event)
        self.assertEqual(q.recover([event.identity]), [])
        self.assertTrue(q.empty()); q.close()

    def test_truncated_last_line_survives_recovery_and_second_restart(self):
        owner = self.agent()
        owner._log('message', owner.messages[0])
        owner._log('message', {'role': 'user', 'content': 'saved fact'})
        with self.log.open('ab') as stream:
            stream.write(b'{"event":"message","data":')
        prefix = self.log.read_bytes()
        restored = self.agent(); result = restore_context(restored)
        self.assertTrue(result['resumed'])
        self.assertTrue(self.log.read_bytes().startswith(prefix))
        second = self.agent(); restore_context(second)
        self.assertTrue(any(m.get('content') == 'saved fact' for m in second.messages))
        read_journal(self.log)  # Ignored fragment is documented, not silently deleted.

    def test_incomplete_call_is_repaired_without_shell_or_model(self):
        owner = self.agent()
        for message in [owner.messages[0], {'role': 'user', 'content': 'send once'},
                        {'role': 'assistant', 'tool_calls': [call('email__send_message', {}, id='pending')]}]:
            owner._log('message', message)
        restored = self.agent()
        with patch('agent.complete') as model, patch('agent.run_bash') as shell:
            info = restore_context(restored)
        self.assertEqual(info['repaired_tools'], 1)
        tool = next(m for m in restored.messages if m['role'] == 'tool')
        self.assertIn('not re-run', tool['content'])
        self.assertEqual(tool['tool_call_id'], 'pending')
        model.assert_not_called(); shell.assert_not_called()

    def test_last_compaction_checkpoint_supersedes_old_messages(self):
        owner = self.agent()
        owner._log('message', owner.messages[0])
        owner._log('message', {'role': 'user', 'content': 'discarded old filler'})
        compact = [owner.messages[0], {'role': 'user', 'content': '[summary] Exact id ABC'}]
        owner._log('compaction', {'messages': compact, 'path': 'unused.md', 'before_input_tokens': 20000})
        owner._log('message', {'role': 'user', 'content': 'after summary'})
        restored = self.agent(); restore_context(restored)
        text = json.dumps(restored.messages)
        self.assertIn('Exact id ABC', text); self.assertIn('after summary', text)
        self.assertNotIn('discarded old filler', text)

    def test_corrupt_interior_record_is_not_silently_skipped(self):
        self.log.parent.mkdir()
        self.log.write_text('bad interior line\n{"event":"startup","data":{}}\n')
        with self.assertRaisesRegex(ValueError, 'interior'):
            restore_context(self.agent())

    def test_receiver_lease_rejects_second_owner(self):
        lease = ReceiverLease(self.root)
        with self.assertRaisesRegex(RuntimeError, 'Another harness receiver'):
            ReceiverLease(self.root)
        lease.close()
        next_lease = ReceiverLease(self.root); next_lease.close()

    def test_pid_reuse_does_not_kill_unrelated_process(self):
        owner = self.agent()
        owner._log('process_start', {'pid': 12345, 'fingerprint': 'old start 12345'})
        with patch('agent.process_fingerprint', return_value='new start 12345'), patch('recovery.os.killpg') as kill:
            restore_context(self.agent())
        kill.assert_not_called()

    def test_matching_orphan_group_is_killed(self):
        owner = self.agent()
        owner._log('process_start', {'pid': 12345, 'fingerprint': 'known start 12345'})
        with patch('agent.process_fingerprint', return_value='known start 12345'), patch('recovery.os.killpg') as kill:
            info = restore_context(self.agent())
        kill.assert_called_once()
        self.assertEqual(info['orphan_groups_killed'], 1)

    def test_real_sigkill_repairs_call_and_kills_orphan_sleep_group(self):
        repo = Path(__file__).resolve().parents[1]
        script = f'''import sys
sys.path.insert(0, {str(repo)!r})
from agent import Agent, run_bash
a=Agent({str(self.root)!r}, log_path={str(self.log)!r})
a._log('message', a.messages[0])
a._log('message', {{'role':'user','content':'fixture long command'}})
a._log('message', {{'role':'assistant','tool_calls':[{{'id':'interrupted','type':'function','function':{{'name':'bash','arguments':'{{"command":"sleep 600"}}'}}}}]}})
run_bash('sleep 600', {str(self.root)!r}, owner=a)
'''
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        pid = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.log.exists():
                    rows, _, _ = read_journal(self.log)
                    starts = [r for r in rows if r['event'] == 'process_start']
                    if starts:
                        pid = starts[-1]['data']['pid']; break
                time.sleep(.02)
            self.assertIsNotNone(pid)
            process.kill(); process.wait(3)
            info = restore_context(self.agent())
            self.assertEqual(info['repaired_tools'], 1)
            self.assertEqual(info['orphan_groups_killed'], 1)
            status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not status or status.startswith('Z'))
        finally:
            if process.poll() is None: process.kill()
            process.wait(3)
            if process.stderr: process.stderr.close()
            if pid:
                try: os.killpg(pid, 9)
                except ProcessLookupError: pass

    def test_mail_burst_paginates_oldest_unseen_without_losing_ids(self):
        rt = FakeRuntime(); rt.routes = {'telegram__send_message', 'telegram__wait_for_messages', 'email__check_inbox'}
        epoch, end = ['one'], [1]
        def inbox(name, args):
            after = args.get('after_id')
            ids = [i for i in range(1, end[0]+1) if after is None or i > int(after)]
            selected = ids[:20] if after is not None else ids[-20:]
            return {'is_error': False, 'text': json.dumps({'messages': [{'id': str(i)} for i in reversed(selected)],
                    'uidvalidity': epoch[0], 'has_more': after is not None and len(ids)>20})}
        rt.call = inbox
        app = AlwaysOn(rt, self.root)
        try:
            app.email_once(); self.assertTrue(app.events.empty())
            end[0] = 46
            app.email_once()
            self.assertEqual(app.events.qsize(), 45)
            app.email_once(); self.assertEqual(app.events.qsize(), 45)
            self.assertEqual(max(map(int, app.seen)), 46)
            epoch[0], end[0] = 'two', 2
            app.email_once()  # New mailbox epoch establishes a fresh baseline, no old-ID collision.
            self.assertEqual(app.seen, {'1', '2'})
            self.assertEqual(app.events.qsize(), 45)
        finally: app.close()

    def test_production_context_resumes_without_importing_demo_logs(self):
        rt = FakeRuntime(); rt.routes = {'telegram__send_message', 'telegram__wait_for_messages', 'email__check_inbox'}
        demo = self.root / 'logs' / 'demos' / 'demo' / 'main.jsonl'
        demo.parent.mkdir(parents=True); demo.write_text('not production history')
        app = AlwaysOn(rt, self.root)
        app.channel.send = Mock(return_value=[100])
        app.events.put(Event('telegram', 'owner', 'durable fact ABC'))
        with patch('agent.complete', return_value=completion(content='remembered')):
            app.process_one(timeout=.1)
        app.close()
        restarted = AlwaysOn(rt, self.root)
        try:
            self.assertTrue(restarted.recovery['resumed'])
            self.assertIn('durable fact ABC', json.dumps(restarted.agent.messages))
            self.assertNotIn('not production history', json.dumps(restarted.agent.messages))
        finally:
            restarted.close()

    def test_incoming_ack_occurs_only_after_durable_queue_commit(self):
        rt = FakeRuntime(); rt.routes = {'telegram__send_message', 'telegram__wait_for_messages', 'email__check_inbox', 'telegram__ack_updates'}
        app = AlwaysOn(rt, self.root)
        app.channel.poll = Mock(return_value=[{'message_id': 7, 'text': 'durable question'}])
        app.channel.pending_offset = 12
        original = app.events.put
        try:
            with patch.object(app.events, 'put', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): app.telegram_once()
            self.assertFalse(any(name == 'telegram__ack_updates' for name, _ in rt.calls))
            app.telegram_once()
            self.assertEqual(app.events.qsize(), 1)
            self.assertEqual(rt.calls[-1][0], 'telegram__ack_updates')
        finally:
            app.close()


if __name__ == '__main__':
    unittest.main()
