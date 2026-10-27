import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent import Agent
from test_harness import FakeRuntime, call, completion


class AgentStateTests(unittest.TestCase):
    def test_two_agents_keep_context_tools_usage_and_inboxes_separate(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = FakeRuntime()
            selected = [runtime.tools[-1]]
            first = Agent(d, runtime, agent_id='sa-1', agent_type='research',
                          system_prompt='Research only.', tools=selected)
            second = Agent(d, runtime, agent_id='sa-2', system_prompt='Other task.', tools=[])
            with patch('agent.complete', side_effect=[completion(content='first report'), completion(content='second report')]) as model:
                first.ask('Private task A', lambda _: False, lambda *a: None)
                second.ask('Private task B', lambda _: False, lambda *a: None)
            self.assertNotIn('Private task A', json.dumps(second.messages))
            self.assertNotIn('Private task B', json.dumps(first.messages))
            self.assertEqual(first.messages[0]['content'], 'Research only.')
            self.assertEqual(first.state, 'idle')
            self.assertEqual(first.report, 'first report')
            self.assertEqual(first.usage['total_tokens'], 42)
            self.assertEqual(second.usage['total_tokens'], 42)
            self.assertEqual(first.turns, 1)
            first.inbox.put('follow-up')
            self.assertTrue(second.inbox.empty())
            first.tools[0]['function']['description'] = 'changed'
            self.assertNotEqual(selected[0]['function']['description'], 'changed')
            self.assertEqual(model.call_args_list[1].kwargs['tools'], [])
            self.assertNotEqual(first.log_path, second.log_path)

    def test_restricted_agent_cannot_execute_an_unadvertised_tool(self):
        with tempfile.TemporaryDirectory() as d:
            rt = FakeRuntime()
            agent = Agent(d, rt, system_prompt='Reader.', tools=[rt.tools[-1]])
            with patch('agent.complete', side_effect=[completion([call()]), completion(content='report')]), patch('agent.run_bash') as shell:
                approvals = []
                agent.ask('read', lambda text: approvals.append(text) or True, lambda *a: None)
            shell.assert_not_called()
            self.assertEqual(approvals, [])
            self.assertEqual(rt.calls, [])

    def test_same_agent_rejects_overlapping_turns_but_can_continue_after_report(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Agent(d, system_prompt='test')
            started, release = threading.Event(), threading.Event()
            errors = []
            def slow(*args, **kwargs):
                started.set()
                if not release.wait(3):
                    raise RuntimeError('test timeout')
                return completion(content='done')
            def worker():
                try:
                    agent.ask('first', lambda _: False, lambda *a: None)
                except BaseException as exc:
                    errors.append(exc)
            with patch('agent.complete', side_effect=slow):
                thread = threading.Thread(target=worker)
                thread.start()
                try:
                    self.assertTrue(started.wait(2))
                    with self.assertRaisesRegex(RuntimeError, 'running turn'):
                        agent.ask('overlap', lambda _: False, lambda *a: None)
                finally:
                    release.set()
                    thread.join(3)
            self.assertFalse(errors)
            self.assertFalse(thread.is_alive())
            with patch('agent.complete', return_value=completion(content='follow-up report')):
                agent.ask('follow up', lambda _: False, lambda *a: None)
            self.assertEqual(agent.turns, 2)
            self.assertEqual(agent.usage['total_tokens'], 84)
            self.assertIn('first', json.dumps(agent.messages))

    def test_private_logs_redact_messages_arguments_and_results(self):
        with tempfile.TemporaryDirectory() as d:
            rt = FakeRuntime()
            agent = Agent(d, rt)
            with patch('agent.complete', side_effect=[completion([call('research__read', {'query':'SECRET123'})]), completion(content='done')]):
                agent.ask('SECRET123', lambda _: True, lambda *a: None)
            data = agent.log_path.read_text()
            self.assertNotIn('SECRET123', data)
            self.assertIn('[REDACTED]', data)
            records = [json.loads(line) for line in data.splitlines()]
            self.assertTrue(all(r['agent_id'] == 'main' for r in records))
            self.assertEqual(records[-1]['data']['state'], 'idle')
            self.assertEqual(agent.log_path.stat().st_mode & 0o777, 0o600)

    def test_model_failure_records_safe_state_without_exception_payload(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Agent(d, system_prompt='test')
            with patch('agent.complete', side_effect=RuntimeError('PRIVATE_EXCEPTION_PAYLOAD')):
                with self.assertRaises(RuntimeError):
                    agent.ask('task', lambda _: False, lambda *a: None)
            self.assertEqual(agent.state, 'failed')
            self.assertNotIn('PRIVATE_EXCEPTION_PAYLOAD', agent.log_path.read_text())
            with self.assertRaisesRegex(RuntimeError, 'stopped or failed'):
                agent.ask('retry', lambda _: False, lambda *a: None)
