#!/usr/bin/env python3
"""Run Strava MCP login in macOS Terminal and wait for it to connect."""

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


POLL_INTERVAL_SECONDS = 2
TIMEOUT_SECONDS = 600
CONNECTED_PATTERN = re.compile(r"Status:\s*[^\w\n]*Connected\b", re.IGNORECASE)


def get_connection_status(claude_path: str) -> tuple[bool, str]:
    result = subprocess.run(
        [
            claude_path,
            "--settings",
            ".claude/settings.json",
            "mcp",
            "get",
            "strava",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    return result.returncode == 0 and CONNECTED_PATTERN.search(output) is not None, output


def terminal_script(
    project_dir: Path, claude_path: str, result_path: Path
) -> str:
    return "\n".join(
        [
            "#!/bin/zsh",
            f"cd {shlex.quote(str(project_dir))} || exit 1",
            f"{shlex.quote(claude_path)} --settings .claude/settings.json mcp login strava",
            "login_status=$?",
            f"printf '%s\\n' \"$login_status\" > {shlex.quote(str(result_path))}",
            "printf '\\nStrava MCP login finished (exit %s). You may close this window.\\n' \"$login_status\"",
            "exit \"$login_status\"",
            "",
        ]
    )


def read_login_result(result_path: Path) -> int | None:
    try:
        value = result_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None

    try:
        return int(value)
    except ValueError:
        return 1


def main() -> None:
    if sys.platform != "darwin":
        print("Interactive Strava login requires macOS Terminal.", file=sys.stderr)
        raise SystemExit(1)

    project_dir = Path.cwd().resolve()
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.is_file():
        print(
            "Run this helper from the Sync_Strava recipe directory; "
            ".claude/settings.json was not found.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    claude_path = shutil.which("claude")
    if claude_path is None:
        print("The claude executable was not found on PATH.", file=sys.stderr)
        raise SystemExit(1)

    try:
        connected, last_status = get_connection_status(claude_path)
    except (OSError, subprocess.SubprocessError) as error:
        connected = False
        last_status = str(error)

    if connected:
        print("Strava MCP is already connected.", flush=True)
        return

    script_fd, script_name = tempfile.mkstemp(
        prefix="sync-strava-login-", suffix=".command", dir="/tmp", text=True
    )
    os.close(script_fd)
    script_path = Path(script_name)
    result_path = script_path.with_suffix(".result")

    try:
        script_path.write_text(
            terminal_script(project_dir, claude_path, result_path),
            encoding="utf-8",
        )
        script_path.chmod(0o700)
        subprocess.run(
            ["/usr/bin/open", "-a", "Terminal", str(script_path)],
            check=True,
            timeout=30,
        )
        print(
            "Opened macOS Terminal and started "
            "`claude --settings .claude/settings.json mcp login strava`; "
            "waiting for authentication.",
            flush=True,
        )

        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                connected, last_status = get_connection_status(claude_path)
            except (OSError, subprocess.SubprocessError) as error:
                connected = False
                last_status = str(error)

            if connected:
                print("Strava MCP authentication completed.", flush=True)
                return

            login_result = read_login_result(result_path)
            if login_result is not None:
                print(
                    f"`claude mcp login strava` exited with status {login_result}, "
                    "but Strava is not connected.",
                    file=sys.stderr,
                )
                if last_status:
                    print(last_status, file=sys.stderr)
                raise SystemExit(1)

            time.sleep(POLL_INTERVAL_SECONDS)

        print("Timed out waiting for Strava MCP authentication.", file=sys.stderr)
        if last_status:
            print(last_status, file=sys.stderr)
        raise SystemExit(1)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Unable to launch interactive Strava login: {error}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        script_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
