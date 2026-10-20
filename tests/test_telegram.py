import asyncio
import unittest
import importlib.util
import json
import os
from pathlib import Path
import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'plugins/wenyuzhang2001-telegram/server.py'


async def main():
    spec = importlib.util.spec_from_file_location('telegram_server', SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    batches = [
        [{'update_id': 9}], [],
        [{'update_id': 10, 'message': {'message_id': 1, 'chat': {'id': 999, 'type': 'private'}, 'text': 'ignore'}},
         {'update_id': 11, 'message': {'message_id': 2, 'chat': {'id': 123, 'type': 'private'}, 'text': 'hello'}}],
        []]

    def handle(request):
        payload = json.loads(request.content)
        calls.append((request.url.path.rsplit('/', 1)[-1], payload))
        result = batches.pop(0) if calls[-1][0] == 'getUpdates' else {'message_id': 3}
        return httpx.Response(200, json={'ok': True, 'result': result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        bot = module.Telegram('fake-test-token', 123, http)
        await bot.skip_backlog()
        assert bot.offset == 10
        assert calls[0][1]['offset'] == -1 and calls[1][1]['offset'] == 10
        result = json.loads(await bot.wait(100))
        assert [m['text'] for m in result] == ['hello']
        assert bot.offset == 12 and calls[2][1]['timeout'] == 50
        assert await bot.wait(0) == 'No new messages.'
        assert calls[3][1]['offset'] == 12
        await bot.send('😀' * 5000)
        sent = [p for method, p in calls if method == 'sendMessage']
        assert len(sent) == 3 and all(p['chat_id'] == 123 for p in sent)
        assert all(len(p['text'].encode('utf-16-le')) // 2 <= 4096 for p in sent)
        try:
            await bot.send('fake-test-token')
            raise AssertionError('token should be blocked')
        except ValueError:
            pass
        await bot.send('approval request', request_reply=True)
        assert calls[-1][1]['reply_markup']['force_reply'] is True
    print('Mock checks passed: backlog, chat filtering, offsets, polling cap, emoji splitting, token guard.')



class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_mocked_chat_security_and_delivery(self):
        await main()


if __name__ == "__main__":
    asyncio.run(main())
