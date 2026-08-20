#!/usr/bin/env python3
"""Sequential, bounded HTTPS JSON client for FRED API Version 2."""

import email.utils
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


ALLOWED_HOST = "api.stlouisfed.org"
API_KEY_RE = re.compile(r"[a-z0-9]{32}\Z", re.ASCII)
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MIN_INTERVAL_SECONDS = 0.55
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "sync-fred-economic-indicators/1.0 (Pachinko recipe)"


def validate_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != ALLOWED_HOST:
        raise ValueError(f"refusing URL outside the approved FRED API host: {url}")
    if parsed.username or parsed.password:
        raise ValueError("refusing credentials embedded in a FRED URL")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        validate_url(new_url)
        return super().redirect_request(request, fp, code, message, headers, new_url)


def retry_after(value):
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value).timestamp()
            return max(0.0, when - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class Client:
    """Issue one FRED request at a time with bearer auth and retry/backoff."""

    def __init__(self, api_key, interval=MIN_INTERVAL_SECONDS, opener=None):
        if not API_KEY_RE.fullmatch(api_key or ""):
            raise ValueError("FRED_API_KEY has an invalid format")
        self._api_key = api_key
        self.interval = interval
        self._last_request = 0.0
        self._opener = opener or urllib.request.build_opener(SafeRedirect())

    def build_request(self, url, params=None):
        validate_url(url)
        query = urllib.parse.urlencode(params or {})
        full_url = f"{url}?{query}" if query else url
        return urllib.request.Request(
            full_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )

    def redact(self, value):
        return str(value).replace(self._api_key, "[REDACTED]")

    def get_json(self, url, params=None, tries=5):
        request = self.build_request(url, params)
        for attempt in range(tries):
            wait = self._last_request + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                with self._opener.open(request, timeout=90) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"response too large from {url}")
                return json.loads(body)
            except urllib.error.HTTPError as exc:
                body = self.redact(exc.read(500).decode("utf-8", "replace"))
                if exc.code not in TRANSIENT_STATUS or attempt == tries - 1:
                    raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
                delay = retry_after(exc.headers.get("Retry-After"))
                time.sleep(min(delay, 60.0) if delay is not None else 2 ** attempt)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == tries - 1:
                    raise RuntimeError(
                        f"network error calling {url}: {self.redact(exc)}"
                    ) from exc
                time.sleep(2 ** attempt)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc
        raise RuntimeError("unreachable")
