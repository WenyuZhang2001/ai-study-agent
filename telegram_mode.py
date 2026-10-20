"""Telegram owns polling; the model owns tool choices, never approval decisions."""
from collections import deque
import json
import secrets
import sys
import time

from agent import Agent

APPROVAL_WORDS = {'yes', 'no', '批准', '同意', '拒绝', '是', '否'}

def is_approval_word(text):
    return text.strip().lower() in APPROVAL_WORDS


class TelegramChannel:
    def __init__(self, runtime, approval_timeout=180):
        self.runtime = runtime
        self.approval_timeout = approval_timeout
        self.pending_questions = deque()
        self.buffer = deque()
        self.poll_failures = 0

    def send(self, text, request_reply=False):
        # Retrying delivery may duplicate a message after an ambiguous timeout;
        # it never reruns a model turn or shell command.
        delay = 1
        while True:
            try:
                arguments = {'text': self.runtime.redact(text)}
                if request_reply:
                    arguments['request_reply'] = True
                result = self.runtime.call('telegram__send_message', arguments)
                if result['is_error']:
                    raise RuntimeError('Telegram send failed.')
                return json.loads(result['text'])['message_ids']
            except (RuntimeError, TimeoutError, ValueError, KeyError, TypeError):
                print(f'Telegram delivery unavailable; retrying in {delay}s. No command is replayed.', file=sys.stderr, flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 15)

    def poll(self, seconds=30, deadline=None):
        try:
            result = self.runtime.call('telegram__wait_for_messages', {'seconds': seconds})
            if result['is_error']:
                raise RuntimeError('Telegram polling failed.')
            messages = [] if result['text'] == 'No new messages.' else json.loads(result['text'])
            self.poll_failures = 0
            return messages
        except (RuntimeError, TimeoutError, ValueError, KeyError, TypeError):
            self.poll_failures += 1
            delay = min(2 ** min(self.poll_failures - 1, 4), 15)
            if deadline is not None:
                delay = max(0, min(delay, deadline - time.monotonic()))
            print(f'Telegram polling unavailable; retrying after {delay:g}s. Pending approval remains unapproved.', file=sys.stderr, flush=True)
            time.sleep(delay)
            return []

    def approve(self, description):
        # Drain already queued messages before creating a new approval. A previous
        # 'yes' cannot approve a future command, even if it arrived in the same batch.
        prior = list(self.buffer) + self.poll(0)
        self.buffer.clear()
        for message in prior:
            if not is_approval_word(message['text']):
                self.pending_questions.append(message)
        approval_id = secrets.token_hex(3)
        ids = self.send(f'审批 {approval_id}：请求执行（bash 命令或 MCP 工具）\n' + description +
                        '\n\n请在本条消息的回复框输入英文 yes（批准）或 no（拒绝）。不要回复其他旧消息。3 分钟内未确认将拒绝。', request_reply=True)
        print(f'Approval {approval_id} waiting; message IDs {ids}.', flush=True)
        deadline = time.monotonic() + self.approval_timeout
        while time.monotonic() < deadline:
            messages = self.poll(max(0, min(30, int(deadline-time.monotonic()))), deadline=deadline)
            if time.monotonic() >= deadline:
                break
            decision = None
            for message in messages:
                if message.get('reply_to_message_id') in ids:
                    # The first matching reply decides; anything but exact yes denies.
                    if decision is None:
                        decision = message['text'].strip().lower() == 'yes'
                elif is_approval_word(message['text']):
                    self.send(f'当前等待审批 {approval_id}。这条确认未回复对应的审批消息，尚未执行。请回复上方该编号的审批消息，输入英文 yes 或 no。')
                else:
                    self.pending_questions.append(message)
            if decision is not None:
                print(f'Approval {approval_id} resolved: {"approved" if decision else "denied"}.', flush=True)
                return decision
        print(f'Approval {approval_id} expired; denied.', flush=True)
        self.send(f'审批 {approval_id} 已超时，本次请求已拒绝。请发送新的问题。')
        return False

    def next_question(self):
        while True:
            if self.pending_questions:
                return self.pending_questions.popleft()
            if self.buffer:
                return self.buffer.popleft()
            self.buffer.extend(self.poll())


def run_telegram(runtime):
    required = {'telegram__send_message', 'telegram__wait_for_messages'}
    if not required.issubset(runtime.routes):
        raise RuntimeError('Telegram mode requires both configured Telegram tools.')
    channel = TelegramChannel(runtime)
    agent = Agent(runtime=runtime, telegram=True)
    channel.send('研究 agent 已上线。可以直接提问；执行命令前会请你“回复”审批消息并输入 yes/no。\n/new 开始新对话。研究模型尚未证明预测优势，不会自动下单。')
    print('Telegram agent ready; waiting for messages.', flush=True)
    while True:
        message = channel.next_question()
        prompt = message['text'].strip()
        # Stale approvals are control messages, never new model questions.
        if is_approval_word(prompt):
            channel.send('当前没有等待中的审批，这条确认未执行任何命令。请先发送新的问题；出现编号审批后，在它的回复框输入英文 yes 或 no。')
            continue
        if prompt == '/new':
            agent = Agent(runtime=runtime, telegram=True)
            channel.send('已开始新对话。')
            continue
        channel.send('收到，正在处理。')
        try:
            final = agent.ask(prompt, channel.approve, lambda kind, data: None)
            channel.send(final or '本轮处理完成。')
        except Exception:
            channel.send('本轮请求失败，未显示内部错误或凭据。请检查本地连接；可用 /new 开始新对话。')
            agent = Agent(runtime=runtime, telegram=True)
