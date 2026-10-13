"""Step 1: send one message and display the reply and usage."""
import sys
from api import ROOT, complete, usage_text

if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or "Explain what an agent harness is in one sentence."
    messages = [
        {"role": "system", "content": (ROOT / "system_prompt.md").read_text()},
        {"role": "user", "content": prompt},
    ]
    try:
        result = complete(messages)
        print(result["choices"][0]["message"].get("content") or "(No text returned)")
        print(usage_text(result))
    except RuntimeError as error:
        raise SystemExit(str(error))
