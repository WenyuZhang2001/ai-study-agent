"""Synchronous hand-off through the same loop, with private child contexts."""
from pathlib import Path
import threading
import time

DELEGATE = {
    'type': 'function',
    'function': {
        'name': 'delegate',
        'description': 'Hand one task to a fresh subagent and wait for its report. It sees none of this conversation: include all needed context and the exact report requested. Choose a configured agent type. Only its final report and stats return; its tool calls/results stay in its own log.',
        'parameters': {'type': 'object', 'properties': {
            'agent': {'type': 'string'}, 'task': {'type': 'string'}},
            'required': ['agent', 'task'], 'additionalProperties': False}
    }
}


class Delegations:
    def __init__(self, main):
        self.main = main
        self.children = {}
        self.total_tokens = 0
        self._next_id = 0
        self.approval_lock = threading.Lock()

    def run(self, agent, task, approve, emit):
        if agent not in self.main.agent_types:
            raise ValueError('Unknown agent type; choose one of the listed types.')
        if not isinstance(task, str) or not task.strip():
            raise ValueError('A nonempty task is required.')
        self._next_id += 1
        agent_id = f'sa-{self._next_id}'
        kind = self.main.agent_types[agent]
        child = kind.create(agent_id=agent_id, runtime=self.main.runtime,
                            workdir=self.main.workdir,
                            log_path=self.main.log_path.parent / f'{agent_id}.jsonl')
        self.children[agent_id] = child
        def child_approve(description):
            with self.approval_lock:
                return approve(f'[{agent_id} · {agent}] ' + description)
        # Main-agent messages never contain this emitter's tool results.
        def child_emit(event, data):
            if event == 'usage':
                emit('subagent_usage', {'id': agent_id, 'usage': data})
        start = time.monotonic()
        try:
            report = child.ask(task, child_approve, child_emit)
        except Exception:
            report = child.failure_reason or 'Subagent failed; see its private log.'
        tokens = child.usage['total_tokens']
        self.total_tokens += tokens
        self.main.session_usage['subagent_tokens'] += tokens
        elapsed = round(time.monotonic() - start, 2)
        stats = f'[{agent_id} · {agent} · {child.turns} turns · {tokens} tokens · {elapsed} s · {child.state}]'
        return {'id': agent_id, 'agent': agent, 'state': child.state,
                'report': report, 'stats': stats, 'is_error': child.state == 'failed'}
