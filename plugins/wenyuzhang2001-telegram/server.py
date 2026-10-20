# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2,<3", "httpx>=0.28,<1"]
# ///
"""Private-chat-only Telegram MCP server. stdout belongs to MCP."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import sys

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logging.getLogger("httpx").disabled = True
logging.getLogger("httpcore").disabled = True
log = logging.getLogger("telegram-plugin")


class Telegram:
    def __init__(self, token, chat_id, client):
        self.token, self.chat_id, self.client = token, chat_id, client
        self.offset = 0
        self.lock = asyncio.Lock()

    async def request(self, method, **payload):
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/{method}", json=payload
            )
            data = response.json()
            if response.status_code != 200 or not data.get("ok"):
                code = data.get("error_code", response.status_code)
                raise RuntimeError(f"Telegram API error {code}; check local configuration and ensure only one receiver runs.")
            return data["result"]
        except (httpx.HTTPError, ValueError):
            # HTTP exceptions may contain the credential-bearing URL. Never expose them.
            raise RuntimeError("Telegram connection failed; check connectivity and retry.") from None

    async def skip_backlog(self):
        # -1 asks for the newest update and forgets all earlier updates.
        updates = await self.request("getUpdates", offset=-1, timeout=0)
        if updates:
            self.offset = updates[-1]["update_id"] + 1
            await self.request("getUpdates", offset=self.offset, timeout=0)

    async def send(self, text, request_reply=False):
        if not text.strip():
            raise ValueError("Message must contain text.")
        if self.token in text:
            raise ValueError("Refusing to send the bot token.")
        # A 2,000-codepoint chunk also fits 4,096 UTF-16 units with emoji.
        message_ids = []
        for start in range(0, len(text), 2000):
            payload = {"chat_id": self.chat_id, "text": text[start:start + 2000]}
            if request_reply and start + 2000 >= len(text):
                payload['reply_markup'] = {'force_reply': True, 'input_field_placeholder': 'yes 批准 / no 拒绝'}
            result = await self.request("sendMessage", **payload)
            message_ids.append(result["message_id"])
        return json.dumps({"status": "sent", "message_ids": message_ids})

    async def wait(self, seconds):
        seconds = max(0, min(50, seconds))
        async with self.lock:
            updates = await self.request("getUpdates", offset=self.offset, timeout=seconds)
            messages = []
            for update in updates:
                self.offset = max(self.offset, update["update_id"] + 1)
                message = update.get("message", {})
                chat = message.get("chat", {})
                if chat.get("id") != self.chat_id or chat.get("type") != "private":
                    log.warning("Dropped an update outside the configured private chat.")
                    continue
                if isinstance(message.get("text"), str):
                    messages.append({"message_id": message["message_id"], "text": message["text"],
                                     "reply_to_message_id": message.get("reply_to_message", {}).get("message_id")})
            return json.dumps(messages, ensure_ascii=False) if messages else "No new messages."


telegram = None


@asynccontextmanager
async def lifespan(server):
    global telegram
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    raw_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not raw_chat_id:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID locally before starting.")
    try:
        chat_id = int(raw_chat_id)
        if chat_id <= 0:
            raise ValueError
    except ValueError:
        raise RuntimeError("TELEGRAM_CHAT_ID must be a positive private-chat integer.") from None
    async with httpx.AsyncClient(timeout=65) as client:
        telegram = Telegram(token, chat_id, client)
        await telegram.skip_backlog()
        try:
            yield
        finally:
            telegram = None


mcp = MCPServer("wenyuzhang2001-telegram", lifespan=lifespan)


@mcp.tool()
async def send_message(text: str, request_reply: bool = False) -> str:
    """Send plain text to the owner's configured private Telegram chat only.

    Use for short replies or progress updates the owner requested on their phone.
    text is Unicode text, e.g. 'Analysis finished.' Long text is split safely.
    request_reply=True opens a reply prompt on the last chunk; default False.
    Never send secrets, credentials, or whole files. No recipient parameter exists.
    """
    try:
        return await telegram.send(text, request_reply=request_reply)
    except (RuntimeError, ValueError) as error:
        raise ToolError(str(error)) from None


@mcp.tool()
async def wait_for_messages(seconds: int = 30) -> str:
    """Wait for new text from the configured owner, returning JSON or 'No new messages.'

    seconds is an integer duration in seconds (0–50, capped), e.g. 30.
    Each update is returned once per process; old updates are skipped at startup.
    Other chats and non-text updates are discarded. Only one receiver may run.
    In harness Telegram mode the harness owns this tool; the model must not poll.
    """
    try:
        return await telegram.wait(seconds)
    except RuntimeError as error:
        raise ToolError(str(error)) from None


if __name__ == "__main__":
    mcp.run(transport="stdio")
