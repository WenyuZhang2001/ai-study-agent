import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent import Agent, BASH
from agent_types import AgentType
from delegation import Delegations
from plugins import PluginRuntime
from test_harness import FakeRuntime, completion, call


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.rt=FakeRuntime()
        self.rt.agent_types={'worker':AgentType('worker','Research worker',('research__read',),'Worker prompt',20)}
        self.rt.allow.add('research__read')
        self.main=Agent(self.temp.name,self.rt)
        self.manager=Delegations(self.main)
        self.main.delegations=self.manager
        self.events=[]
        self.emit=lambda k,v:self.events.append((k,v))

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def until(self,predicate,timeout=3):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.01)
        self.fail('Expected state was not reached.')

    def start(self,task='task',approve=lambda _:False):
        return self.manager.start('worker',task,approve,self.emit)['id']

    def test_three_workers_overlap_and_automatic_notes_reach_main(self):
        barrier=threading.Barrier(4)
        def model(messages,**kwargs):
            barrier.wait(timeout=3)
            return completion(content='Independent report')
        with patch('agent.complete',side_effect=model):
            ids=[self.start(str(i)) for i in range(3)]
            self.assertTrue(all(r['state']=='running' for r in self.manager.list()))
            barrier.wait(timeout=3)
            states=self.manager.wait(ids,3)
        self.assertTrue(all(r['state']=='idle' for r in states))
        self.assertEqual(self.main.session_usage['subagent_tokens'],126)
        self.until(lambda:len([e for e in self.events if e[0]=='subagent_note'])==3)
        with patch('agent.complete',return_value=completion(content='Main summary')) as model:
            self.main.ask('Summarize',lambda _:False,lambda *a:None)
        context=json.dumps(model.call_args.args[0])
        self.assertIn('[subagent notes]',context)
        self.assertIn('Independent report',context)

    def test_followup_resumes_context_without_rereading(self):
        reads=[]
        self.rt.call=lambda *a:reads.append(a) or {'is_error':False,'text':'CACHED_FACT_42'}
        def model(messages,**kwargs):
            if messages[-1]['role']=='user' and messages[-1]['content']=='followup':
                self.assertIn('CACHED_FACT_42',json.dumps(messages))
                return completion(content='Followup based on cached fact')
            if messages[-1]['role']=='tool':return completion(content='Initial report')
            return completion([call('research__read',{})])
        with patch('agent.complete',side_effect=model):
            identity=self.start('initial')
            self.assertEqual(self.manager.wait([identity],3)[0]['state'],'idle')
            child=self.manager.children[identity]
            self.manager.send(identity,'followup')
            self.assertEqual(self.manager.wait([identity],3)[0]['report'],'Followup based on cached fact')
            self.assertIs(child,self.manager.children[identity])
        self.assertEqual(len(reads),1)
        self.assertEqual(child.turns,3)
        self.assertEqual(self.main.session_usage['subagent_tokens'],126)

    def test_running_send_is_consumed_before_next_model_call(self):
        entered,release=threading.Event(),threading.Event()
        seen=[]
        def model(messages,**kwargs):
            seen.append(json.dumps(messages))
            if len(seen)==1:
                entered.set();release.wait(3)
                return completion([call('research__read',{})])
            return completion(content='Followup incorporated')
        with patch('agent.complete',side_effect=model):
            identity=self.start('first')
            self.assertTrue(entered.wait(2))
            self.manager.send(identity,'QUEUED_FOLLOWUP')
            release.set()
            self.manager.wait([identity],3)
        self.assertIn('QUEUED_FOLLOWUP',seen[1])
        self.assertNotIn('QUEUED_FOLLOWUP',seen[0])

    def test_max_four_and_wait_timeout_and_read_bounds(self):
        gate=threading.Event()
        with patch('agent.complete',side_effect=lambda *a,**k:gate.wait(3) and completion(content='done')):
            ids=[self.start(str(i)) for i in range(4)]
            try:
                with self.assertRaisesRegex(ValueError,'4 subagents'):
                    self.start('fifth')
                self.assertEqual(self.manager.wait(ids,0)[0]['state'],'running')
                with self.assertRaises(ValueError):self.manager.wait(ids,601)
                self.assertLessEqual(len(self.manager.read(ids[0],1)),1)
                with self.assertRaises(ValueError):self.manager.read(ids[0],51)
            finally:gate.set()
            self.manager.wait(ids,3)

    @unittest.skipIf(os.name=='nt','POSIX process group assertion')
    def test_stop_kills_real_sleep_group_and_child_cannot_resume(self):
        self.rt.agent_types['worker']=AgentType('worker','Command worker',('bash',),'Worker prompt',20)
        pidfile=Path(self.temp.name,'sleep.pid')
        command=f'sleep 600 & echo $! > {pidfile}; wait'
        approvals=[]
        with patch('agent.complete',return_value=completion([call(arguments={'command':command})])):
            identity=self.start(approve=lambda text:approvals.append(text) or True)
            self.until(lambda:pidfile.exists() and pidfile.read_text().strip())
            child=self.manager.children[identity]
            process=child.process
            pid=int(pidfile.read_text())
            self.assertEqual(self.manager.list()[0]['state'],'running')
            self.assertTrue(self.manager.read(identity,5))
            self.assertEqual(self.manager.stop(identity)['state'],'stopped')
            self.until(lambda:not self.manager.records[identity].active)
        self.assertIsNotNone(process.poll())
        result=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True)
        self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith('Z'))
        self.assertTrue(approvals[0].startswith('[sa-1 · worker] '))
        with self.assertRaisesRegex(ValueError,'stopped'):
            self.manager.send(identity,'wake up')
        self.assertNotIn('idle',[json.loads(l)['data'].get('state') for l in child.log_path.read_text().splitlines() if json.loads(l)['event']=='state'])

    def test_turn_limit_failure_note_and_failed_child_cannot_resume(self):
        self.rt.agent_types['worker']=AgentType('worker','Limited',('research__read',),'Worker prompt',1)
        with patch('agent.complete',return_value=completion([call('research__read',{})])):
            identity=self.start()
            result=self.manager.wait([identity],3)[0]
        self.assertEqual(result['state'],'failed')
        self.assertIn('Turn limit',result['report'])
        self.assertIn('Turn limit',json.dumps(self.manager.drain_notes()))
        with self.assertRaisesRegex(ValueError,'failed'):
            self.manager.send(identity,'retry')

    def test_deadline_fails_during_blocked_model_and_late_tool_never_runs(self):
        gate=threading.Event()
        self.rt.call=unittest.mock.Mock()
        with patch('agent.complete',side_effect=lambda *a,**k:(gate.wait(3),completion([call('research__read',{})]))[1]):
            identity=self.start()
            child=self.manager.children[identity]
            child.deadline=time.monotonic()+.05
            result=self.manager.wait([identity],2)[0]
            self.assertEqual(result['state'],'failed')
            self.assertIn('Time limit',result['report'])
            gate.set()
            self.until(lambda:not self.manager.records[identity].active)
        self.rt.call.assert_not_called()

    def test_approvals_are_serialized_and_stop_prevents_late_approval(self):
        self.rt.agent_types['worker']=AgentType('worker','Command worker',('bash',),'Worker prompt',20)
        entered,release=threading.Event(),threading.Event()
        active=0;peak=0;lock=threading.Lock()
        def approval(text):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            entered.set();release.wait(3)
            with lock:active-=1
            return True
        with patch('agent.complete',return_value=completion([call()])),patch('agent.run_bash') as shell:
            ids=[self.start(approve=approval) for _ in range(2)]
            self.assertTrue(entered.wait(2))
            for identity in ids:self.manager.stop(identity)
            release.set()
            self.until(lambda:not any(r.active for r in self.manager.records.values()))
        self.assertEqual(peak,1)
        shell.assert_not_called()

    def test_failed_child_final_response_cannot_invent_unreturned_observations(self):
        self.rt.agent_types['worker']=AgentType('worker','Limited',('research__read',),'Worker prompt',1)
        with patch('agent.complete',return_value=completion([call('research__read',{})])):
            identity=self.start()
            self.manager.wait([identity],3)
        events=[]
        with patch('agent.complete',return_value=completion(content='The unreturned stock price is $9999.')):
            report=self.main.ask('Report status',lambda _:False,lambda k,v:events.append((k,v)))
        self.assertIn('Turn limit exceeded',report)
        self.assertNotIn('$9999',report)
        self.assertNotIn('$9999',json.dumps(self.main.messages))
        self.assertNotIn('$9999',self.main.log_path.read_text())
        self.assertEqual([v for k,v in events if k=='text'],[report])

    def test_shutdown_stops_children_and_delivers_stop_note_after_idle(self):
        with patch('agent.complete',return_value=completion(content='report')):
            identity=self.start();self.manager.wait([identity],3)
        self.manager.drain_notes()
        self.manager.close()
        self.assertEqual(self.manager.list()[0]['state'],'stopped')
        self.assertEqual(self.manager.drain_notes()[0]['state'],'stopped')


class ParallelMCPTests(unittest.TestCase):
    def test_one_shared_mcp_session_handles_two_overlapping_requests(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'plugins/fixture';(p/'.claude-plugin').mkdir(parents=True)
            (p/'.claude-plugin/plugin.json').write_text('{"name":"fixture"}')
            (p/'.mcp.json').write_text(json.dumps({'mcpServers':{'fixture':{'command':sys.executable,'args':['${CLAUDE_PLUGIN_ROOT}/fixture.py']}}}))
            (p/'fixture.py').write_text('''import asyncio
from mcp.server import MCPServer
m=MCPServer('parallel')
active=0
ready=asyncio.Event()
@m.tool()
async def overlap()->str:
    global active
    active+=1
    if active==2:ready.set()
    await asyncio.wait_for(ready.wait(),2)
    return 'overlapped'
if __name__=='__main__':m.run()
''')
            rt=PluginRuntime(root);results=[]
            try:
                threads=[threading.Thread(target=lambda:results.append(rt.call('fixture__overlap',{}))) for _ in range(2)]
                for t in threads:t.start()
                for t in threads:t.join(5)
                self.assertEqual(len(results),2)
                self.assertTrue(all(not r['is_error'] and 'overlapped' in r['text'] for r in results))
            finally:rt.close()
