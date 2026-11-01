import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from agent import Agent
from agent_types import AgentType
from always_on import AlwaysOn, Event
from delegation import Delegations
from test_harness import FakeRuntime, completion, call


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rt = FakeRuntime()
        self.rt.routes = {'telegram__send_message', 'telegram__wait_for_messages', 'email__check_inbox'}
        self.agent = Agent(self.temp.name, self.rt, telegram=True)
        self.app = AlwaysOn(self.rt, self.temp.name, agent=self.agent)
        self.sent = []
        self.app.channel.send = lambda text, **kwargs: self.sent.append(text) or [100]
        self.errors = []

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('Timed out waiting for test worker')
            time.sleep(.005)

    def worker(self):
        self.app.events.put(Event('telegram', 'owner', 'test task'))
        def work():
            try:
                self.app.process_one(timeout=.1)
            except BaseException as error:
                self.errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        self.addCleanup(thread.join, 3)
        return thread

    def test_commands_pause_fifo_and_do_not_call_model(self):
        self.rt.polls = [[{'text': '/pause'}, {'text': 'first'}, {'text': 'second'},
                          {'text': '/status'}, {'text': '/unknown'}]]
        with patch('agent.complete', return_value=completion(content='done')) as model:
            self.app.telegram_once()
            self.assertFalse(self.app.process_one(timeout=.01))
            self.assertEqual(self.app.events.qsize(), 2)
            self.assertIn('paused', self.sent[1])
            self.app.command('/stop')  # Idle stop leaves the waiting messages intact.
            model.assert_not_called()
            self.app.command('/resume')
            self.app.process_one(timeout=.01)
            self.app.process_one(timeout=.01)
        users = [m['content'] for m in self.agent.messages if m['role'] == 'user']
        self.assertTrue(users[0].endswith('first'))
        self.assertTrue(users[1].endswith('second'))

    def test_stop_while_waiting_denies_late_approval_and_can_take_new_task(self):
        with patch('agent.complete', return_value=completion([call()])) as model:
            thread = self.worker()
            self.until(lambda: self.app.channel.pending is not None)
            self.app.command('/status')
            self.assertIn('waiting', self.sent[-1])
            self.rt.polls = [[{'text': '/stop', 'reply_to_message_id': 100}]]
            self.app.telegram_once()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(self.errors)
            self.assertEqual(model.call_count, 1)
            self.assertFalse(self.app.stopping)
            self.assertIsNone(self.app.channel.pending)
            self.assertTrue(self.app.channel.route_reply({'text': 'yes', 'reply_to_message_id': 100}))
        messages = self.app.agent.messages
        self.assertTrue(any(m['role'] == 'tool' and 'Interrupted' in m['content'] for m in messages))
        self.assertIn('stopped', messages[-1]['content'])
        self.app.events.put(Event('telegram', 'owner', 'a new task'))
        with patch('agent.complete', return_value=completion(content='new answer')):
            self.app.process_one(timeout=.1)
        self.assertEqual(self.sent[-1], 'new answer')

    def test_stop_during_model_call_blocks_late_tool_and_does_not_overlap(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return completion([call()])
        self.addCleanup(release.set)
        with patch('agent.complete', side_effect=slow) as model, patch('agent.run_bash') as shell:
            thread = self.worker()
            self.assertTrue(entered.wait(2))
            self.app.command('/stop')
            self.app.command('/status')
            self.assertIn('取消收尾', self.sent[-1])
            self.app.command('/resume')
            self.assertTrue(self.app.stopping)
            release.set()
            thread.join(3)
            shell.assert_not_called()
            self.assertEqual(model.call_count, 1)
            self.assertFalse(self.errors)

    @unittest.skipIf(os.name == 'nt', 'POSIX process group check')
    def test_stop_kills_main_sleep_and_entire_child_tree(self):
        pidfile = Path(self.temp.name) / 'sleep.pid'
        command = f'sleep 600 & echo $! > {pidfile}; wait'
        self.app.channel.approve = Mock(return_value=True)
        with patch('agent.complete', return_value=completion([call(arguments={'command': command})])):
            thread = self.worker()
            self.until(lambda: pidfile.exists() and pidfile.read_text().strip())
            process = self.agent.process
            pid = int(pidfile.read_text())
            self.app.command('/status')
            self.assertIn('running', self.sent[-1])
            self.app.command('/stop')
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(self.errors)
            self.assertIsNotNone(process.poll())
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
            self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith('Z'))

    def test_stop_cancels_background_children_even_when_main_is_asleep(self):
        self.rt.agent_types = {'worker': AgentType('worker', 'Worker', (), 'worker', 20)}
        self.agent.agent_types = self.rt.agent_types
        manager = Delegations(self.agent)
        self.agent.delegations = manager
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return completion(content='late child answer')
        with patch('agent.complete', side_effect=slow):
            identity = manager.start('worker', 'test', lambda _: False, lambda *args: None)['id']
            self.assertTrue(entered.wait(2))
            self.app.command('/stop')
            release.set()
            manager.close()  # Wait for the deliberately delayed writer before deleting its temp logs.
            self.assertTrue(manager.children[identity].stop_event.is_set())
            self.assertEqual(manager.records[identity].state, 'stopped')
            self.assertIsNot(self.app.agent, self.agent)

    def test_status_usage_does_not_double_count_after_stop(self):
        self.agent._log('wake', {'kind': 'telegram'})
        self.agent._record_usage({'prompt_tokens': 100, 'completion_tokens': 20, 'cost': .01})
        wake = self.app.budget.start_wake()
        ticket = wake.reserve([], [], 1)
        wake.settle(ticket, {'prompt_tokens': 100, 'completion_tokens': 20, 'cost': .01})
        self.app.active = Event('telegram', 'owner', 'test')
        self.app.command('/stop')
        self.app._replace_stopped_agent()
        self.app.active, self.app.stopping = None, False
        self.app.command('/status')
        self.assertIn('1 次唤醒，120 tokens，$0.010000', self.sent[-1])
        self.assertIn('asleep', self.sent[-1])


if __name__ == '__main__':
    unittest.main()
