#!/usr/bin/env python3
"""Small sequential HTTPS JSON client for the weather recipe."""

import email.utils
import json
import time
import urllib.error
import urllib.parse
import urllib.request


ALLOWED_HOSTS = {
    "api.open-meteo.com",
    "air-quality-api.open-meteo.com",
    "api.zippopotam.us",
}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MIN_INTERVAL_SECONDS = 0.11
USER_AGENT = "sync-us-weather/1.0 (Pachinko recipe; personal use)"


class HttpStatusError(RuntimeError):
    def __init__(self, code, url, body=""):
        super().__init__(f"HTTP {code} from {url}: {body[:300]}")
        self.code = code
        self.url = url


def _validate_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError(f"refusing URL outside approved weather providers: {url}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _retry_after(value):
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
    """Issue one request at a time with retry/backoff and redirect controls."""

    def __init__(self, interval=MIN_INTERVAL_SECONDS):
        self.interval = interval
        self._last_request = 0.0
        self._opener = urllib.request.build_opener(_SafeRedirect())

    def get_json(self, url, params=None, tries=4):
        _validate_url(url)
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(tries):
            wait = self._last_request + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            req = urllib.request.Request(full, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            })
            try:
                with self._opener.open(req, timeout=30) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"response too large from {url}")
                return json.loads(body)
            except urllib.error.HTTPError as exc:
                body = exc.read(300).decode("utf-8", "replace")
                if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                    raise HttpStatusError(exc.code, url, body) from exc
                delay = _retry_after(exc.headers.get("Retry-After"))
                time.sleep(min(delay, 60.0) if delay is not None else 2 ** attempt)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == tries - 1:
                    raise RuntimeError(f"network error calling {url}: {exc}") from exc
                time.sleep(2 ** attempt)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc
        raise RuntimeError("unreachable")


CLIENT = Client()
