import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import Agent
from plugins import PluginRuntime, expand
from telegram_mode import TelegramChannel, run_telegram


def completion(calls=None, content=None):
    return {'choices': [{'message': {'content': content, 'tool_calls': calls or []}}],
            'usage': {'prompt_tokens': 42}}


def call(name='bash', arguments=None, id='call1'):
    return {'id': id, 'type': 'function', 'function': {'name': name,
        'arguments': json.dumps(arguments if arguments is not None else {'command': 'pwd'})}}


class FakeRuntime:
    def __init__(self, polls=None):
        self.tools = [{'type': 'function', 'function': {'name': n, 'description': n,
                       'parameters': {'type': 'object'}}} for n in
                      ['telegram__send_message', 'telegram__wait_for_messages', 'research__read']]
        self.allow = {'telegram__send_message'}
        self.calls = []
        self.polls = list(polls or [])

    def skill_prompt(self):
        return 'research: metadata (/tmp/skill/SKILL.md)'

    def redact(self, value):
        return str(value).replace('SECRET123', '[REDACTED]')

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'telegram__send_message':
            return {'is_error': False, 'text': json.dumps({'message_ids': [100]})}
        if name == 'telegram__wait_for_messages':
            messages = self.polls.pop(0) if self.polls else []
            return {'is_error': False, 'text': json.dumps(messages) if messages else 'No new messages.'}
        return {'is_error': False, 'text': 'SECRET123'}


class AgentTests(unittest.TestCase):
    def test_registered_skill_read_skips_approval_and_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'SKILL.md')
            path.write_text('registered instructions')
            runtime = FakeRuntime()
            runtime.skills = [{'path': str(path)}]
            responses = [completion([call(arguments={'command': 'cat ' + str(path)})]), completion(content='Done')]
            with patch('agent.complete', side_effect=responses), patch('agent.run_bash') as shell:
                approve = unittest.mock.Mock()
                agent = Agent(runtime=runtime)
                agent.ask('Read skill', approve, lambda *args: None)
                approve.assert_not_called()
                shell.assert_not_called()
                self.assertIn('registered instructions', json.dumps(agent.messages))
                for command in ['cat ' + str(path) + '; rm -rf /tmp/x', 'cat /tmp/unknown', 'rm ' + str(path)]:
                    self.assertIsNone(agent.skill_read_path(command))

    def test_allowlisted_research_skips_approval_unknown_still_asks(self):
        runtime = FakeRuntime()
        runtime.allow.add('research__read')
        responses = [completion([call('research__read', {})]), completion([call(arguments={'command': 'rm file'})]), completion(content='Stopped')]
        with patch('agent.complete', side_effect=responses), patch('agent.run_bash') as shell:
            approve = unittest.mock.Mock(return_value=False)
            Agent(runtime=runtime).ask('Research', approve, lambda *args: None)
            self.assertEqual(runtime.calls[0][0], 'research__read')
            approve.assert_called_once_with('rm file')
            shell.assert_not_called()

    def test_denial_blocks_batch_and_later_attempts(self):
        responses = [completion([call(), call('telegram__send_message', {'text': 'bypass'}, 'call2')]),
                     completion([call(arguments={'command': 'ls'}, id='call3')]),
                     completion(content='Denied; no command ran.')]
        runtime = FakeRuntime()
        with patch('agent.complete', side_effect=responses) as api, patch('agent.run_bash') as shell:
            approve = unittest.mock.Mock(return_value=False)
            agent = Agent(runtime=runtime)
            agent.ask('List files', approve, lambda *args: None)
            approve.assert_called_once()
            shell.assert_not_called()
            self.assertFalse(runtime.calls)
            self.assertEqual(api.call_args_list[1].kwargs['tool_choice'], 'none')

    def test_telegram_wait_hidden_and_secret_result_redacted(self):
        runtime = FakeRuntime()
        responses = [completion([call('research__read', {}, 'r1')]), completion(content='Done')]
        with patch('agent.complete', side_effect=responses) as api:
            agent = Agent(runtime=runtime, telegram=True)
            agent.ask('Research', lambda _: True, lambda *args: None)
            tools = api.call_args_list[0].kwargs['tools']
            self.assertNotIn('telegram__wait_for_messages', [t['function']['name'] for t in tools])
            history = json.dumps(agent.messages)
            self.assertNotIn('SECRET123', history)
            self.assertIn('[REDACTED]', history)

    def test_bash_always_asks_even_if_allowlist_changed_in_memory(self):
        runtime = FakeRuntime()
        runtime.allow.add('bash')
        with patch('agent.complete', side_effect=[completion([call()]), completion(content='Done')]), patch('agent.run_bash') as shell:
            shell.return_value = {'stdout': 'ok', 'exit_code': 0}
            approve = unittest.mock.Mock(return_value=True)
            Agent(runtime=runtime).ask('pwd', approve, lambda *args: None)
            approve.assert_called_once_with('pwd')
            shell.assert_called_once()

    def test_prose_approval_repaired_into_real_blocked_tool_call(self):
        rt=FakeRuntime()
        responses=[completion(content='请批准我用 bash 读取 SKILL.md，批准后继续。'),
                   completion([call()]),completion(content='已拒绝，未执行。')]
        events=[]
        with patch('agent.complete',side_effect=responses) as api,patch('agent.run_bash') as shell:
            approve=unittest.mock.Mock(return_value=False)
            result=Agent(runtime=rt).ask('research',approve,lambda k,v:events.append((k,v)))
            self.assertEqual(api.call_args_list[1].kwargs['tool_choice'],'required')
            approve.assert_called_once()
            shell.assert_not_called()
            self.assertEqual([v for k,v in events if k=='text'],['已拒绝，未执行。'])

    def test_prose_is_never_parsed_as_an_executable_command(self):
        rt=FakeRuntime()
        prose='请批准 bash 命令 printf should-not-run'
        with patch('agent.complete',side_effect=[completion(content=prose),completion(content=prose)]) as api,patch('agent.run_bash') as shell:
            approve=unittest.mock.Mock()
            final=Agent(runtime=rt).ask('x',approve,lambda *args:None)
            approve.assert_not_called()
            shell.assert_not_called()
            self.assertEqual(api.call_count,2)
            self.assertIn('未生成',final)

    def test_denial_never_triggers_a_proposal_repair(self):
        with patch('agent.complete',side_effect=[completion([call()]),completion(content='请批准我用 bash 换一种命令')]) as api,patch('agent.run_bash') as shell:
            final=Agent(runtime=FakeRuntime()).ask('x',lambda _:False,lambda *args:None)
            self.assertEqual(api.call_count,2)
            self.assertEqual(api.call_args_list[1].kwargs['tool_choice'],'none')
            self.assertIn('已被拒绝',final)
            shell.assert_not_called()

    def test_truncated_final_repaired_without_tools(self):
        cut=completion(content='truncated ans')
        cut['choices'][0]['finish_reason']='length'
        with patch('agent.complete',side_effect=[cut,completion(content='complete answer')]) as api:
            approve=unittest.mock.Mock()
            events=[]
            final=Agent(runtime=FakeRuntime()).ask('x',approve,lambda k,v:events.append((k,v)))
            self.assertEqual(final,'complete answer')
            self.assertEqual(api.call_args_list[1].kwargs['tool_choice'],'none')
            self.assertEqual([v for k,v in events if k=='text'],['complete answer'])
            approve.assert_not_called()

    def test_model_cannot_execute_tools_during_final_repair(self):
        cut=completion(content='cut')
        cut['choices'][0]['finish_reason']='length'
        with patch('agent.complete',side_effect=[cut,completion([call()]),completion(content='done')]),patch('agent.run_bash') as shell:
            approve=unittest.mock.Mock()
            Agent(runtime=FakeRuntime()).ask('x',approve,lambda *args:None)
            approve.assert_not_called()
            shell.assert_not_called()

    def test_unknown_tool_never_runs(self):
        runtime = FakeRuntime()
        with patch('agent.complete', side_effect=[completion([call('missing', {})]), completion(content='Done')]):
            approve = unittest.mock.Mock()
            Agent(runtime=runtime).ask('x', approve, lambda *args: None)
            approve.assert_not_called()
            self.assertFalse(runtime.calls)


def msg(text, reply=None, id=1):
    return {'text': text, 'reply_to_message_id': reply, 'message_id': id}


class ChannelTests(unittest.TestCase):
    def test_only_reply_to_current_approval_accepts(self):
        rt = FakeRuntime([[], [msg('yes', 99), msg('yes')], [msg('yes',100)]])
        self.assertTrue(TelegramChannel(rt).approve('pwd'))

    def test_approval_asks_telegram_to_open_correct_reply_box(self):
        rt=FakeRuntime([[],[msg('yes',100)]])
        self.assertTrue(TelegramChannel(rt).approve('pwd'))
        sends=[args for name,args in rt.calls if name=='telegram__send_message']
        self.assertTrue(sends[0]['request_reply'])
        self.assertIn('审批',sends[0]['text'])

    def test_reply_to_any_chunk_of_same_approval_is_valid(self):
        rt=FakeRuntime([[],[msg('yes',99)]])
        channel=TelegramChannel(rt)
        with patch.object(channel,'send',return_value=[99,100]):
            self.assertTrue(channel.approve('long command'))

    def test_quoted_question_is_preserved_during_approval(self):
        rt=FakeRuntime([[],[msg('analyze TSLA',88),msg('yes',100)]])
        channel=TelegramChannel(rt)
        self.assertTrue(channel.approve('pwd'))
        self.assertEqual(channel.next_question()['text'],'analyze TSLA')

    def test_ordinary_quoted_question_reaches_model(self):
        rt=FakeRuntime()
        rt.routes={'telegram__send_message','telegram__wait_for_messages'}
        channel=unittest.mock.Mock()
        channel.next_question.side_effect=[msg('analyze TSLA',88),KeyboardInterrupt()]
        model=unittest.mock.Mock()
        model.ask.return_value='research reply'
        with patch('telegram_mode.TelegramChannel',return_value=channel), patch('telegram_mode.Agent',return_value=model):
            with self.assertRaises(KeyboardInterrupt):
                run_telegram(rt)
        self.assertEqual(model.ask.call_args.args[0],'analyze TSLA')
        channel.send.assert_any_call('research reply')

    def test_any_non_yes_matching_reply_denies(self):
        rt = FakeRuntime([[], [msg('please run it',100)]])
        self.assertFalse(TelegramChannel(rt).approve('pwd'))

    def test_first_reply_decides_and_does_not_approve_future_command(self):
        rt = FakeRuntime([[], [msg('no',100), msg('yes',100)]])
        self.assertFalse(TelegramChannel(rt).approve('pwd'))

    def test_queued_yes_cannot_approve_next_command(self):
        rt = FakeRuntime([[msg('yes',100)], [msg('no',100)]])
        channel = TelegramChannel(rt)
        channel.buffer.append(msg('yes',100))
        self.assertFalse(channel.approve('pwd'))

    def test_unrelated_question_preserved(self):
        rt = FakeRuntime([[], [msg('next question'), msg('yes',100)]])
        channel = TelegramChannel(rt)
        self.assertTrue(channel.approve('pwd'))
        self.assertEqual(channel.next_question()['text'], 'next question')

    def test_poll_failure_recovers_without_stopping_receiver(self):
        rt = FakeRuntime()
        rt.call = unittest.mock.Mock(side_effect=[
            {'is_error': True, 'text': 'temporary connection error'},
            {'is_error': False, 'text': json.dumps([msg('reconnected question')])}])
        channel = TelegramChannel(rt)
        with patch('telegram_mode.time.sleep') as sleep:
            self.assertEqual(channel.next_question()['text'], 'reconnected question')
            sleep.assert_called_once_with(1)
        self.assertEqual(channel.poll_failures,0)

    def test_send_failure_retries_delivery_only(self):
        rt = FakeRuntime()
        rt.call = unittest.mock.Mock(side_effect=[
            RuntimeError('temporary network error'),
            {'is_error': False, 'text': json.dumps({'message_ids':[123]})}])
        with patch('telegram_mode.time.sleep'):
            self.assertEqual(TelegramChannel(rt).send('test'),[123])
        self.assertTrue(all(c.args[0]=='telegram__send_message' for c in rt.call.call_args_list))

    def test_reply_arriving_after_deadline_cannot_approve(self):
        rt=FakeRuntime([[],[msg('yes',100)]])
        with patch('telegram_mode.time.monotonic',side_effect=[0,0,0,181]):
            self.assertFalse(TelegramChannel(rt).approve('pwd'))

    def test_disconnect_through_approval_deadline_denies(self):
        rt=FakeRuntime()
        rt.call=unittest.mock.Mock(side_effect=[
            {'is_error':False,'text':'No new messages.'},
            {'is_error':False,'text':json.dumps({'message_ids':[100]})},
            {'is_error':True,'text':'connection failed'},
            {'is_error':False,'text':json.dumps({'message_ids':[101]})}])
        with patch('telegram_mode.time.monotonic',side_effect=[0,0,0,180,181]), patch('telegram_mode.time.sleep'):
            self.assertFalse(TelegramChannel(rt).approve('pwd'))

    def test_timeout_denies(self):
        rt = FakeRuntime([[]])
        with patch('telegram_mode.time.monotonic', side_effect=[0, 200]):
            self.assertFalse(TelegramChannel(rt,approval_timeout=180).approve('pwd'))


class RuntimeTests(unittest.TestCase):
    def test_missing_env_and_bash_allow_fail_closed(self):
        with self.assertRaises(ValueError):
            expand('${MISSING}', {})
        with tempfile.TemporaryDirectory() as d:
            Path(d,'settings.json').write_text('{"allow":["bash"]}')
            with self.assertRaises(ValueError):
                PluginRuntime(d)

    def test_real_stdio_lifecycle_skill_disclosure_and_env_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root/'plugins/example'
            (p/'.claude-plugin').mkdir(parents=True)
            (p/'skills/example').mkdir(parents=True)
            (p/'.claude-plugin/plugin.json').write_text('{"name":"example"}')
            (p/'skills/example/SKILL.md').write_text('---\nname: example\ndescription: metadata only\n---\nBODY_SENTINEL_DO_NOT_PRELOAD')
            (root/'.env').write_text('API_KEY=PRIVATE_API_VALUE\nTELEGRAM_BOT_TOKEN=PRIVATE_BOT_VALUE\n')
            (p/'.mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {
                'command':sys.executable, 'args':['${CLAUDE_PLUGIN_ROOT}/fixture.py']}}}))
            (p/'fixture.py').write_text('''import os
from mcp.server import MCPServer
m=MCPServer('fixture')
@m.tool()
def probe()->str:
    return str('API_KEY' in os.environ or 'TELEGRAM_BOT_TOKEN' in os.environ)
if __name__=='__main__': m.run()
''')
            rt = PluginRuntime(root)
            try:
                self.assertEqual(rt.call('fixture__probe', {})['text'], 'False')
                self.assertIn('metadata only', rt.skill_prompt())
                self.assertNotIn('BODY_SENTINEL', rt.skill_prompt())
                self.assertNotIn('PRIVATE_API_VALUE', rt.redact('PRIVATE_API_VALUE'))
            finally:
                rt.close()
            self.assertFalse(rt.thread.is_alive())


if __name__=='__main__':
    unittest.main()
