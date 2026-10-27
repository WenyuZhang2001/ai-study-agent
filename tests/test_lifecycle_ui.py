import json
import threading
import time
import unittest
from unittest.mock import patch

from api import complete
from telegram_mode import TelegramChannel
from test_harness import FakeRuntime, msg
from test_web import server


class LifecycleUITests(unittest.TestCase):
    def test_cancelled_web_approval_cannot_authorize_a_later_action(self):
        cancel=threading.Event();decisions=[]
        server.pending=None;server.events=[]
        worker=threading.Thread(target=lambda:decisions.append(server.approve('[sa-1 · worker] sleep 600',cancel)))
        worker.start()
        end=time.monotonic()+2
        while server.pending is None and time.monotonic()<end:time.sleep(.01)
        self.assertIsNotNone(server.pending)
        old=server.pending
        cancel.set();worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(decisions,[False])
        self.assertIsNone(server.pending)
        old['approved']=True;old['event'].set()
        self.assertEqual(decisions,[False])
        self.assertTrue(any(e['kind']=='decision' and not e['data']['approved'] for e in server.events))

    def test_idle_telegram_poll_does_not_block_background_notice(self):
        channel=TelegramChannel(FakeRuntime())
        finished=threading.Event();delivered=threading.Event();answers=[]
        def poll(*args,**kwargs):
            time.sleep(.01)
            return [msg('question')] if finished.is_set() else []
        with patch.object(channel,'_poll',side_effect=poll),patch.object(channel,'_send',side_effect=lambda *a:delivered.set() or [100]):
            reader=threading.Thread(target=lambda:answers.append(channel.next_question()))
            writer=threading.Thread(target=lambda:channel.send('child completed'))
            reader.start();writer.start()
            try:self.assertTrue(delivered.wait(1))
            finally:
                finished.set();reader.join(2);writer.join(2)
        self.assertFalse(reader.is_alive())
        self.assertEqual(answers[0]['text'],'question')

    def test_cancelled_telegram_approval_returns_before_poll_or_send(self):
        channel=TelegramChannel(FakeRuntime());cancel=threading.Event();cancel.set()
        with patch.object(channel,'send') as send,patch.object(channel,'poll') as poll:
            self.assertFalse(channel.approve('command',cancel))
        send.assert_not_called();poll.assert_not_called()

    def test_request_timeout_is_transport_option_not_model_payload(self):
        response=unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value=b'{"choices":[]}'
        with patch('api.load_config',return_value={'MODEL':'fixture','BASE_URL':'https://example.invalid','API_KEY':'fixture'}),patch('api.urlopen',return_value=response) as request:
            complete([{'role':'user','content':'test'}],request_timeout=2)
        self.assertEqual(request.call_args.kwargs['timeout'],2)
        self.assertNotIn('request_timeout',json.loads(request.call_args.args[0].data))
