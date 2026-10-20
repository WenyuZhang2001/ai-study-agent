import json
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import importlib.util
_spec = importlib.util.spec_from_file_location('harness_web_server', __import__('pathlib').Path(__file__).resolve().parents[1] / 'server.py')
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
from agent import Agent
from test_harness import FakeRuntime, call, completion


class WebTests(unittest.TestCase):
    def request(self, path, body=None, token=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Session-Token'] = server.TOKEN
        if origin:
            headers['Origin'] = origin
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.url+path, data=data, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def until(self, predicate):
        end = time.monotonic()+5
        while time.monotonic()<end:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Web turn did not reach the expected state.')

    def test_session_approval_denial_concurrency_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            server.runtime = FakeRuntime()
            server.agent = Agent(workdir=d,runtime=server.runtime)
            server.events, server.busy, server.pending = [], False, None
            http = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            self.url = f'http://127.0.0.1:{http.server_port}'
            thread=threading.Thread(target=http.serve_forever,daemon=True)
            thread.start()
            responses=[completion([call(arguments={'command':'printf approved'})]),completion(content='done'),
                       completion([call(arguments={'command':'printf denied'})]),completion(content='declined')]
            try:
                with patch('agent.complete',side_effect=responses), patch('agent.run_bash',return_value={'stdout':'approved','exit_code':0}) as shell:
                    self.assertEqual(self.request('/events',token=False)[0],403)
                    self.assertEqual(self.request('/chat',{'message':'x'},origin='http://evil.invalid')[0],403)
                    self.assertEqual(self.request('/chat',{'message':'first'})[0],200)
                    self.until(lambda: server.pending is not None)
                    self.assertEqual(self.request('/chat',{'message':'second'})[0],409)
                    self.assertEqual(self.request('/reset',{})[0],409)
                    id=server.pending['id']
                    self.assertEqual(self.request('/approve',{'id':'wrong','approved':True})[0],409)
                    self.assertEqual(self.request('/approve',{'id':id,'approved':True})[0],200)
                    self.until(lambda: not server.busy)
                    self.assertEqual(shell.call_count,1)
                    self.assertEqual(self.request('/approve',{'id':id,'approved':True})[0],409)
                    self.assertEqual(self.request('/chat',{'message':'deny'})[0],200)
                    self.until(lambda: server.pending is not None)
                    self.assertEqual(self.request('/approve',{'id':server.pending['id'],'approved':False})[0],200)
                    self.until(lambda: not server.busy)
                    self.assertEqual(shell.call_count,1)
                    self.assertEqual(self.request('/reset',{})[0],200)
                    self.assertIs(server.agent.runtime,server.runtime)
                    self.assertEqual(len(server.agent.messages),1)
            finally:
                http.shutdown()
                http.server_close()
                thread.join()


if __name__=='__main__':
    unittest.main()
