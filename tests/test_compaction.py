import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from agent import Agent, AgentLimitError
from compaction import Compactor, validate_tool_chain
from memory_store import MemoryStore
from safeguards import Budget
from test_harness import FakeRuntime, call, completion


SUMMARY = '''## Goal
Demo project only.
## Done
Earlier context read.
## Open
Answer follow-up.
## Decisions
Preserve exact identifiers.
## Exact
W5-ORBIT-731
## People
Owner.
'''


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rt = FakeRuntime()
        self.agent = Agent(self.root, self.rt, persistent_context=True)
        self.agent.refresh_prompt()
        self.agent.messages.extend([
            {'role': 'user', 'content': 'Old identifier W5-ORBIT-731; filler ' + 'old data ' * 1000},
            {'role': 'assistant', 'content': 'Old response'},
            {'role': 'user', 'content': 'Recent turn A'},
            {'role': 'assistant', 'content': None, 'tool_calls': [call('research__read', {}, id='kept')]},
            {'role': 'tool', 'tool_call_id': 'kept', 'content': 'Recent data'},
            {'role': 'assistant', 'content': 'Recent answer A'},
            {'role': 'user', 'content': 'Recent turn B'},
            {'role': 'assistant', 'content': 'Recent answer B'},
        ])
        self.agent.last_input_tokens = 20000
        self.compactor = Compactor(self.root)

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def results(self, summary=SUMMARY):
        return [completion([call('memory', {'action': 'write', 'content': '# Facts\nDemo-only ID: W5-ORBIT-731\n'})]),
                completion(content='DONE'), completion(content=summary)]

    def test_compaction_preserves_verbatim_tail_and_measures_next_real_call(self):
        original = copy.deepcopy(self.agent.messages)
        approvals = []
        with patch('agent.complete', side_effect=self.results()) as model:
            result = self.compactor.compact(self.agent, lambda text: approvals.append(text) or True, lambda *a: None)
        self.assertEqual(len(approvals), 1)
        self.assertIn('memory', approvals[0])
        self.assertEqual(model.call_count, 3)
        self.assertEqual(self.agent.messages[2:], original[3:])
        self.assertIn('W5-ORBIT-731', self.agent.messages[1]['content'])
        validate_tool_chain(self.agent.messages)
        path = Path(result['path'])
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn('Before API input tokens: 20000', path.read_text())
        with patch('agent.complete', return_value=completion(content='W5-ORBIT-731')) as model:
            self.agent.ask('What was the identifier?', lambda _: False, lambda *a: None)
        self.assertEqual(model.call_count, 1)
        self.assertIn('Next-request API input tokens: 42', path.read_text())
        self.assertIsNone(self.agent.pending_compaction)
        rows = [json.loads(line) for line in self.agent.log_path.read_text().splitlines()]
        self.assertTrue(any(r['event'] == 'compaction' and r['data']['compacted'] for r in rows))
        self.assertTrue(any(r['event'] == 'compaction_measurement' for r in rows))

    def test_below_threshold_empty_queue_and_insufficient_turns_make_no_calls(self):
        with patch('agent.complete') as model:
            self.agent.last_input_tokens = 19999
            self.assertIsNone(self.compactor.compact(self.agent, lambda _: True, lambda *a: None))
            self.agent.last_input_tokens = 20000
            self.agent.messages = self.agent.messages[:3]
            self.assertIsNone(self.compactor.compact(self.agent, lambda _: True, lambda *a: None))
        model.assert_not_called()

    def test_declined_memory_write_keeps_original_history(self):
        original = copy.deepcopy(self.agent.messages)
        results = [self.results()[0], completion(content='The owner declined.')]
        with patch('agent.complete', side_effect=results) as model:
            result = self.compactor.compact(self.agent, lambda _: False, lambda *a: None)
        self.assertIn('error', result)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(self.agent.messages, original)
        self.assertFalse(self.agent.memory.path.exists())
        self.assertFalse(list((self.root / 'logs').glob('compactions/*.md')))

    def test_checkpoint_forces_write_only_structured_tool_and_rejects_read_without_retry(self):
        original = copy.deepcopy(self.agent.messages)
        with patch('agent.complete', return_value=completion([call('memory', {'action': 'read'})])) as model:
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertIn('error', result)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.kwargs['tool_choice'], 'required')
        schema = model.call_args.kwargs['tools'][0]['function']['parameters']
        self.assertEqual(schema['properties']['action']['enum'], ['write'])
        self.assertIn('content', schema['required'])
        self.assertEqual(self.agent.messages, original)
        self.assertFalse(self.agent.memory.path.exists())
        self.assertEqual(self.agent.state, 'idle')
        rows = [json.loads(line) for line in self.agent.log_path.read_text().splitlines()]
        aborted = next(r['data'] for r in rows if r['event'] == 'compaction_aborted')
        self.assertEqual(aborted['phase'], 'memory_checkpoint')
        self.assertEqual(aborted['error_type'], 'ToolContractError')

    def test_text_tool_call_is_never_executed_or_retried(self):
        original = copy.deepcopy(self.agent.messages)
        text = '<tool_call><function=memory><parameter=action>write</parameter></function></tool_call>'
        with patch('agent.complete', return_value=completion(content=text)) as model, \
                patch.object(self.agent.memory, 'dispatch') as dispatch:
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertIn('error', result)
        model.assert_called_once()
        dispatch.assert_not_called()
        self.assertEqual(self.agent.messages, original)

    def test_truncated_empty_and_missing_heading_summaries_never_replace(self):
        for summary in ['', 'Incomplete summary', SUMMARY]:
            original = copy.deepcopy(self.agent.messages)
            results = self.results(summary)
            if summary == SUMMARY:
                results[-1]['choices'][0]['finish_reason'] = 'length'
            self.agent.last_input_tokens = 20000
            with patch('agent.complete', side_effect=results):
                result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
            self.assertIn('error', result)
            self.assertEqual(self.agent.messages, original)

    def test_summary_network_failure_keeps_context_without_exception_secret(self):
        original = copy.deepcopy(self.agent.messages)
        with patch('agent.complete', side_effect=[*self.results()[:2], RuntimeError('PRIVATE_EXCEPTION_VALUE')]):
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertIn('error', result)
        self.assertEqual(self.agent.messages, original)
        self.assertNotIn('PRIVATE_EXCEPTION_VALUE', self.agent.log_path.read_text())

    def test_failed_artifact_write_keeps_history_and_no_commit_marker(self):
        original = copy.deepcopy(self.agent.messages)
        with patch('agent.complete', side_effect=self.results()), patch('compaction.atomic_write', side_effect=OSError('disk full')):
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertIn('error', result)
        self.assertEqual(self.agent.messages, original)
        rows = [json.loads(line) for line in self.agent.log_path.read_text().splitlines()]
        self.assertFalse(any(r['event'] == 'compaction' for r in rows))

    def test_memory_caps_and_approval_are_code_enforced(self):
        store = MemoryStore(self.root)
        for content in ['x\n' * 201, 'x' * 32001]:
            with self.assertRaises(ValueError):
                store.dispatch('write', content=content)
        self.rt.allow.add('memory')  # A generic tool allowlist cannot authorize writes.
        with patch('agent.complete', side_effect=[self.results()[0], completion(content='denied')]):
            self.agent.ask('save', lambda _: False, lambda *a: None)
        self.assertFalse(store.path.exists())

    def test_checkpoint_heading_cannot_erase_existing_preferences(self):
        existing = '# Facts\n- Approved budget 100000 tokens\n- Morning report at 08:00\n'
        self.agent.memory.dispatch('write', content=existing)
        results = [completion([call('memory', {'action': 'write', 'content': '# Checkpoint placeholder'})]),
                   completion(content='DONE'), completion(content=SUMMARY)]
        with patch('agent.complete', side_effect=results):
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertNotIn('error', result)
        content = self.agent.memory.read()
        self.assertTrue(content.startswith(existing.rstrip()))
        self.assertIn('Approved budget 100000', content)
        self.assertIn('Morning report at 08:00', content)
        self.assertFalse(self.agent.memory.preserve_existing)

    def test_failed_memory_write_cannot_be_confirmed_by_a_done_response(self):
        existing = '\n'.join(f'original {i}' for i in range(200))
        self.agent.memory.dispatch('write', content=existing)
        original = copy.deepcopy(self.agent.messages)
        with patch('agent.complete', side_effect=[self.results()[0], completion(content='DONE')]) as model:
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertIn('error', result)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(self.agent.messages, original)
        self.assertEqual(self.agent.memory.read(), existing)

    def test_prompt_files_reload_and_are_outside_compacted_summary(self):
        (self.root / 'system_prompt.md').write_text('Edited system rule')
        (self.root / 'AGENTS.md').write_text('Standing exact rule XYZ')
        self.agent.memory.dispatch('write', content='Persistent fact 123')
        self.agent.refresh_prompt()
        prompt = self.agent.messages[0]['content']
        self.assertTrue(prompt.startswith('Edited system rule'))
        self.assertIn('Standing exact rule XYZ', prompt)
        self.assertIn('Persistent fact 123', prompt)
        self.agent.memory.dispatch('write', content='Persistent fact 456')
        self.agent.refresh_prompt()
        self.assertIn('Persistent fact 456', self.agent.messages[0]['content'])
        self.assertNotIn('Persistent fact 123', self.agent.messages[0]['content'])

    def test_symlink_memory_is_refused(self):
        target = self.root / 'other.md'
        target.write_text('x')
        self.agent.memory.path.parent.mkdir()
        self.agent.memory.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.agent.memory.read()

    def test_missing_tool_result_prevents_any_compaction_request(self):
        self.agent.messages.append({'role': 'assistant', 'tool_calls': [call()]})
        with patch('agent.complete') as model:
            result = self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertIn('error', result)
        model.assert_not_called()

    def test_stop_before_commit_preserves_history(self):
        original = copy.deepcopy(self.agent.messages)
        with patch('agent.complete', side_effect=self.results()):
            with self.assertRaisesRegex(RuntimeError, 'Stopped before compaction'):
                self.compactor.compact(self.agent, lambda _: True, lambda *a: None,
                                       commit_lock=threading.RLock(), cancelled=lambda: True)
        self.assertEqual(self.agent.messages, original)

    def test_compaction_calls_share_existing_wake_turn_budget(self):
        (self.root / 'limits.toml').write_text('turns_per_wake = 2\n')
        self.agent.control = Budget(self.root).start_wake()
        original = copy.deepcopy(self.agent.messages)
        with patch('agent.complete', side_effect=self.results()) as model:
            with self.assertRaisesRegex(AgentLimitError, 'turns_per_wake'):
                self.compactor.compact(self.agent, lambda _: True, lambda *a: None)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(self.agent.messages, original)

    def test_api_count_reuse_only_for_unchanged_message_prefix(self):
        self.agent.control = Budget(self.root).start_wake()
        with patch('agent.complete', return_value=completion(content='done')), patch.object(
                self.agent.control, 'reserve', wraps=self.agent.control.reserve) as reserve:
            self.agent.model_call(self.agent.messages, self.agent.tools, lambda *a: None)
            self.assertIsNone(reserve.call_args.kwargs['input_estimate'])
            self.agent.messages.append({'role': 'user', 'content': 'next'})
            self.agent.model_call(self.agent.messages, self.agent.tools, lambda *a: None)
            self.assertIsNotNone(reserve.call_args.kwargs['input_estimate'])
            self.agent.messages[0]['content'] += 'changed rule'
            self.agent.model_call(self.agent.messages, self.agent.tools, lambda *a: None)
            self.assertIsNone(reserve.call_args.kwargs['input_estimate'])


if __name__ == '__main__':
    unittest.main()
