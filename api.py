"""One OpenAI-compatible API call, using only Python's standard library."""
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent

def load_config():
    import os
    config = {}
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                config[name.strip()] = value.strip().strip(chr(34) + chr(39))
    for name in ("BASE_URL", "API_KEY", "MODEL"):
        config[name] = os.environ.get(name, config.get(name, ""))
        if not config[name] or config[name] == "replace_with_your_private_key":
            raise RuntimeError(f"Configure {name} in .env locally.")
    return config

def complete(messages, **options):
    config = load_config()
    payload = {"model": config["MODEL"], "messages": messages, "max_tokens": 1024, **options}
    request = Request(
        config["BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + config["API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=60) as response:
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
