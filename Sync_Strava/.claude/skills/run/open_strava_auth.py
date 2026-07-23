#!/usr/bin/env python3
"""Open an active Strava OAuth URL and wait for Claude Code to connect."""

import re
import subprocess
import sys
import time
from urllib.parse import urlparse
import webbrowser


POLL_INTERVAL_SECONDS = 2
TIMEOUT_SECONDS = 600
CONNECTED_PATTERN = re.compile(r"Status:\s*[^\w\n]*Connected\b", re.IGNORECASE)


def validate_auth_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "strava.com" or hostname.endswith(".strava.com")
    ):
        raise ValueError("authorization URL must be HTTPS on strava.com")
    return value


def get_connection_status() -> tuple[bool, str]:
    result = subprocess.run(
        [
            "claude",
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


def open_default_browser(auth_url: str) -> None:
    if webbrowser.open(auth_url, new=1, autoraise=True):
        return

    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", auth_url], check=True, timeout=30)
        return

    raise RuntimeError("the default browser could not be opened")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: open_strava_auth.py <auth_url>", file=sys.stderr)
        raise SystemExit(2)

    try:
        auth_url = validate_auth_url(sys.argv[1])
        open_default_browser(auth_url)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as error:
        print(f"Unable to open Strava authorization: {error}", file=sys.stderr)
        raise SystemExit(1)

    print("Strava authorization opened in the default browser; waiting for completion.", flush=True)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_status = ""

    while time.monotonic() < deadline:
        try:
            connected, last_status = get_connection_status()
        except (OSError, subprocess.SubprocessError) as error:
            last_status = str(error)
            connected = False

        if connected:
            print("Strava MCP authentication completed.", flush=True)
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    print("Timed out waiting for Strava MCP authentication.", file=sys.stderr)
    if last_status:
        print(last_status, file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
