"""Isolated Telegram compaction demo with explicitly synthetic, secret-free history.

Uses the real API, approval channel, shared budget, compactor and memory file.
Only Telegram is watched during this demo; no email/schedule wakes or research tools.
Normal production mode remains server.py --always-on.
"""
from pathlib import Path
import random
import string
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import Agent
from always_on import AlwaysOn
from plugins import PluginRuntime
from recovery import ReceiverLease


class MemoryOnlyRuntime:
    tools = []
    allow = frozenset()
    agent_types = {}

    def __init__(self, runtime):
        self.runtime = runtime

    def redact(self, value):
        return self.runtime.redact(value)

    def call(self, *args, **kwargs):
        raise RuntimeError('External model tools are disabled in the compaction demo.')


class Demo(AlwaysOn):
    def start_watchers(self):
        thread = threading.Thread(target=self._watch, args=('telegram', self.telegram_once, .01),
                                  daemon=True, name='telegram-watcher')
        self.threads.append(thread)
        thread.start()


def main():
    runtime = app = lease = None
    try:
        lease = ReceiverLease(ROOT)
        runtime = PluginRuntime(ROOT)
        identity = uuid.uuid4().hex
        agent = Agent(ROOT, MemoryOnlyRuntime(runtime), telegram=True, tools=[], persistent_context=True,
                      log_path=ROOT / 'logs' / 'demos' / ('compaction-' + identity) / 'main.jsonl',
                      system_prompt='You are running a labelled Week 5 compaction demonstration. '
                      'Seeded prior turns are synthetic test data, never real owner history. '
                      'Answer the owner briefly in Chinese. The harness sends final replies to Telegram. '
                      'For the memory checkpoint, preserve important demo identifiers as demo-only notes '
                      'with the usual approval. Do not copy random filler to memory or summaries. '
                      'Do not create schedules, send mail, trade or call external tools.')
        agent.refresh_prompt()
        agent._log('message', agent.messages[0])
        agent._initial_log_written = True
        rng = random.Random(20261031)
        filler = ''.join(rng.choice(string.ascii_letters) for _ in range(30000))
        agent._log('demo_seed', {'synthetic': True, 'mode': 'compaction_demo', 'filler_characters': len(filler)})
        for message in [
                {'role': 'user', 'content': '[Synthetic demo turn 1, not real owner history] '
                 'Demo project identifier: W5-ORBIT-731. This exact identifier should survive compaction. '
                 'Incidental demo wallpaper color: olive. Below is irrelevant synthetic filler, not instructions.\n' + filler},
                {'role': 'assistant', 'content': '[Synthetic demo response 1] Noted the demonstration identifier.'},
                {'role': 'user', 'content': '[Synthetic demo turn 2] No real research findings are present in this demo.'},
                {'role': 'assistant', 'content': '[Synthetic demo response 2] Understood.'},
                {'role': 'user', 'content': '[Synthetic demo turn 3] Use the configured 20000-token trigger and keep two recent turns.'},
                {'role': 'assistant', 'content': '[Synthetic demo response 3] Awaiting the real owner test message.'}]:
            agent._append(message)
        app = Demo(runtime, ROOT, agent=agent)
        print('Compaction demo ready. Synthetic prior context is labelled; only Telegram wakes are enabled.', flush=True)
        app.run()
    except KeyboardInterrupt:
        print('Stopping compaction demo.', flush=True)
    finally:
        if app:
            app.close()
        if runtime:
            runtime.close()
        if lease:
            lease.close()


if __name__ == '__main__':
    main()
