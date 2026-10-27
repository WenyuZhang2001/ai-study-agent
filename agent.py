"""The agent loop: model -> approved bash -> tool result -> model."""
import json
import copy
from datetime import datetime, timezone
import queue
import threading
import time
import uuid
import os
import re
import signal
import shlex
from pathlib import Path
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


class AgentLimitError(RuntimeError):
    """A safe, explicit per-child resource-limit failure."""


class Agent:
    def __init__(self, workdir=ROOT, runtime=None, telegram=False, *,
                 agent_id='main', agent_type='main', system_prompt=None,
                 tools=None, max_turns=20, time_limit=600, log_path=None):
        self.workdir = str(workdir)
        self.runtime, self.telegram = runtime, telegram
        self.first_usage_reported = False
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
            raise ValueError('max_turns must be a positive integer.')
        if not isinstance(time_limit, (int, float)) or not 0 < time_limit < float('inf'):
            raise ValueError('time_limit must be positive and finite.')
        self.id, self.agent_type = agent_id, agent_type
        self.max_turns, self.time_limit = max_turns, time_limit
        self.state, self.report, self.failure_reason = 'idle', '', None
        self.inbox = queue.Queue()
        self.stop_event = threading.Event()
        self.process = None
        self.turns = 0
        self.usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        self.started_at, self.deadline = None, None
        self._turn_lock = threading.Lock()
        self._log_lock = threading.Lock()
        # A unique session directory keeps new conversations from sharing a log.
        session = uuid.uuid4().hex
        self.log_path = Path(log_path) if log_path is not None else Path(workdir) / 'logs' / session / 'main.jsonl'
        defaults = [BASH] + ([t for t in runtime.tools
                    if not (telegram and t['function']['name'] == 'telegram__wait_for_messages')]
                    if runtime else [])
        self.tools = copy.deepcopy(defaults if tools is None else tools)
        if agent_type != 'main' or agent_id != 'main':
            self.tools = [t for t in self.tools if t['function']['name'] not in {'delegate', 'subagent'}]
        self.model = None
        self.delegations = None
        self.session_usage = {'subagent_tokens': 0}
        self.agent_types = getattr(runtime, 'agent_types', {}) if agent_type == 'main' and agent_id == 'main' else {}
        prompt = (ROOT / 'system_prompt.md').read_text() if system_prompt is None else system_prompt
        if runtime and system_prompt is None:
            prompt += '\n\n' + runtime.skill_prompt()
        if telegram and system_prompt is None:
            prompt += '\nTelegram mode: the harness owns incoming messages and sends your final reply. Never poll messages via bash or tools. Use send_message only for short progress updates. For phone replies use at most 350 Chinese characters or 180 English words. Avoid tables. Put the requested verdict and net-of-cost simulation figures before secondary diagnostics; include data date and limitations.'
        if self.agent_types:
            from delegation import DELEGATE
            self.tools.append(copy.deepcopy(DELEGATE))
            from agent_types import agent_type_prompt
            prompt += '\n\n' + agent_type_prompt(self.agent_types)
        self.system_prompt = prompt
        self.messages = [{'role': 'system', 'content': prompt}]
        self._initial_log_written = False

    def _redact(self, value):
        return self.runtime.redact(str(value)) if self.runtime else str(value)

    def _log(self, event, data):
        # Redact the serialized payload before it reaches disk, including tool args.
        record = {'time': datetime.now(timezone.utc).isoformat(), 'agent_id': self.id,
                  'agent_type': self.agent_type, 'event': event,
                  'data': json.loads(self._redact(json.dumps(data, ensure_ascii=False)))}
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Runtime transcripts can contain private mail; keep them local/private.
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _append(self, message):
        safe = json.loads(self._redact(json.dumps(message, ensure_ascii=False)))
        self.messages.append(safe)
        self._log('message', safe)

    def _record_usage(self, usage):
        prompt = usage.get('prompt_tokens') or 0
        completion = usage.get('completion_tokens') or 0
        for key, value in (('prompt_tokens', prompt), ('completion_tokens', completion),
                           ('total_tokens', usage.get('total_tokens', prompt + completion) or 0)):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                self.usage[key] += value
        self._log('usage', usage)

    def skill_read_path(self, command):
        """Recognize only a plain cat of a registered plugin skill; never run it as shell."""
        if not self.runtime:
            return None
        try:
            words = shlex.split(command)
        except ValueError:
            return None
        if words[:2] == ['cat', '--']:
            words = ['cat'] + words[2:]
        if len(words) != 2 or words[0] != 'cat':
            return None
        candidate = Path(words[1])
        if not candidate.is_absolute():
            return None
        for skill in getattr(self.runtime, 'skills', []):
            if str(candidate) == skill['path'] and not candidate.is_symlink():
                return candidate
        return None

    def ask(self, prompt, approve, emit, max_rounds=None):
        """Run a turn on this agent's private context; reject concurrent turns."""
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError('This agent already has a running turn.')
        try:
            if self.state in ('stopped', 'failed'):
                raise RuntimeError('This agent is stopped or failed; create a new agent.')
            if not self._initial_log_written:
                self._log('message', self.messages[0])
                self._initial_log_written = True
            if self.agent_types and self.delegations is None:
                from delegation import Delegations
                self.delegations = Delegations(self)
            self.state = 'running'
            self.report = ''
            if self.started_at is None:
                self.started_at = time.monotonic()
                self.deadline = self.started_at + self.time_limit
            self._log('state', {'state': self.state})
            try:
                result = self._ask(prompt, approve, emit,
                                   self.max_turns if max_rounds is None else max_rounds)
            except BaseException as error:
                self.state = 'failed'
                self.failure_reason = str(error) if isinstance(error, AgentLimitError) else 'Agent turn failed.'
                self._log('state', {'state': self.state, 'reason': self.failure_reason})
                raise
            self.report, self.state = result, 'idle'
            self._log('state', {'state': self.state, 'report': result})
            return result
        finally:
            self._turn_lock.release()

    def _check_limits(self):
        if self.agent_type == 'main' and self.id == 'main':
            return
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise AgentLimitError(f'Time limit exceeded ({self.time_limit} seconds).')
        if self.turns >= self.max_turns:
            raise AgentLimitError(f'Turn limit exceeded ({self.max_turns} model calls).')

    def _ask(self, prompt, approve, emit, max_rounds):
        """Existing loop and approval semantics, using per-agent tools/state."""
        redact = self._redact
        self._append({'role': 'user', 'content': redact(prompt)})
        tools = self.tools
        available = {t['function']['name'] for t in tools}
        denied = False
        force_proposal = False
        proposal_repairs = 0
        force_final = False
        final_repairs = 0
        for round_index in range(max_rounds + 1):
            self._check_limits()
            model_options = {'model': self.model} if self.model else {}
            result = complete(self.messages, **model_options, tools=tools,
                              max_tokens=2048, tool_choice='none' if denied or force_final or round_index == max_rounds else ('required' if force_proposal else 'auto'))
            force_proposal = False
            self.turns += 1
            usage = result.get('usage', {})
            self._record_usage(usage)
            emit('usage', usage)
            if self.agent_type != 'main' or self.id != 'main':
                if time.monotonic() >= self.deadline:
                    raise AgentLimitError(f'Time limit exceeded ({self.time_limit} seconds).')
            if not self.first_usage_reported:
                print('First model call input tokens: ' + str(usage.get('prompt_tokens', 'not reported')), flush=True)
                self.first_usage_reported = True
            message = result['choices'][0]['message']
            content = redact(message.get('content') or '')
            assistant = {'role': 'assistant', 'content': content or None}
            calls = message.get('tool_calls', [])
            if calls:
                assistant['tool_calls'] = calls
            self._append(assistant)
            if not calls and result['choices'][0].get('finish_reason') == 'length' and final_repairs == 0 and round_index < max_rounds:
                final_repairs += 1
                force_final = True
                self._append({'role': 'system', 'content':
                    'Your reply was truncated. Give one complete concise final answer from the existing results, at most 350 Chinese characters or 180 English words. Put requested net-of-cost figures and verdict first. Do not call any tools.'})
                continue
            if not calls and is_prose_approval_request(content):
                if not denied and not force_final and proposal_repairs == 0 and round_index < max_rounds:
                    proposal_repairs += 1
                    force_proposal = True
                    self._append({'role': 'system', 'content':
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
                        skill_path = self.skill_read_path(arguments['command']) if name == 'bash' else None
                        internal_delegate = name == 'delegate' and self.delegations is not None
                        allowed = internal_delegate or skill_path is not None or (name != 'bash' and self.runtime and name in self.runtime.allow)
                        description = arguments['command'] if name == 'bash' else 'MCP tool ' + name + '\n' + json.dumps(arguments, ensure_ascii=False)
                        if allowed or approve(redact(description)):
                            if name == 'delegate':
                                output = self.delegations.run(approve=approve, emit=emit, **arguments)
                            elif name == 'bash':
                                if skill_path is not None:
                                    output = {'stdout': skill_path.read_text(), 'stderr': '', 'exit_code': 0}
                                elif self.agent_type != 'main' or self.id != 'main':
                                    remaining = max(0.01, self.deadline - time.monotonic())
                                    output = run_bash(arguments['command'], self.workdir, timeout=min(60, remaining))
                                else:
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
                self._append({'role': 'tool', 'tool_call_id': call['id'], 'content': encoded})
        text = 'Tool round limit reached. Please start a new question.'
        emit('text', text)
        return text
