# AI Study Agent

A small Python agent harness for AI in Research, week 2, built with Codex. It chats through a local web page and has exactly one tool: bash (PowerShell on Windows). No agent framework or third-party Python libraries are used.

## Configure and run

Install Python 3.10+ or [uv](https://docs.astral.sh/uv/). Copy `.env.example` to `.env` and enter `BASE_URL`, `API_KEY` and `MODEL` locally. Environment variables override the file. Never share or commit `.env`.

macOS / Linux:

```sh
cp .env.example .env
# Edit .env locally and enter your key.
uv run server.py
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
uv run server.py
```

The browser opens at http://127.0.0.1:8765. If necessary, open that URL yourself. `--no-browser` suppresses browser opening; `--port 8766` changes the port. Stop the server with Ctrl+C. Without uv, run `python3 server.py` (Windows: `py server.py`).

## Use

Ask “What files are in this folder?” Review each proposed command and click Approve or Deny. You will see command output, exit code, the assistant's reply, and input/output/cached token counts per API call. New conversation clears in-memory model history. History is lost when the server stops. Commands run in the project folder; this is a personal single-conversation app.

Commands require approval by default. Each command has a 60-second time limit and output is limited to roughly 10,000 bytes. The loop allows at most 20 tool rounds per question. Approved shell commands have the same local permissions as the server; inspect commands before approving them.

## Understand the code

- `system_prompt.md`: editable instructions for the model.
- `api.py`: load local configuration, send one Chat Completions request, display usage.
- `one_call.py`: the initial one-request learning example (`uv run one_call.py "Hello"`).
- `agent.py`: message history, one bash schema, command execution, and the loop.
- `server.py`: localhost HTTP server, approval bridge and conversation events.
- `index.html`: chat, approval buttons and usage display.

`Agent.ask` sends all conversation messages and the tool definition. It saves the assistant reply. For each tool call, it requests approval and appends a result using the matching `tool_call_id`. Then it calls the model again so the model can interpret the result. A response with no tool calls ends the turn.

## Status

API connectivity, text replies and tool-call support passed. The student explained the loop and approved the web interface. Browser testing verified approved execution and replies. Isolated HTTP checks verified denial without execution, concurrent-turn protection, session authentication and reset. Real API tests in a disposable sample folder verified file listing, sequential tool rounds, failed commands and denial; input token counts grew as history accumulated. Command timeout and output truncation checks passed.

Testing was on macOS. At the student's request, the home-directory size scan was not performed; disposable sample folders were used instead. Linux and Windows instructions are provided but have not been tested on those platforms. Cached token reporting was verified with a reported value of zero; a cache hit was not observed.

Published at https://github.com/WenyuZhang2001/ai-study-agent. A fresh clone successfully started with uv; approved listing, denied commands, token reporting and conversation reset passed. The temporary clone was removed. Submitted: https://github.com/huangzesen/ai-in-research-homework/pull/10. The PR contains only the installation skill; its plan check passed and the validate job was skipped. The harness and fresh-install checks above are complete.
