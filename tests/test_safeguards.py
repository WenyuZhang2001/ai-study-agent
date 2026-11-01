from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from agent import Agent, AgentLimitError
from agent_types import AgentType
from always_on import AlwaysOn, Event, WatcherChannel
from safeguards import Budget
from test_harness import FakeRuntime, call, completion


class SafeguardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.apps = []
        self.now = datetime(2026, 10, 30, 23, 59, tzinfo=ZoneInfo('America/Los_Angeles'))

    def tearDown(self):
        for app in self.apps:
            app.close()
        self.temp.cleanup()

    def config(self, **values):
        (self.root / 'limits.toml').write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in values.items()))

    def budget(self):
        return Budget(self.root, clock=lambda: self.now)

    def app(self):
        rt = FakeRuntime()
        rt.routes = {'telegram__send_message', 'telegram__wait_for_messages', 'email__check_inbox'}
        app = AlwaysOn(rt, self.root)
        app.channel.send = Mock(return_value=[100])
        self.apps.append(app)
        return app, rt

    def test_persistent_accounting_and_local_midnight_reset(self):
        budget = self.budget()
        wake = budget.start_wake()
        ticket = wake.reserve([], [], 1)
        wake.settle(ticket, {'total_tokens': 120, 'cost': .02})
        restarted = self.budget()
        self.assertEqual(restarted.status()['tokens'], 120)
        self.assertEqual(restarted.status()['wakes'], 1)
        self.now += timedelta(minutes=2)
        self.assertEqual(restarted.status()['tokens'], 0)
        self.assertEqual(restarted.status()['reserved'], 0)

    def test_uncertain_usage_and_restart_keep_conservative_charge(self):
        budget = self.budget()
        wake = budget.start_wake()
        pending = wake.reserve([], [], 1)
        reserved = budget.status()['reserved']
        self.assertEqual(self.budget().status()['reserved'], reserved)
        wake.settle(pending, None)
        self.assertEqual(budget.status()['tokens'], reserved)
        self.assertTrue(budget.status()['unknown_cost'])

    def test_call_started_before_midnight_settles_to_its_original_day(self):
        budget = self.budget()
        wake = budget.start_wake()
        ticket = wake.reserve([], [], 1)
        self.now += timedelta(minutes=2)
        wake.settle(ticket, {'total_tokens': 77, 'cost': 0})
        self.assertEqual(budget.status()['tokens'], 0)
        self.assertEqual(budget.days['2026-10-30']['tokens'], 77)

    def test_shared_turn_count_and_concurrent_reservations(self):
        self.config(tokens_per_day=1600, turns_per_wake=8)
        wake = self.budget().start_wake()
        tickets, errors = [], []
        def reserve():
            try:
                tickets.append(wake.reserve([], [], 1))
            except AgentLimitError as error:
                errors.append(error)
        threads = [threading.Thread(target=reserve) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(len(errors), 3)
        self.assertEqual(wake.calls, 1)

    def test_main_and_child_share_turn_limit(self):
        self.config(turns_per_wake=2)
        rt = FakeRuntime()
        rt.agent_types = {'worker': AgentType('worker', 'Worker', (), 'Worker', 20)}
        owner = Agent(self.root, rt, control=self.budget().start_wake())
        with patch('agent.complete', side_effect=[
                completion([call('delegate', {'agent': 'worker', 'task': 'work'})]),
                completion(content='child done')]) as model:
            with self.assertRaisesRegex(AgentLimitError, 'turns_per_wake'):
                owner.ask('work', lambda _: True, lambda *a: None)
        self.assertEqual(model.call_count, 2)
        child = next(iter(owner.delegations.children.values()))
        self.assertIs(child.control, owner.control)
        owner.close()

    def test_tiny_daily_budget_sends_code_notice_without_model_and_can_recover(self):
        self.config(tokens_per_day=1)
        app, _ = self.app()
        app.events.put(Event('telegram', 'owner', 'test limit'))
        with patch('agent.complete') as model:
            self.assertTrue(app.process_one(timeout=.1))
            model.assert_not_called()
        self.assertIn('tokens_per_day', app.channel.send.call_args.args[0])
        self.assertFalse(app.stopping)
        self.assertIsNone(app.active)
        app.command('/status')
        self.assertIn('asleep', app.channel.send.call_args.args[0])
        self.config(tokens_per_day=100000)
        app.events.put(Event('telegram', 'owner', 'a fresh task'))
        with patch('agent.complete', return_value=completion(content='recovered')):
            app.process_one(timeout=.1)
        self.assertEqual(app.channel.send.call_args.args[0], 'recovered')

    def test_watchdog_kills_real_command_and_records_pid(self):
        self.config(minutes_per_wake=.005)
        app, _ = self.app()
        original = app.agent
        app.channel.approve = Mock(return_value=True)
        app.events.put(Event('telegram', 'owner', 'sleep'))
        with patch('agent.complete', return_value=completion([call(arguments={'command': 'sleep 600'})])):
            app.process_one(timeout=.1)
        rows = [json.loads(line) for line in original.log_path.read_text().splitlines()]
        starts = [r for r in rows if r['event'] == 'process_start']
        ends = [r for r in rows if r['event'] == 'process_end']
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['data']['pid'], ends[0]['data']['pid'])
        self.assertTrue(ends[0]['data']['stopped'])
        self.assertIsNotNone(ends[0]['data']['exit_code'])
        self.assertIn('minutes_per_wake', app.channel.send.call_args.args[0])

    def test_watchdog_interrupts_approval_wait(self):
        self.config(minutes_per_wake=.001)
        app, _ = self.app()
        app.events.put(Event('telegram', 'owner', 'approval'))
        with patch('agent.complete', return_value=completion([call()])) as model, patch('agent.run_bash') as shell:
            app.process_one(timeout=.1)
        shell.assert_not_called()
        self.assertEqual(model.call_count, 1)
        self.assertIn('minutes_per_wake', app.channel.send.call_args.args[0])

    def test_ten_minute_approval_timeout_becomes_explicit_tool_result(self):
        channel = WatcherChannel(Mock(), threading.Event())
        channel.send = Mock(return_value=[100])
        self.assertEqual(channel.approval_timeout, 600)
        with patch('always_on.time.monotonic', side_effect=[0, 601]):
            denied = channel.approve('pwd')
        self.assertFalse(denied)
        owner = Agent(self.root, FakeRuntime())
        with patch('agent.complete', side_effect=[completion([call()]), completion(content='declined')]), patch('agent.run_bash') as shell:
            owner.ask('test', lambda _: denied, lambda *a: None)
        shell.assert_not_called()
        tool = next(m for m in owner.messages if m['role'] == 'tool')
        self.assertIn('declined: nobody answered within 10 minutes', tool['content'])
        owner.close()

    def test_automatic_email_wake_cannot_send_even_allowlisted(self):
        rt = FakeRuntime()
        rt.tools.append({'type': 'function', 'function': {'name': 'email__send_message', 'parameters': {'type': 'object'}}})
        rt.allow.add('email__send_message')
        owner = Agent(self.root, rt, control=self.budget().start_wake(automatic_email=True))
        with patch('agent.complete', side_effect=[completion([call('email__send_message',
                {'to': 'test@example.invalid', 'subject': 'x', 'body': 'x'})]), completion(content='blocked')]):
            owner.ask('automatic mail', lambda _: True, lambda *a: None)
        self.assertEqual(rt.calls, [])
        self.assertIn('automatic/list', next(m for m in owner.messages if m['role'] == 'tool')['content'])
        owner.close()

    def test_invalid_configuration_and_state_fail_closed(self):
        for values in [{'turns_per_wake': 0}, {'minutes_per_wake': -1}, {'tokens_per_day': True}]:
            self.config(**values)
            with self.assertRaises(ValueError):
                self.budget()
        self.config(tokens_per_day=100000)
        path = self.root / 'state' / 'usage.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"bad": 1}')
        with self.assertRaisesRegex(ValueError, 'state/usage.json'):
            self.budget()

    def test_failed_reservation_write_does_not_call_model_or_mutate_state(self):
        budget = self.budget()
        wake = budget.start_wake()
        with patch('safeguards.atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                wake.reserve([], [], 1)
        self.assertEqual(wake.calls, 0)
        self.assertEqual(budget.status()['reserved'], 0)


if __name__ == '__main__':
    unittest.main()
