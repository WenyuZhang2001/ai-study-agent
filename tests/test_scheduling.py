from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from agent import Agent
from always_on import AlwaysOn
from scheduling import Scheduler
from test_always_on import Runtime, fake_agent
from test_harness import FakeRuntime, call, completion


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 30, 7, 59, tzinfo=ZoneInfo('America/Los_Angeles'))
        self.scheduler = Scheduler(self.root, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, name='morning', **timing):
        return self.scheduler.dispatch('add', name=name, task='Research "quotes"\nNext line 中文', **timing)

    def test_cron_weekday_due_and_reload_and_persistent_dedup(self):
        self.add(cron='0 8 * * 1-5')
        self.assertEqual(self.scheduler.due(), [])
        self.now += timedelta(minutes=1)
        due = self.scheduler.due()
        self.assertEqual([j.name for j, _ in due], ['morning'])
        self.assertEqual(due[0][1].tzname(), 'PDT')
        self.assertEqual(self.scheduler.due(), [])
        restarted = Scheduler(self.root, clock=lambda: self.now)
        self.assertEqual(restarted.due(), [])
        self.assertIn('morning', restarted.next_job())
        self.assertIn('2026-11-02 08:00 PST', restarted.next_job())

    def test_half_hourly_rejected_and_hourly_accepted(self):
        for expr in ['*/30 * * * *', '* * * * *', '0,59 * * * *']:
            with self.subTest(expr=expr), self.assertRaisesRegex(ValueError, 'hourly'):
                self.add(cron=expr)
        self.add(cron='0 * * * *')
        self.assertEqual(len(self.scheduler.dispatch('list')['jobs']), 1)

    def test_invalid_fields_and_duplicate_names_and_max_jobs(self):
        for timing in [{'every': '30m'}, {'every': '0h'}, {'cron': '0 0 31 2 *'},
                       {'cron': '0 8 * * * *'}, {'cron': '0 wrong * * *'},
                       {'cron': '0 8 * * *', 'every': '1h'}, {}]:
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                self.add(**timing)
        for i in range(10):
            self.add(name=f'job{i}', every='2h')
        before = self.scheduler.path.read_text()
        with self.assertRaises(ValueError):
            self.add(name='eleventh', every='2h')
        self.assertEqual(before, self.scheduler.path.read_text())
        with self.assertRaises(ValueError):
            self.add(name='job1', every='2h')

    def test_toml_roundtrip_and_remove(self):
        self.add(every='1d')
        listed = self.scheduler.dispatch('list')['jobs']
        self.assertEqual(listed[0]['task'], 'Research "quotes"\nNext line 中文')
        self.scheduler.dispatch('remove', name='morning')
        self.assertEqual(self.scheduler.dispatch('list')['jobs'], [])
        with self.assertRaises(ValueError):
            self.scheduler.dispatch('remove', name='missing')

    def test_every_is_anchored_and_missed_occurrences_not_caught_up(self):
        self.add(every='2h')
        self.assertEqual(self.scheduler.due(), [])
        self.now += timedelta(hours=2)
        self.assertEqual(len(self.scheduler.due()), 1)
        self.now += timedelta(hours=4, minutes=1)
        restarted = Scheduler(self.root, clock=lambda: self.now)
        self.assertEqual(restarted.due(), [])
        self.assertIn('15:59', restarted.next_job())
        self.now = self.now.replace(hour=15, minute=59)
        self.assertEqual(len(restarted.due()), 1)

    def test_pause_skips_occurrence_even_if_resumed_same_minute(self):
        self.add(cron='0 8 * * 1-5')
        self.now += timedelta(minutes=1)
        self.assertEqual(self.scheduler.due(paused=True), [])
        self.assertEqual(self.scheduler.due(paused=False), [])
        self.now += timedelta(days=3)
        self.assertEqual(len(self.scheduler.due()), 1)

    def test_no_cron_catchup_on_late_restart(self):
        self.add(cron='0 8 * * 1-5')
        self.scheduler.due()
        self.now += timedelta(minutes=3)
        self.assertEqual(Scheduler(self.root, clock=lambda: self.now).due(), [])

    def test_hot_reload_does_not_rerun_same_name_within_hour(self):
        self.add(cron='0 8 * * *')
        self.now += timedelta(minutes=1)
        self.assertEqual(len(self.scheduler.due()), 1)
        self.scheduler.dispatch('remove', name='morning')
        self.add(cron='5 8 * * *')
        self.now += timedelta(minutes=5)
        self.assertEqual(self.scheduler.due(), [])

    def test_state_write_failure_never_emits_or_commits_in_memory(self):
        self.add(cron='0 8 * * *')
        self.now += timedelta(minutes=1)
        with patch('scheduling.atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.scheduler.due()
        self.assertEqual(self.scheduler.state, {})
        self.assertEqual(len(self.scheduler.due()), 1)

    def test_malformed_config_or_state_fails_closed(self):
        self.scheduler.path.write_text('[[job]\n')
        with self.assertRaisesRegex(ValueError, 'syntax'):
            self.scheduler.due()
        self.scheduler.state_path.parent.mkdir(exist_ok=True)
        self.scheduler.state_path.write_text('{"job": {"timing":"x", "last_slot":NaN}}')
        with self.assertRaisesRegex(ValueError, 'state/schedule.json'):
            Scheduler(self.root)

    def test_concurrent_pollers_get_one_occurrence(self):
        self.add(cron='0 8 * * *')
        self.now += timedelta(minutes=1)
        found = []
        threads = [threading.Thread(target=lambda: found.extend(self.scheduler.due())) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(found), 1)

    def test_watcher_enqueues_labelled_event_without_model_and_pause_skips(self):
        self.add(cron='0 8 * * *')
        app = AlwaysOn(Runtime(), self.root, agent=fake_agent(), channel=Mock())
        app.scheduler.clock = lambda: self.now
        self.now += timedelta(minutes=1)
        with patch('agent.complete') as model:
            app.schedule_once()
            event = app.events.get_nowait()
            self.assertEqual(event.kind, 'schedule')
            self.assertIn('schedule morning', event.as_text())
            self.assertIn('PDT', event.as_text())
            model.assert_not_called()
            self.now += timedelta(days=1)
            app.paused = True
            app.schedule_once()
            self.assertTrue(app.events.empty())

    def test_tool_list_free_but_add_and_remove_require_approval(self):
        rt = FakeRuntime()
        agent = Agent(self.root, rt, scheduler=self.scheduler)
        approvals = []
        with patch('agent.complete', side_effect=[
                completion([call('schedule', {'action': 'list'})]), completion(content='listed')]):
            agent.ask('list jobs', lambda x: approvals.append(x) or False, lambda *a: None)
        self.assertEqual(approvals, [])
        for action in ['add', 'remove']:
            args = {'action': action, 'name': 'test'}
            if action == 'add':
                args.update(every='1h', task='test')
            with patch('agent.complete', side_effect=[completion([call('schedule', args)]), completion(content='denied')]):
                agent.ask('change jobs', lambda x: approvals.append(x) or False, lambda *a: None)
        self.assertEqual(len(approvals), 2)
        self.assertFalse(self.scheduler.path.exists())
        with patch('agent.complete', side_effect=[completion([call('schedule',
                {'action': 'add', 'name': 'test', 'every': '1h', 'task': 'test'})]), completion(content='added')]):
            agent.ask('add it', lambda _: True, lambda *a: None)
        self.assertEqual(len(self.scheduler.dispatch('list')['jobs']), 1)
        self.assertEqual(rt.calls, [])  # Internal tool, no MCP dispatch.
        child = Agent(self.root, rt, agent_id='sa-1', scheduler=self.scheduler)
        self.assertNotIn('schedule', [t['function']['name'] for t in child.tools])
        agent.close()
        child.close()


if __name__ == '__main__':
    unittest.main()
