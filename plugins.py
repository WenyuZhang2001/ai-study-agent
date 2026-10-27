"""One managed MCP lifecycle thread; synchronous adapters for the existing loop/UI."""
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import re
import threading

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from api import ROOT, local_environment

NAME = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
VARIABLE = re.compile(r'\$\{([^}]+)\}')


def expand(value, variables):
    def replace(match):
        key = match.group(1)
        if key not in variables or not variables[key]:
            raise ValueError(f'Missing environment variable {key}; configure it locally.')
        return variables[key]
    return VARIABLE.sub(replace, value)


def skill_summary(path):
    text = path.read_text()
    if not text.startswith('---\n') or '\n---' not in text[4:]:
        raise ValueError(f'Missing skill frontmatter: {path}')
    fields = {}
    for line in text.split('---', 2)[1].splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip()] = value.strip().strip('\"\'')
    if not fields.get('name') or not fields.get('description'):
        raise ValueError(f'Skill requires name and description: {path}')
    return {'name': fields['name'], 'description': fields['description'], 'path': str(path.resolve())}


class PluginRuntime:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.tools, self.skills, self.plugins, self.routes = [], [], [], {}
        self.environment = local_environment(self.root)
        settings = self.root / 'settings.json'
        config = json.loads(settings.read_text()) if settings.exists() else {'allow': []}
        allow = config.get('allow', [])
        if not isinstance(allow, list) or not all(isinstance(n, str) and NAME.fullmatch(n) for n in allow):
            raise ValueError('settings.json allow must be an explicit list of tool names.')
        if 'bash' in allow:
            raise ValueError('bash must always require approval; remove it from allow.')
        self.allow = frozenset(allow)
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._thread_main, name='mcp-runtime', daemon=True)
        self.thread.start()
        if not self.ready.wait(180):
            raise RuntimeError('Plugin startup timed out; check local configuration and dependencies.')
        if self.error:
            raise RuntimeError(self.error)
        from agent import BASH
        from agent_types import load_agent_types
        try:
            self.agent_types = load_agent_types(self.root / 'agents', [BASH, *self.tools])
        except ValueError:
            self.close()
            raise
        print(json.dumps({'plugins': self.plugins, 'tools': [t['function']['name'] for t in self.tools],
                          'skills': self.skills}, ensure_ascii=False), flush=True)

    def redact(self, text):
        text = str(text)
        for key, value in self.environment.items():
            if any(word in key.upper() for word in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD')) and len(value) >= 6:
                text = text.replace(value, '[REDACTED]')
        return text

    def _thread_main(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            # Never stringify a subprocess exception that may contain env values.
            self.error = 'MCP plugin startup or lifecycle failed; check local settings and server configuration.'
        finally:
            self.ready.set()

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        async with AsyncExitStack() as stack:
            for manifest in sorted((self.root / 'plugins').glob('*/.claude-plugin/plugin.json')):
                plugin = manifest.parent.parent.resolve()
                metadata = json.loads(manifest.read_text())
                self.plugins.append(metadata['name'])
                self.skills.extend(skill_summary(p) for p in sorted(plugin.glob('skills/*/SKILL.md')))
                servers = json.loads((plugin / '.mcp.json').read_text()).get('mcpServers', {})
                variables = {**self.environment, 'CLAUDE_PLUGIN_ROOT': str(plugin)}
                for server_name, config in servers.items():
                    if not NAME.fullmatch(server_name):
                        raise ValueError('Invalid MCP server name.')
                    # Only basic process environment plus explicitly declared variables.
                    child_env = {k: v for k, v in os.environ.items() if k in {
                        'PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'TEMP', 'TMP', 'TMPDIR',
                        'LANG', 'LC_ALL', 'UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR'}}
                    child_env.update({k: expand(v, variables) for k, v in config.get('env', {}).items()})
                    params = StdioServerParameters(command=expand(config['command'], variables),
                        args=[expand(arg, variables) for arg in config.get('args', [])],
                        env=child_env, cwd=plugin)
                    client = await stack.enter_async_context(Client(params, read_timeout_seconds=240))
                    cursor = None
                    while True:
                        page = await client.list_tools(cursor=cursor)
                        for tool in page.tools:
                            name = f'{server_name}__{tool.name}'
                            if not NAME.fullmatch(name) or name in self.routes:
                                raise ValueError('Invalid or duplicate MCP tool name.')
                            self.routes[name] = (client, tool.name)
                            self.tools.append({'type': 'function', 'function': {'name': name,
                                'description': tool.description or name, 'parameters': tool.input_schema}})
                        cursor = page.next_cursor
                        if cursor is None:
                            break
            if self.allow - self.routes.keys():
                raise ValueError('An allowlisted tool was not loaded; check settings.json.')
            self.ready.set()
            self.pending_calls = set()
            while True:
                name, arguments, future = await self.queue.get()
                if name is None:
                    for task in list(self.pending_calls):
                        task.cancel()
                    if self.pending_calls:
                        await asyncio.gather(*self.pending_calls,return_exceptions=True)
                    future.set_result(None)
                    break
                task = asyncio.create_task(self._dispatch(name,arguments,future))
                self.pending_calls.add(task)
                task.add_done_callback(self.pending_calls.discard)

    async def _dispatch(self, name, arguments, future):
        try:
            if name not in self.routes:
                result = {'is_error':True,'text':'Unknown MCP tool.'}
            else:
                client, remote = self.routes[name]
                response = await client.call_tool(remote, arguments)
                result = {'is_error':response.is_error,
                          'text':self.redact('\n'.join(b.text for b in response.content if getattr(b,'type',None)=='text'))}
            if not future.done():
                future.set_result(result)
        except asyncio.CancelledError:
            if not future.done():
                future.set_result({'is_error':True,'text':'MCP call cancelled; no automatic retry. Delivery of side effects may be uncertain.'})
            raise
        except Exception:
            if not future.done():
                future.set_result({'is_error':True,'text':'MCP call failed; check server. Do not automatically retry side effects.'})

    async def _request(self, name, arguments):
        future = self.loop.create_future()
        await self.queue.put((name,arguments,future))
        return await future

    def call(self, name, arguments):
        if not self.thread.is_alive():
            raise RuntimeError('MCP runtime is not running.')
        future = asyncio.run_coroutine_threadsafe(self._request(name, arguments), self.loop)
        return future.result(timeout=300)

    def skill_prompt(self):
        lines = ['Available plugin skills (metadata only). To use a skill, read its SKILL.md with bash first:']
        lines += [f"- {s['name']}: {s['description']} ({s['path']})" for s in self.skills]
        return '\n'.join(lines)

    def close(self):
        if self.thread.is_alive():
            asyncio.run_coroutine_threadsafe(self._request(None, {}), self.loop).result(timeout=300)
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError('MCP servers did not close cleanly.')
