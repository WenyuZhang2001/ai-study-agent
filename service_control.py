"""macOS user LaunchAgent generation/control. Plists contain paths, never secrets."""
from pathlib import Path
import json
import os
import plistlib
import re
import subprocess
import sys

from recovery import ReceiverLease
from agent import process_fingerprint

LABEL = 'com.wenyuzhang.ai-study-agent'


def build_plist(root, python_path, ca_file):
    root, python_path, ca_file = Path(root).resolve(), Path(python_path).absolute(), Path(ca_file).resolve()
    for path in (python_path, ca_file, root / 'server.py', root / '.venv' / 'bin' / 'uv'):
        if not path.is_file():
            raise ValueError('Missing local service runtime path: ' + str(path))
    return {'Label': LABEL,
            'ProgramArguments': ['/usr/bin/caffeinate', '-i', '-s', str(python_path), '-u', str(root / 'server.py'), '--always-on'],
            'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True,
            'ThrottleInterval': 10, 'ExitTimeOut': 30, 'ProcessType': 'Background',
            'AbandonProcessGroup': False, 'Umask': 0o077,
            'StandardOutPath': str(root / 'logs' / 'launchd.stdout.log'),
            'StandardErrorPath': str(root / 'logs' / 'launchd.stderr.log'),
            'EnvironmentVariables': {'HOME': str(Path.home()),
                'PATH': str(root / '.venv' / 'bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                'SSL_CERT_FILE': str(ca_file), 'PYTHONUNBUFFERED': '1'}}


class Service:
    def __init__(self, root, home=None):
        self.root = Path(root).resolve()
        self.home = Path(home) if home else Path.home()
        self.target = f'gui/{os.getuid()}/{LABEL}'
        self.destination = self.home / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
        self.source = self.root / 'service' / (LABEL + '.plist')

    @staticmethod
    def launchctl(*args, check=True):
        result = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=40)
        if check and result.returncode:
            raise RuntimeError('launchctl ' + args[0] + ' failed: ' + result.stderr.strip())
        return result

    def generate(self, python_path, ca_file):
        config = build_plist(self.root, python_path, ca_file)
        self.source.parent.mkdir(parents=True, exist_ok=True)
        data = plistlib.dumps(config, sort_keys=False)
        self.source.write_bytes(data)
        self.source.chmod(0o600)
        return data

    def status(self):
        result = self.launchctl('print', self.target, check=False)
        def field(name):
            found = re.search(r'^\s*' + re.escape(name) + r' = ([^\n]+)', result.stdout, re.M)
            return found[1].strip() if found else None
        raw_pid = field('pid')
        job_pid = int(raw_pid) if raw_pid and raw_pid.isdecimal() else None
        marker = self.root / 'state' / 'receiver.json'
        receiver, ready = None, False
        if marker.exists():
            receiver = json.loads(marker.read_text())
            pid = receiver.get('pid')
            if type(pid) is int and receiver.get('fingerprint'):
                same = process_fingerprint(pid) == receiver['fingerprint']
                parent = subprocess.run(['ps', '-p', str(pid), '-o', 'ppid='],
                                        capture_output=True, text=True, timeout=3).stdout.strip()
                ready = (same and result.returncode == 0 and receiver.get('phase') == 'watching'
                         and (pid == job_pid or parent == str(job_pid)))
        return {'label': LABEL, 'loaded': result.returncode == 0, 'state': field('state'),
                'launchd_pid': job_pid, 'receiver_pid': receiver.get('pid') if receiver else None,
                'receiver_ready': bool(ready), 'runs': field('runs'), 'last_exit_code': field('last exit code'),
                'installed_plist': str(self.destination), 'generated_plist': str(self.source)}

    def install(self, python_path, ca_file):
        data = self.generate(python_path, ca_file)
        if self.destination.is_symlink() or self.destination.parent.is_symlink():
            raise ValueError('Symlinked LaunchAgent destination was not modified.')
        if self.destination.exists() and self.destination.read_bytes() != data:
            raise ValueError('Existing LaunchAgent differs; it was not overwritten. Review the two plist files first.')
        if not self.status()['loaded']:
            lease = ReceiverLease(self.root)
            lease.close()  # Launching server.py takes the lease again before any plugin starts.
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        self.destination.chmod(0o600)
        logs = self.root / 'logs'
        logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        logs.chmod(0o700)
        for name in ('launchd.stdout.log', 'launchd.stderr.log'):
            path = logs / name
            if path.is_symlink():
                raise ValueError('Symlinked service log was not modified.')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
            path.chmod(0o600)
        self.start()

    def start(self):
        if not self.destination.exists():
            raise ValueError('Install the generated LaunchAgent first.')
        self.launchctl('enable', self.target)
        if not self.status()['loaded']:
            lease = ReceiverLease(self.root); lease.close()
            self.launchctl('bootstrap', f'gui/{os.getuid()}', str(self.destination))

    def stop(self):
        # Disable as well as unload, so login/KeepAlive will not undo an explicit stop.
        self.launchctl('disable', self.target)
        if self.status()['loaded']:
            self.launchctl('bootout', self.target)


def main():
    import certifi
    root = Path(__file__).resolve().parent
    service = Service(root)
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == 'generate':
        service.generate(root / '.venv' / 'bin' / 'python', certifi.where())
        print(json.dumps({'generated_plist': str(service.source)}, indent=2))
        return
    elif action == 'install':
        service.install(root / '.venv' / 'bin' / 'python', certifi.where())
    elif action == 'start':
        service.start()
    elif action == 'stop':
        service.stop()
    elif action != 'status':
        raise SystemExit('Use generate, install, start, stop, or status.')
    print(json.dumps(service.status(), indent=2))


if __name__ == '__main__':
    main()
