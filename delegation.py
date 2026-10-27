"""Scoped hand-offs and background workers sharing one plugin runtime."""
from dataclasses import dataclass
import json
import math
import queue
import threading
import time

DELEGATE = {
    'type': 'function', 'function': {
        'name': 'delegate',
        'description': 'Hand one task to a fresh subagent and wait for its report. It sees none of this conversation: include all context and the exact report requested. Only its final report and statistics return; detailed tool results remain in its log.',
        'parameters': {'type': 'object', 'properties': {'agent': {'type': 'string'}, 'task': {'type': 'string'}},
                       'required': ['agent', 'task'], 'additionalProperties': False}}}

SUBAGENT = {
    'type': 'function', 'function': {
        'name': 'subagent',
        'description': 'Control background subagents. start(agent,task) immediately returns an id; list shows state, turns, tokens and active time; send(id,message) queues context before the next model call or resumes an idle child; wait(ids,timeout) returns reports when all settle or timeout; read(id,last) shows bounded recent log steps; stop(id) permanently stops the child and kills its command process group. Include all context in each task: children cannot see this conversation. Use start/wait for independent parallel jobs. Failed/stopped children cannot resume.',
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['start','list','send','wait','read','stop']},
            'agent': {'type': 'string'}, 'task': {'type': 'string'}, 'id': {'type': 'string'},
            'message': {'type': 'string'}, 'ids': {'type': 'array', 'items': {'type': 'string'}},
            'timeout': {'type': 'number', 'minimum': 0, 'maximum': 600},
            'last': {'type': 'integer', 'minimum': 1, 'maximum': 50}},
            'required': ['action'], 'additionalProperties': False}}}


@dataclass
class Worker:
    child: object
    task: str
    background: bool
    approve: object
    emit: object
    state: str = 'running'
    active: bool = True
    thread: object = None
    elapsed: float = 0
    active_since: float = 0
    report: str = ''
    notified: bool = False


class Delegations:
    def __init__(self, main):
        self.main = main
        self.children = {}  # Kept public for local inspection and course demos.
        self.records = {}
        self.total_tokens = 0
        self._next_id = 0
        self.condition = threading.Condition(threading.RLock())
        self.approval_lock = threading.RLock()
        self._notes = []
        self._closed = threading.Event()
        self._watcher = None
        self._interface_queue = queue.Queue()
        self._interface_thread = None

    def _capacity(self):
        if self._closed.is_set():
            raise ValueError('This subagent session is closed.')
        if sum(r.active for r in self.records.values()) >= 4:
            raise ValueError('4 subagents are running or finishing cancellation: wait for one or stop one.')

    def _create(self, agent, task, approve, emit, background):
        if agent not in self.main.agent_types:
            raise ValueError('Unknown agent type; choose one of the listed types.')
        if not isinstance(task, str) or not task.strip():
            raise ValueError('A nonempty task is required.')
        with self.condition:
            self._capacity()
            self._next_id += 1
            identity = f'sa-{self._next_id}'
            kind = self.main.agent_types[agent]
            child = kind.create(agent_id=identity, runtime=self.main.runtime, workdir=self.main.workdir,
                                log_path=self.main.log_path.parent / f'{identity}.jsonl')
            self.children[identity] = child
            rec = Worker(child, self.main._redact(task), background, approve, emit,
                         active_since=time.monotonic())
            child.started_at = rec.active_since
            child.deadline = rec.active_since + child.time_limit
            self.records[identity] = rec
            return rec

    def approval(self, callback, description, child=None):
        # The same lock is used for main and child questions, never for model/tool calls.
        while not self.approval_lock.acquire(timeout=.05):
            if self._closed.is_set() or (child and child.stop_event.is_set()):
                return False
        try:
            if self._closed.is_set() or (child and child.stop_event.is_set()):
                return False
            if getattr(callback, 'supports_cancel', False):
                return callback(description, cancel=child.stop_event if child else self._closed)
            return callback(description)
        finally:
            self.approval_lock.release()

    def _usage(self, rec, event, data):
        if event != 'usage':
            return
        tokens = data.get('total_tokens')
        if tokens is None:
            tokens = (data.get('prompt_tokens') or 0) + (data.get('completion_tokens') or 0)
        if not isinstance(tokens, (int, float)) or isinstance(tokens, bool) or tokens < 0:
            tokens = 0
        with self.condition:
            self.total_tokens += tokens
            self.main.session_usage['subagent_tokens'] += tokens
        rec.emit('subagent_usage', {'id': rec.child.id, 'usage': data})

    def _snapshot(self, rec, *, truncate=True):
        child = rec.child
        report = rec.report or child.report or child.failure_reason or ''
        if truncate and len(report) > 4000:
            report = report[:4000] + '\n[Report truncated; full report is in the private child log.]'
        elapsed = rec.elapsed + (time.monotonic() - rec.active_since if rec.active else 0)
        return {'id': child.id, 'agent': child.agent_type, 'state': rec.state,
                'turns': child.turns, 'tokens': child.usage['total_tokens'],
                'seconds': round(elapsed, 2), 'task': rec.task[:120], 'report': report,
                'stats': f'[{child.id} · {child.agent_type} · {child.turns} turns · {child.usage["total_tokens"]} tokens · {elapsed:.2f} s · {rec.state}]',
                'is_error': rec.state == 'failed'}

    def _note(self, rec):
        if not rec.background or rec.notified:
            return
        rec.notified = True
        note = self._snapshot(rec)
        with self.condition:
            self._notes.append(note)
        # Interface sees completion even while the main agent waits for user input.
        self._interface_queue.put((rec.emit,note))
        if self._interface_thread is None or not self._interface_thread.is_alive():
            self._interface_thread = threading.Thread(target=self._interface_worker,daemon=True,
                                                     name='subagent-notices')
            self._interface_thread.start()

    def _interface_worker(self):
        while True:
            try:
                emit,note=self._interface_queue.get(timeout=.1)
            except queue.Empty:
                with self.condition:
                    if self._interface_queue.empty():
                        self._interface_thread=None
                        return
                continue
            try:
                emit('subagent_note',note)
            except Exception:
                # Reports still reach main via drain_notes; no model/tool retry.
                pass

    def drain_notes(self):
        with self.condition:
            notes, self._notes = self._notes, []
        return notes

    def _work(self, rec, task):
        child = rec.child
        try:
            while True:
                if child.stop_event.is_set():
                    break
                try:
                    report = child.ask(task,
                        lambda text: self.approval(rec.approve, f'[{child.id} · {child.agent_type}] '+text, child),
                        lambda event, data: self._usage(rec, event, data))
                except Exception:
                    report = child.failure_reason or 'Subagent failed; see its private log.'
                with self.condition:
                    if rec.state in ('stopped', 'failed'):
                        break
                    rec.report = report
                    # A follow-up arriving after the final model call must not be stranded.
                    pending = child.take_pending()
                    if child.state == 'idle' and pending:
                        task = pending
                        continue
                    rec.state = child.state
                    break
        finally:
            with self.condition:
                rec.elapsed += time.monotonic() - rec.active_since
                rec.active = False
                if rec.state == 'running':
                    rec.state = child.state if child.state != 'running' else 'failed'
                if rec.state in ('stopped', 'failed'):
                    rec.report = child.failure_reason or rec.report or 'Subagent ended.'
                self._note(rec)
                self.condition.notify_all()

    def run(self, agent, task, approve, emit):
        rec = self._create(agent, task, approve, emit, False)
        with self.condition:
            if self._watcher is None:
                self._watcher=threading.Thread(target=self._watch,name='subagent-deadlines',daemon=True)
                self._watcher.start()
        self._work(rec, task)
        with self.condition:
            return self._snapshot(rec)

    def _launch(self, rec, task):
        rec.state, rec.active, rec.notified = 'running', True, False
        rec.active_since = time.monotonic()
        rec.child.deadline = rec.active_since + rec.child.time_limit
        rec.thread = threading.Thread(target=self._work, args=(rec, task),
                                      name=rec.child.id, daemon=True)
        rec.thread.start()
        if self._watcher is None:
            self._watcher = threading.Thread(target=self._watch, name='subagent-deadlines', daemon=True)
            self._watcher.start()

    def start(self, agent, task, approve, emit):
        with self.condition:
            rec = self._create(agent, task, approve, emit, True)
            self._launch(rec, task)
            return {'id': rec.child.id, 'state': 'running'}

    def _get(self, identity):
        if identity not in self.records:
            raise ValueError('Unknown subagent id.')
        return self.records[identity]

    def list(self):
        with self.condition:
            return [self._snapshot(r) for r in self.records.values()]

    def send(self, identity, message):
        if not isinstance(message, str) or not message.strip():
            raise ValueError('A nonempty follow-up message is required.')
        with self.condition:
            rec = self._get(identity)
            if rec.state in ('stopped','failed'):
                raise ValueError(f'{identity} is {rec.state}; start a new subagent.')
            if rec.state == 'running' or rec.active:
                rec.child.inbox.put(self.main._redact(message))
            else:
                self._capacity()
                self._launch(rec, self.main._redact(message))
            return {'id':identity, 'state':rec.state, 'queued':True}

    def wait(self, ids, timeout=60):
        if not isinstance(ids, list) or not ids or not all(isinstance(i,str) for i in ids):
            raise ValueError('ids must be a nonempty list of subagent ids.')
        if not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not math.isfinite(timeout) or not 0<=timeout<=600:
            raise ValueError('timeout must be between 0 and 600 seconds.')
        end = time.monotonic()+timeout
        with self.condition:
            records = [self._get(i) for i in ids]
            while any(r.state == 'running' for r in records):
                remaining = end-time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return [self._snapshot(r) for r in records]

    def read(self, identity, last=5):
        if not isinstance(last,int) or isinstance(last,bool) or not 1<=last<=50:
            raise ValueError('last must be between 1 and 50.')
        with self.condition:
            child = self._get(identity).child
        # Read while holding the writer's lock, so incomplete JSONL lines are never exposed.
        with child._log_lock:
            from collections import deque
            if not child.log_path.exists():
                return []
            with child.log_path.open() as stream:
                rows = list(deque(stream, maxlen=last))
        return [self.main._redact(row.strip())[:500] + (' [step truncated]' if len(row.strip())>500 else '') for row in rows]

    def _cancel(self, rec, state, reason):
        if rec.state in ('stopped','failed'):
            return self._snapshot(rec)
        rec.state = state
        rec.notified = False
        rec.child.cancel(state, reason)
        rec.report = reason
        self._note(rec)
        self.condition.notify_all()
        return self._snapshot(rec)

    def stop(self, identity):
        with self.condition:
            return self._cancel(self._get(identity),'stopped','Stopped by user or main agent.')

    def _watch(self):
        while not self._closed.wait(.05):
            with self.condition:
                if not any(r.active for r in self.records.values()):
                    self._watcher=None
                    return
                for rec in self.records.values():
                    if rec.state=='running' and time.monotonic()>=rec.child.deadline:
                        self._cancel(rec,'failed',f'Time limit exceeded ({rec.child.time_limit} seconds).')

    def dispatch(self, action, *, approve, emit, **args):
        fields = {'start':{'agent','task'},'list':set(),'send':{'id','message'},
                  'wait':{'ids','timeout'},'read':{'id','last'},'stop':{'id'}}
        if action not in fields or set(args)-fields[action]:
            raise ValueError('Invalid subagent action or arguments.')
        if action=='start':return self.start(args['agent'],args['task'],approve,emit)
        if action=='list':return self.list()
        if action=='send':return self.send(args['id'],args['message'])
        if action=='wait':return self.wait(args['ids'],args.get('timeout',60))
        if action=='read':return self.read(args['id'],args.get('last',5))
        return self.stop(args['id'])

    def close(self):
        with self.condition:
            self._closed.set()
            for rec in self.records.values():
                if rec.state not in ('stopped','failed'):
                    self._cancel(rec,'stopped','Session ended.')
            threads = [r.thread for r in self.records.values() if r.thread]
        # In-flight HTTP calls cannot be killed by Python threads; cancel prevents all
        # later actions. Socket/API timeouts bound cleanup; command groups die at once.
        deadline=time.monotonic()+2
        for thread in threads:
            thread.join(max(0,deadline-time.monotonic()))
        if self._watcher:
            self._watcher.join(.2)
