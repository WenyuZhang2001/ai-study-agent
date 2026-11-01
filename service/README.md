# macOS user service

`com.wenyuzhang.ai-study-agent.plist` is generated for this Mac by service_control.py. It has absolute paths and contains no credentials. The agent loads its existing local .env; settings.json, limits.toml, schedule.toml and plugin manifests are preserved.

From the harness root:

```sh
.venv/bin/python service_control.py status
.venv/bin/python service_control.py stop
.venv/bin/python service_control.py start
```

Install/regenerate initially with `service_control.py install` / `generate`. Install refuses to overwrite a differing existing LaunchAgent. The installed file is under ~/Library/LaunchAgents/. Start enables and bootstraps this label; stop disables and unloads it, preventing KeepAlive or a later login from silently undoing an explicit stop. Native fallback commands are `launchctl disable gui/$(id -u)/com.wenyuzhang.ai-study-agent` and `launchctl bootout gui/$(id -u)/com.wenyuzhang.ai-study-agent`.

Telegram /stop only cancels current agent work. /pause holds future events; neither unloads this background service. To stop the background process and release its caffeinate assertions, use the service stop command.

The user LaunchAgent runs in the logged-in user session, starts at login, and KeepAlive restarts it after exits. It uses the project venv Python and uv, an explicit PATH/CA trust store, a private umask and private stdout/stderr files under logs/. SIGTERM requests normal harness shutdown. launchd also cleans the job's process group; separately sessioned approved commands are recovered using the existing verified orphan logic.

caffeinate -i -s runs for the service lifetime. On AC it prevents idle/system sleep; display sleep is not blocked. Existing pmset settings are not changed. Stay connected to power and keep the user session logged in; locking the screen is fine.

Live validation: intentional idle SIGKILL changed receiver PID 86442 to 86593, launchd runs 1 to 2, ready again in 11.391 seconds without a manual start. No model call occurred during that idle restart. The new receiver's caffeinate child held both assertions. A BrokenPipeError from the old Telegram subprocess was captured during the intentional pipe teardown; it is not hidden, and the replacement had no polling conflict/error. All 189 regressions passed, including isolated SIGTERM cleanup. Owner Telegram status/reply confirmation is pending. This is service acceptance work, not an eight-hour overnight result.
