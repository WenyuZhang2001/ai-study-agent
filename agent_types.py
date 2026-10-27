"""Load explicitly scoped agent definitions; no model calls or subprocesses."""
from dataclasses import dataclass
import copy
from pathlib import Path
import re

import yaml

FORBIDDEN = {'delegate', 'subagent'}


@dataclass(frozen=True)
class AgentType:
    name: str
    description: str
    tools: tuple[str, ...]
    system_prompt: str
    max_turns: int = 20
    model: str | None = None

    def create(self, *, agent_id, runtime, workdir, log_path=None):
        from agent import Agent, BASH
        schemas = {t['function']['name']: t for t in [BASH, *runtime.tools]}
        selected = [copy.deepcopy(schemas[name]) for name in self.tools if name not in FORBIDDEN]
        agent = Agent(workdir=workdir, runtime=runtime, agent_id=agent_id,
                      agent_type=self.name, system_prompt=self.system_prompt,
                      tools=selected, max_turns=self.max_turns, log_path=log_path)
        agent.model = self.model
        return agent


def load_agent_types(directory, tools):
    """Missing tools means all plugin tools plus bash; an empty list means none."""
    available = {t['function']['name'] for t in tools}
    result = {}
    for path in sorted(Path(directory).glob('*.md')):
        def error(message):
            return ValueError(f'{path}: {message}')
        text = path.read_text()
        if not text.startswith('---\n') or '\n---\n' not in text[4:]:
            raise error('expected YAML frontmatter followed by a system prompt.')
        front, body = text[4:].split('\n---\n', 1)
        try:
            data = yaml.safe_load(front)
        except yaml.YAMLError:
            raise error('invalid YAML frontmatter.') from None
        if not isinstance(data, dict):
            raise error('frontmatter must be a mapping.')
        name, description = data.get('name'), data.get('description')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
            raise error('name must contain 1–64 letters, digits, underscores or hyphens.')
        if name in result:
            raise error(f'duplicate agent name {name}.')
        if not isinstance(description, str) or not description.strip() or not body.strip():
            raise error('description and system prompt must not be empty.')
        selected = data.get('tools', sorted(available))
        if isinstance(selected, str):
            selected = [name.strip() for name in selected.split(',') if name.strip()]
        if not isinstance(selected, list) or not all(isinstance(n, str) for n in selected):
            raise error('tools must be a comma-separated string or YAML list.')
        selected = tuple(dict.fromkeys(n for n in selected if n not in FORBIDDEN))
        for tool in selected:
            if tool not in available:
                raise error(f'unknown tool {tool}.')
        turns = data.get('max_turns', 20)
        if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
            raise error('max_turns must be a positive integer.')
        model = data.get('model')
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise error('model must be a nonempty model name.')
        result[name] = AgentType(name, description.strip(), selected, body.strip(), turns, model)
    return result


def agent_type_prompt(types):
    if not types:
        return ''
    return 'Available subagent types (names and descriptions only; use delegate with a complete task):\n' + '\n'.join(
        f'- {kind.name}: {kind.description}' for kind in types.values())
