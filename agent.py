"""The agent loop: model -> approved bash -> tool result -> model."""
import json
import os
import re
import signal
import subprocess
from api import ROOT, complete

BASH = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the working folder after user approval. Uses bash on macOS/Linux and PowerShell on Windows. Returns stdout, stderr and exit code. Commands time out after 60 seconds; long output is truncated.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


def run_bash(command, workdir, timeout=60):
    shell = ["powershell", "-NoProfile", "-Command"] if os.name == "nt" else ["bash", "-lc"]
    # Temporary files avoid keeping unlimited command output in memory.
    import tempfile
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            shell + [command], cwd=workdir, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        def tail(file):
            size = file.seek(0, 2)
            file.seek(max(0, size - 5000))
            text = file.read().decode("utf-8", errors="replace")
            return ("[Earlier output truncated]\n" if size > 5000 else "") + text
        return {"stdout": tail(stdout), "stderr": tail(stderr),
                "exit_code": process.returncode, "timed_out": timed_out}


def is_prose_approval_request(text):
    """Detect a narrow failure mode; never parse or execute commands from prose."""
    requesting = re.search(r'请(?:先)?(?:批准|授权|允许)|批准后|需要.{0,30}(?:批准|授权)|please.{0,30}(?:approve|authorize|permission)', text, re.I)
    tool_context = re.search(r'bash|MCP|SKILL\.md|工具|命令|tool|command', text, re.I)
    return bool(requesting and tool_context)


class Agent:
    def __init__(self, workdir=ROOT, runtime=None, telegram=False):
        self.workdir = str(workdir)
        self.runtime, self.telegram = runtime, telegram
        self.first_usage_reported = False
        prompt = (ROOT / 'system_prompt.md').read_text()
        if runtime:
            prompt += '\n\n' + runtime.skill_prompt()
        if telegram:
            prompt += '\nTelegram mode: the harness owns incoming messages and sends your final reply. Never poll messages via bash or tools. Use send_message only for short progress updates. For phone replies use at most 350 Chinese characters or 180 English words. Avoid tables. Put the requested verdict and net-of-cost simulation figures before secondary diagnostics; include data date and limitations.'
        self.messages = [{'role': 'system', 'content': prompt}]

    def ask(self, prompt, approve, emit, max_rounds=20):
        """Run one turn; a denial disables every remaining tool for this turn."""
        redact = self.runtime.redact if self.runtime else str
        self.messages.append({'role': 'user', 'content': redact(prompt)})
        tools = [BASH] + ([t for t in self.runtime.tools
                  if not (self.telegram and t['function']['name'] == 'telegram__wait_for_messages')]
                  if self.runtime else [])
        available = {t['function']['name'] for t in tools}
        denied = False
        force_proposal = False
        proposal_repairs = 0
        force_final = False
        final_repairs = 0
        for round_index in range(max_rounds + 1):
            result = complete(self.messages, tools=tools,
                              max_tokens=2048, tool_choice='none' if denied or force_final or round_index == max_rounds else ('required' if force_proposal else 'auto'))
            force_proposal = False
            usage = result.get('usage', {})
            emit('usage', usage)
            if not self.first_usage_reported:
                print('First model call input tokens: ' + str(usage.get('prompt_tokens', 'not reported')), flush=True)
                self.first_usage_reported = True
            message = result['choices'][0]['message']
            content = redact(message.get('content') or '')
            assistant = {'role': 'assistant', 'content': content or None}
            calls = message.get('tool_calls', [])
            if calls:
                assistant['tool_calls'] = calls
            self.messages.append(assistant)
            if not calls and result['choices'][0].get('finish_reason') == 'length' and final_repairs == 0 and round_index < max_rounds:
                final_repairs += 1
                force_final = True
                self.messages.append({'role': 'system', 'content':
                    'Your reply was truncated. Give one complete concise final answer from the existing results, at most 350 Chinese characters or 180 English words. Put requested net-of-cost figures and verdict first. Do not call any tools.'})
                continue
            if not calls and is_prose_approval_request(content):
                if not denied and not force_final and proposal_repairs == 0 and round_index < max_rounds:
                    proposal_repairs += 1
                    force_proposal = True
                    self.messages.append({'role': 'system', 'content':
                        'Your prose did not create an approval. Propose the intended action with an actual structured tool call now. The harness will request human approval before execution. Do not claim approval was granted or execute via another route.'})
                    continue
                content = ('本轮请求已被拒绝，不会再执行工具或寻求替代批准。' if denied else
                           '本轮未生成可审批的工具请求，未执行任何操作。请重新发送问题；无需回复这条消息 yes。')
                self.messages[-1]['content'] = content
            if content:
                emit('text', content)
            if not calls:
                return content
            for call in calls:
                try:
                    name = call['function']['name']
                    arguments = json.loads(call['function']['arguments'])
                    if not isinstance(arguments, dict) or name not in available:
                        raise ValueError('Invalid or unavailable tool call.')
                    if name == 'bash' and (set(arguments) != {'command'} or not isinstance(arguments['command'], str)):
                        raise ValueError('Invalid bash arguments.')
                    if denied:
                        output = {'error': 'The user declined. All remaining tools are disabled for this turn; do not try another route.'}
                    elif force_final:
                        output = {'error': 'Final-answer repair permits no tool execution.'}
                    elif round_index == max_rounds:
                        output = {'error': 'Tool round limit reached.'}
                    else:
                        allowed = name != 'bash' and self.runtime and name in self.runtime.allow
                        description = arguments['command'] if name == 'bash' else 'MCP tool ' + name + '\n' + json.dumps(arguments, ensure_ascii=False)
                        if allowed or approve(redact(description)):
                            if name == 'bash':
                                output = run_bash(arguments['command'], self.workdir)
                            else:
                                output = self.runtime.call(name, arguments)
                        else:
                            denied = True
                            output = {'error': 'The user declined this tool. Do not retry, substitute another tool, or work around the denial in this turn.'}
                except (ValueError, KeyError, TypeError, OSError, RuntimeError, TimeoutError) as error:
                    output = {'error': redact(error)}
                # Raw secrets never enter model context or interface events.
                encoded = redact(json.dumps(output, ensure_ascii=False))
                if len(encoded) > 60000:
                    encoded = json.dumps({'text': encoded[:60000], 'truncated': True})
                safe_output = json.loads(encoded)
                emit('tool_result', safe_output)
                self.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': encoded})
        text = 'Tool round limit reached. Please start a new question.'
        emit('text', text)
        return text
