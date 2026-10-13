"""The agent loop: model -> approved bash -> tool result -> model."""
import json
import os
import signal
import subprocess
from api import ROOT, complete

BASH = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the working folder after user approval. Uses bash on macOS/Linux and PowerShell on Windows. Returns stdout, stderr and exit code. Commands time out after 60 seconds; long output is truncated.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


def run_bash(command, workdir, timeout=60):
    shell = ["powershell", "-NoProfile", "-Command"] if os.name == "nt" else ["bash", "-lc"]
    # Temporary files avoid keeping unlimited command output in memory.
    import tempfile
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            shell + [command], cwd=workdir, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        def tail(file):
            size = file.seek(0, 2)
            file.seek(max(0, size - 5000))
            text = file.read().decode("utf-8", errors="replace")
            return ("[Earlier output truncated]\n" if size > 5000 else "") + text
        return {"stdout": tail(stdout), "stderr": tail(stderr),
                "exit_code": process.returncode, "timed_out": timed_out}


class Agent:
    def __init__(self, workdir=ROOT):
        self.workdir = str(workdir)
        self.messages = [{"role": "system", "content": (ROOT / "system_prompt.md").read_text()}]

    def ask(self, prompt, approve, emit, max_rounds=20):
        """approve(command) -> bool; emit(kind, data) sends interface events."""
        self.messages.append({"role": "user", "content": prompt})
        for round_index in range(max_rounds + 1):
            # Every call sends the entire conversation plus our single tool.
            result = complete(self.messages, tools=[BASH],
                              tool_choice="none" if round_index == max_rounds else "auto")
            emit("usage", result.get("usage", {}))
            message = result["choices"][0]["message"]
            assistant = {"role": "assistant", "content": message.get("content")}
            calls = message.get("tool_calls", [])
            if calls:
                assistant["tool_calls"] = calls
            self.messages.append(assistant)
            if message.get("content"):
                emit("text", message["content"])
            if not calls:
                return
            for call in calls:
                try:
                    arguments = json.loads(call["function"]["arguments"])
                    command = arguments["command"]
                    if call["function"]["name"] != "bash" or not isinstance(command, str):
                        raise ValueError("Invalid bash call")
                    if round_index == max_rounds:
                        output = {"error": "Tool round limit reached."}
                    elif approve(command):
                        output = run_bash(command, self.workdir)
                    else:
                        output = {"error": "The user declined to run this command."}
                except (ValueError, KeyError, TypeError, OSError) as error:
                    output = {"error": str(error)}
                emit("tool_result", output)
                self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                      "content": json.dumps(output)})
        emit("text", "Tool round limit reached. Please start a new question.")
