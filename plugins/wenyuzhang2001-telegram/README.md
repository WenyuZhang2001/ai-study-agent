# wenyuzhang2001-telegram

A private-chat-only stdio MCP plugin built with the official Python SDK v2 for AI in Research week 3. Requires uv and Python 3.11+. Inline dependencies are MCP and httpx. No inbound port is opened.

## Configuration and tools

Set TELEGRAM_BOT_TOKEN (BotFather credential) and TELEGRAM_CHAT_ID (positive private-chat integer) in the server environment. Type secrets locally, never into AI chat or source files. The server reads environment variables only; the harness reads its ignored .env and forwards just these configured values.

- send_message(text, request_reply=False): send only to the configured chat, split long Unicode text, and return a short JSON confirmation with message_ids. request_reply=True opens Telegram ForceReply on the final chunk; the harness uses those IDs for linked approvals.
- wait_for_messages(seconds=30): poll up to 50 seconds and return new owner text messages with message IDs/reply targets, or "No new messages." Other chats are dropped and logged to stderr. Startup skips old queued updates. Update offsets prevent repeat delivery during the process.

## Load in the harness

From the harness root, run `uv run server.py` for web chat or `uv run server.py --telegram` for phone chat. Only run one receiver. The loader expands .mcp.json and registers telegram__send_message and telegram__wait_for_messages. In phone mode it hides polling from the model and owns the input channel. Full setup: https://github.com/WenyuZhang2001/ai-study-agent

## Other hosts

With the two variables already exported locally, Claude Code can load the folder:

```sh
claude --plugin-dir /absolute/path/to/wenyuzhang2001-telegram
claude plugin validate /absolute/path/to/wenyuzhang2001-telegram
```

For Codex, use the current `codex mcp add --help`, or configure a stdio entry without putting literal secrets in config:

```toml
[mcp_servers.class_telegram]
command = "uv"
args = ["run", "--script", "/absolute/path/to/wenyuzhang2001-telegram/server.py"]
env_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
```

Stop the harness receiver first. The live second-host test used ephemeral configuration and loaded .env only in a local child launcher, without printing credentials or changing global Codex configuration. Codex successfully sent a fixed phone message. Claude Code validation was unavailable and is not claimed.

## Safety and acceptance

Anyone can discover a bot; only the configured private chat gets forwarded. Never send secrets or whole files. Telegram bot chats are not end-to-end encrypted. Keep .env untracked; revoke a leaked credential with BotFather /revoke. All logs go to stderr and expected network errors omit credential-bearing URLs. The model cannot choose another recipient. This plugin never executes shell commands.

The harness separately requires approval for every bash and non-allowlisted tool call. Replies must target the current numbered approval and contain exact English yes to approve; other replies deny. It handles transient disconnects with capped backoff and enforces expiry across outages. Delivery retries may duplicate a message after an ambiguous timeout, but never rerun a command or model turn. Ordinary quoted questions are not mistaken for approvals.

Live tests passed for phone send/receive, approved execution, refusal, multi-step work, research results, actual wrong-chat drops, and skipping a verified queued marker at startup. .env was unchanged during filtering tests. See the harness tests and README for regression coverage and limitations. A permanent hosting service or scheduler is not installed.
