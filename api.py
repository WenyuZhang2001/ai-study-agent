"""One OpenAI-compatible API call, using only Python's standard library."""
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent

def local_environment(root=ROOT):
    """Read literal .env values locally; never execute it or print values."""
    import os
    config = {}
    env = Path(root) / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                name, value = line.split('=', 1)
                config[name.strip()] = value.strip().strip(chr(34) + chr(39))
    return {**config, **os.environ}


def load_config():
    config = local_environment()
    for name in ('BASE_URL', 'API_KEY', 'MODEL'):
        if not config.get(name) or config[name] == 'replace_with_your_private_key':
            raise RuntimeError(f'Configure {name} in .env locally.')
    return {name: config[name] for name in ('BASE_URL', 'API_KEY', 'MODEL')}

def complete(messages, request_timeout=60, **options):
    config = load_config()
    payload = {"model": config["MODEL"], "messages": messages, "max_tokens": 1024, **options}
    request = Request(
        config["BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + config["API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=request_timeout) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"API returned HTTP {error.code}; check credentials, credits and model.") from None
    except (URLError, TimeoutError):
        raise RuntimeError("API connection failed or timed out.") from None

def usage_text(result):
    usage = result.get("usage", {})
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    text = f"Input: {usage.get('prompt_tokens', 'unknown')} | Output: {usage.get('completion_tokens', 'unknown')}"
    if cached is not None:
        text += f" | Cached input: {cached}"
    if usage.get("cost") is not None:
        text += f" | Cost: ${usage['cost']:.8f}"
    return text
