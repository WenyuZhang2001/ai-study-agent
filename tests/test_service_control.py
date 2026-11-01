import os
from pathlib import Path
import plistlib
import tempfile
import sys
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

from recovery import ReceiverLease
from service_control import Service, build_plist, LABEL


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='service paths ')
        self.root = Path(self.temp.name) / 'harness'
        (self.root / '.venv' / 'bin').mkdir(parents=True)
        for path in (self.root / 'server.py', self.root / 'ca.pem', self.root / '.venv/bin/python', self.root / '.venv/bin/uv'):
            path.write_text('fixture')
        self.python = self.root / '.venv/bin/python'
        self.ca = self.root / 'ca.pem'
        self.home = Path(self.temp.name) / 'home'
        self.service = Service(self.root, self.home)

    def tearDown(self):
        self.temp.cleanup()

    def test_absolute_args_and_venv_path_are_not_shell_expanded_or_resolved_away(self):
        self.python.unlink()
        self.python.symlink_to(sys.executable)
        config = build_plist(self.root, self.python, self.ca)
        args = config['ProgramArguments']
        self.assertEqual(args[:3], ['/usr/bin/caffeinate', '-i', '-s'])
        self.assertEqual(args[3], str(self.python))
        self.assertEqual(args[-2:], [str((self.root / 'server.py').resolve()), '--always-on'])
        self.assertEqual(config['WorkingDirectory'], str(self.root.resolve()))
        self.assertTrue(config['KeepAlive']); self.assertTrue(config['RunAtLoad'])
        self.assertEqual(config['Umask'], 0o077)
        self.assertFalse(config['AbandonProcessGroup'])
        self.assertEqual(config['ThrottleInterval'], 10)

    def test_generated_plist_has_no_credential_environment(self):
        with patch.dict(os.environ, {'API_KEY': 'UNIT_SECRET_DO_NOT_COPY', 'TELEGRAM_BOT_TOKEN': 'UNIT_TOKEN'}):
            data = self.service.generate(self.python, self.ca)
        self.assertNotIn(b'UNIT_SECRET', data); self.assertNotIn(b'UNIT_TOKEN', data)
        config = plistlib.loads(data)
        self.assertEqual(set(config['EnvironmentVariables']), {'HOME', 'PATH', 'SSL_CERT_FILE', 'PYTHONUNBUFFERED'})
        self.assertEqual(self.service.source.stat().st_mode & 0o777, 0o600)

    def test_missing_runtime_file_fails_before_install(self):
        self.ca.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing local service runtime'):
            self.service.generate(self.python, self.ca)
        self.assertFalse(self.service.destination.exists())

    def test_existing_different_launchagent_is_never_overwritten(self):
        path = self.service.destination
        path.parent.mkdir(parents=True); path.write_bytes(b'original local configuration')
        with self.assertRaisesRegex(ValueError, 'not overwritten'):
            self.service.install(self.python, self.ca)
        self.assertEqual(path.read_bytes(), b'original local configuration')

    def test_manual_receiver_lease_blocks_install_before_bootstrap(self):
        lease = ReceiverLease(self.root)
        try:
            with patch.object(self.service, 'status', return_value={'loaded': False}), \
                    patch.object(self.service, 'launchctl') as launchctl:
                with self.assertRaisesRegex(RuntimeError, 'Another harness receiver'):
                    self.service.install(self.python, self.ca)
                launchctl.assert_not_called()
        finally:
            lease.close()

    def test_install_preserves_env_and_uses_bootstrap_with_private_logs(self):
        private = self.root / '.env'; private.write_text('API_KEY=private fixture')
        with patch.object(self.service, 'status', return_value={'loaded': False}), patch.object(self.service, 'launchctl') as lc:
            self.service.install(self.python, self.ca)
        self.assertEqual(private.read_text(), 'API_KEY=private fixture')
        self.assertEqual(self.service.destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'logs/launchd.stderr.log').stat().st_mode & 0o777, 0o600)
        self.assertIn(('bootstrap', f'gui/{os.getuid()}', str(self.service.destination)), [c.args for c in lc.call_args_list])

    def test_stop_disables_and_unloads_only_its_own_label(self):
        with patch.object(self.service, 'status', return_value={'loaded': True}), patch.object(self.service, 'launchctl') as lc:
            self.service.stop()
        self.assertEqual([c.args for c in lc.call_args_list], [('disable', self.service.target), ('bootout', self.service.target)])

    @unittest.skipIf(os.name == 'nt', 'POSIX SIGTERM integration test')
    def test_sigterm_closes_runtime_and_releases_receiver_lease(self):
        repo = Path(__file__).resolve().parents[1]
        ready, closed = self.root / 'ready', self.root / 'runtime.closed'
        script = f'''import sys,time,types
from pathlib import Path
sys.path.insert(0, {str(repo)!r})
import server,always_on
from agent import Agent
server.ROOT=Path({str(self.root)!r})
server.agent=Agent(server.ROOT)
class Runtime:
    def __init__(self, **kw): pass
    def close(self): Path({str(closed)!r}).write_text('closed')
sys.modules['plugins']=types.SimpleNamespace(PluginRuntime=Runtime)
def run(rt):
    Path({str(ready)!r}).write_text('ready')
    while True: time.sleep(.05)
always_on.run_always_on=run
sys.argv=['server.py','--always-on']
server.main()
'''
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+5
            while not ready.exists() and time.monotonic()<deadline: time.sleep(.02)
            self.assertTrue(ready.exists())
            process.terminate(); process.wait(5)
            self.assertEqual(process.returncode, 0)
            self.assertTrue(closed.exists())
            lease = ReceiverLease(self.root); lease.close()
        finally:
            if process.poll() is None: process.kill()
            process.wait(3)
            if process.stderr: process.stderr.close()


if __name__ == '__main__':
    unittest.main()
