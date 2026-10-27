import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import Agent, BASH
from agent_types import load_agent_types
from test_harness import FakeRuntime, call, completion


class AgentTypeTests(unittest.TestCase):
    def definition(self, root, tools='tools: research__read', extra=''):
        path = Path(root, 'reader.md')
        path.write_text('---\nname: reader\ndescription: Reads research.\n' + tools + '\n' + extra + '\n---\nPrivate child instructions.\n')
        return path

    def test_yaml_list_string_missing_and_empty_tools(self):
        with tempfile.TemporaryDirectory() as d:
            schemas = [BASH, *FakeRuntime().tools]
            for tools, expected in [('tools: research__read', ('research__read',)),
                                    ('tools:\n  - research__read', ('research__read',)),
                                    ('tools: []', ())]:
                self.definition(d, tools)
                self.assertEqual(load_agent_types(d, schemas)['reader'].tools, expected)
            self.definition(d, '')
            self.assertEqual(set(load_agent_types(d, schemas)['reader'].tools), {t['function']['name'] for t in schemas})

    def test_unknown_tool_names_file_and_recursive_tools_always_removed(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.definition(d, 'tools: missing__tool')
            with self.assertRaisesRegex(ValueError, 'reader.md: unknown tool missing__tool'):
                load_agent_types(d, [BASH])
            self.definition(d, 'tools: delegate, subagent, bash')
            kind = load_agent_types(d, [BASH])['reader']
            self.assertEqual(kind.tools, ('bash',))

    def test_bad_limits_and_duplicate_names_fail_at_load(self):
        with tempfile.TemporaryDirectory() as d:
            for value in ('0', 'true', '"ten"'):
                self.definition(d, extra='max_turns: '+value)
                with self.assertRaisesRegex(ValueError, 'max_turns'):
                    load_agent_types(d, FakeRuntime().tools)
            self.definition(d)
            Path(d, 'duplicate.md').write_text(Path(d, 'reader.md').read_text())
            with self.assertRaisesRegex(ValueError, 'duplicate agent name'):
                load_agent_types(d, FakeRuntime().tools)

    def test_child_has_only_own_prompt_and_tools_main_has_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            rt = FakeRuntime()
            self.definition(d, extra='model: example/model\nmax_turns: 3')
            rt.agent_types = load_agent_types(d, [BASH, *rt.tools])
            main = Agent(d, rt)
            self.assertIn('reader: Reads research.', main.system_prompt)
            self.assertNotIn('Private child instructions.', main.system_prompt)
            child = rt.agent_types['reader'].create(agent_id='sa-1',runtime=rt,workdir=d)
            self.assertEqual(child.system_prompt, 'Private child instructions.')
            self.assertEqual([t['function']['name'] for t in child.tools], ['research__read'])
            self.assertEqual(child.model, 'example/model')
            self.assertEqual(child.max_turns, 3)
            self.assertEqual(child.agent_types, {})
            self.assertNotIn('delegate', [t['function']['name'] for t in child.tools])

    def test_delegate_only_returns_report_not_child_tool_results(self):
        with tempfile.TemporaryDirectory() as d:
            rt = FakeRuntime()
            self.definition(d)
            rt.agent_types = load_agent_types(d, [BASH, *rt.tools])
            rt.allow.add('research__read')
            rt.call = lambda *args: {'is_error':False,'text':'RAW_CHILD_ONLY_SENTINEL'}
            seen = []
            def model(messages, **kwargs):
                seen.append(copy.deepcopy(messages))
                if messages[0]['content'] == 'Private child instructions.':
                    return completion(content='Concise child report') if messages[-1]['role']=='tool' else completion([call('research__read',{})])
                if messages[-1]['role']=='tool':
                    return completion(content='Main summary')
                return completion([call('delegate',{'agent':'reader','task':'Self-contained task'})])
            main = Agent(d, rt)
            with patch('agent.complete',side_effect=model):
                result = main.ask('Main private conversation',lambda _:False,lambda *args:None)
            self.assertEqual(result,'Main summary')
            self.assertNotIn('RAW_CHILD_ONLY_SENTINEL',json.dumps(main.messages))
            child = main.delegations.children['sa-1']
            self.assertIn('RAW_CHILD_ONLY_SENTINEL',child.log_path.read_text())
            self.assertNotIn('Main private conversation',json.dumps(child.messages))
            self.assertIn('Concise child report',json.dumps(main.messages))
            self.assertEqual(main.session_usage['subagent_tokens'],84)

    def test_child_turn_limit_reports_failed_reason_to_main(self):
        with tempfile.TemporaryDirectory() as d:
            rt=FakeRuntime(); self.definition(d, extra='max_turns: 1')
            rt.agent_types=load_agent_types(d,[BASH,*rt.tools]); rt.allow.add('research__read')
            responses=[completion([call('delegate',{'agent':'reader','task':'Keep working'})]),
                       completion([call('research__read',{})]),completion(content='Child hit limit')]
            main=Agent(d,rt)
            with patch('agent.complete',side_effect=responses):
                main.ask('delegate',lambda _:False,lambda *a:None)
            child=main.delegations.children['sa-1']
            self.assertEqual(child.state,'failed')
            self.assertIn('Turn limit',child.failure_reason)
            self.assertIn('Turn limit',json.dumps(main.messages))

    def test_delegate_bash_approval_is_labeled_and_denial_does_not_execute(self):
        with tempfile.TemporaryDirectory() as d:
            rt=FakeRuntime(); self.definition(d,'tools: bash')
            rt.agent_types=load_agent_types(d,[BASH,*rt.tools])
            responses=[completion([call('delegate',{'agent':'reader','task':'Check files'})]),completion([call()]),completion(content='Denied.'),completion(content='Summary')]
            approvals=[]
            with patch('agent.complete',side_effect=responses),patch('agent.run_bash') as shell:
                Agent(d,rt).ask('delegate',lambda text:approvals.append(text) or False,lambda *a:None)
            shell.assert_not_called()
            self.assertEqual(approvals,['[sa-1 · reader] pwd'])
