"""Local web interface. The model loop remains in agent.py."""
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from agent import Agent
from api import ROOT

TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
agent = Agent()
events = []
busy = False
pending = None


def emit(kind, data):
    with LOCK:
        events.append({'kind': kind, 'data': data})


def approve(command):
    global pending
    item = {'id': secrets.token_hex(16), 'command': command, 'event': threading.Event(), 'approved': False}
    with LOCK:
        pending = item
        emit('approval', {'id': item['id'], 'command': command})
    item['event'].wait()
    with LOCK:
        pending = None
    return item['approved']


def run(prompt):
    global busy
    try:
        agent.ask(prompt, approve, emit)
    except Exception:
        # Keep API response bodies and credentials out of browser errors.
        emit('error', 'Request failed. Check connectivity, API credits and configuration; start a new conversation if needed.')
    finally:
        with LOCK:
            busy = False
            emit('done', None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body, content_type='application/json'):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error': 'Invalid host'})
        route = urlparse(self.path)
        if route.path == '/':
            html = (ROOT / 'index.html').read_text().replace('__TOKEN__', TOKEN)
            return self.reply(200, html, 'text/html')
        if self.headers.get('X-Session-Token') != TOKEN:
            return self.reply(403, {'error': 'Invalid session'})
        if route.path == '/events':
            try:
                offset = max(0, int(parse_qs(route.query).get('after', ['0'])[0]))
            except ValueError:
                return self.reply(400, {'error': 'Invalid event offset'})
            with LOCK:
                return self.reply(200, {'events': events[offset:], 'next': len(events), 'busy': busy})
        return self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        global busy, agent
        if not self.allowed() or self.headers.get('X-Session-Token') != TOKEN:
            return self.reply(403, {'error': 'Invalid session'})
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            return self.reply(403, {'error': 'Invalid origin'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 32768:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            return self.reply(400, {'error': 'Invalid request'})
        with LOCK:
            if self.path == '/chat':
                prompt = body.get('message', '')
                if not isinstance(prompt, str) or not prompt.strip():
                    return self.reply(400, {'error': 'Enter a message'})
                if busy:
                    return self.reply(409, {'error': 'Wait for the current turn'})
                busy = True
                emit('user', prompt)
                threading.Thread(target=run, args=(prompt,), daemon=True).start()
            elif self.path == '/approve':
                if not pending or body.get('id') != pending['id'] or pending['event'].is_set():
                    return self.reply(409, {'error': 'This command is no longer waiting'})
                if not isinstance(body.get('approved'), bool):
                    return self.reply(400, {'error': 'Choose approve or deny'})
                pending['approved'] = body['approved']
                emit('decision', {'id': pending['id'], 'approved': pending['approved']})
                pending['event'].set()
            elif self.path == '/reset':
                if busy:
                    return self.reply(409, {'error': 'Finish or deny the current turn first'})
                agent = Agent()
                emit('reset', None)
            else:
                return self.reply(404, {'error': 'Not found'})
        self.reply(200, {'ok': True})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'AI Study Agent: {url}', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
