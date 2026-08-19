#!/usr/bin/env python3
"""Fetch and parse repository cards from GitHub Trending."""

import argparse
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://github.com/trending"
USER_AGENT = "Pachinko-GitHub-Trending/1.0"
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def clean_text(parts) -> str:
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def parse_count(value):
    if not value:
        return None
    match = re.search(r"([0-9][0-9,]*)", value)
    return int(match.group(1).replace(",", "")) if match else None


def repo_name_from_href(href):
    if not href:
        return None
    parsed = urllib.parse.urlsplit(html.unescape(href))
    if parsed.netloc and parsed.netloc.lower() != "github.com":
        return None
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() in {"login", "trending"}:
        return None
    return f"{parts[0]}/{parts[1]}"


class TrendingParser(HTMLParser):
    """Small purpose-built parser for GitHub's server-rendered Trending page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.language_options = {}
        self.spoken_options = {}
        self.saw_results_container = False
        self.saw_empty_state = False
        self.repositories_selected = False
        self.developers_selected = False
        self.title_parts = []
        self.page_text_parts = []
        self.selected_filters = {}

        self._details_stack = []
        self._menu = None
        self._link = None
        self._summary = None
        self._summary_depth = 0
        self._in_title = False
        self._card = None
        self._in_h2 = False
        self._capture = None
        self._capture_depth = 0

    @staticmethod
    def _attrs(attrs):
        return {key: value or "" for key, value in attrs}

    @staticmethod
    def _classes(attrs):
        return set(attrs.get("class", "").split())

    def _start_capture(self, name: str, tag: str):
        self._capture = {"name": name, "tag": tag, "parts": []}
        self._capture_depth = 1

    def handle_starttag(self, tag, raw_attrs):
        attrs = self._attrs(raw_attrs)
        classes = self._classes(attrs)

        if self._capture is not None:
            self._capture_depth += 1
        if self._summary is not None:
            self._summary_depth += 1

        if tag == "title":
            self._in_title = True

        if tag == "details":
            self._details_stack.append(self._menu)
            detail_id = attrs.get("id")
            if detail_id == "select-menu-language":
                self._menu = "language"
            elif detail_id == "select-menu-spoken-language":
                self._menu = "spoken"
            elif detail_id == "select-menu-date":
                self._menu = "date"

        if tag == "summary" and self._menu in {"language", "spoken", "date"}:
            self._summary = {"menu": self._menu, "parts": []}
            self._summary_depth = 1

        if tag == "div" and "data-hpc" in attrs:
            self.saw_results_container = True

        if tag == "a":
            href = html.unescape(attrs.get("href", ""))
            selected = attrs.get("aria-current") == "page" or "selected" in classes
            path = urllib.parse.urlsplit(href).path.rstrip("/")
            if selected and path == "/trending":
                self.repositories_selected = True
            if selected and path == "/trending/developers":
                self.developers_selected = True

            if self._menu in {"language", "spoken"}:
                self._link = {"menu": self._menu, "href": href, "parts": []}

        if tag == "article" and "Box-row" in classes and self._card is None:
            self._card = {
                "full_name": None,
                "description": None,
                "language": None,
                "total_stars": None,
                "forks": None,
                "period_stars": None,
                "period_label": None,
                "contributors": [],
            }
            return

        if self._card is None:
            return

        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and self._card["full_name"] is None:
            full_name = repo_name_from_href(attrs.get("href"))
            if full_name:
                self._card["full_name"] = full_name
        elif tag == "p" and self._capture is None:
            self._start_capture("description", tag)
        elif tag == "span" and attrs.get("itemprop") == "programmingLanguage":
            self._start_capture("language", tag)
        elif tag == "a" and attrs.get("href", "").endswith("/stargazers"):
            self._start_capture("total_stars", tag)
        elif tag == "a" and attrs.get("href", "").endswith("/forks"):
            self._start_capture("forks", tag)
        elif tag == "span" and {"d-inline-block", "float-sm-right"}.issubset(classes):
            self._start_capture("period", tag)
        elif tag == "img" and "avatar-user" in classes:
            login = attrs.get("alt", "").lstrip("@").strip()
            if login and login not in {item["login"] for item in self._card["contributors"]}:
                self._card["contributors"].append({
                    "login": login,
                    "url": f"https://github.com/{urllib.parse.quote(login, safe='')}",
                    "avatar_url": html.unescape(attrs.get("src", "")),
                })

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        self.page_text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._link is not None:
            self._link["parts"].append(data)
        if self._summary is not None:
            self._summary["parts"].append(data)
        if self._capture is not None:
            self._capture["parts"].append(data)

    def handle_endtag(self, tag):
        if self._capture is not None:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                self._finish_capture()

        if self._summary is not None:
            self._summary_depth -= 1
            if self._summary_depth == 0:
                text = clean_text(self._summary["parts"])
                selected = text.split(":", 1)[-1].strip()
                self.selected_filters[self._summary["menu"]] = selected
                self._summary = None

        if tag == "a" and self._link is not None:
            self._finish_menu_link()

        if tag == "h2":
            self._in_h2 = False
        elif tag == "article" and self._card is not None:
            self._finish_card()
        elif tag == "details":
            self._menu = self._details_stack.pop() if self._details_stack else None
        elif tag == "title":
            self._in_title = False

    def _finish_capture(self):
        capture = self._capture
        self._capture = None
        text = clean_text(capture["parts"])
        name = capture["name"]
        if name in {"description", "language"}:
            self._card[name] = text or None
        elif name in {"total_stars", "forks"}:
            self._card[name] = parse_count(text)
        elif name == "period":
            self._card["period_stars"] = parse_count(text)
            self._card["period_label"] = text or None

    def _finish_menu_link(self):
        link = self._link
        self._link = None
        label = clean_text(link["parts"])
        parsed = urllib.parse.urlsplit(link["href"])
        if link["menu"] == "spoken":
            code = urllib.parse.parse_qs(parsed.query).get("spoken_language_code", [None])[0]
            if label and code:
                self.spoken_options[label.casefold()] = {
                    "label": label,
                    "code": code.lower(),
                }
                self.spoken_options[code.casefold()] = self.spoken_options[label.casefold()]
        elif link["menu"] == "language":
            parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
            if label and len(parts) == 2 and parts[0] == "trending":
                option = {"label": label, "slug": parts[1]}
                self.language_options[label.casefold()] = option
                self.language_options[parts[1].casefold()] = option

    def _finish_card(self):
        card = self._card
        self._card = None
        if not card["full_name"]:
            raise ValueError("a GitHub Trending repository card has no repository link")
        card["id"] = card["full_name"].casefold()
        card["url"] = f"https://github.com/{card['full_name']}"
        card["rank"] = len(self.cards) + 1
        self.cards.append(card)

    @property
    def title(self):
        return clean_text(self.title_parts)

    @property
    def page_text(self):
        return clean_text(self.page_text_parts)


def parse_trending_page(source: str) -> TrendingParser:
    parser = TrendingParser()
    parser.feed(source)
    parser.close()
    if parser._card is not None:
        raise ValueError("GitHub returned an incomplete repository card")
    normalized_page = parser.page_text.casefold()
    parser.saw_empty_state = (
        "no trending repositories" in normalized_page
        or "aren’t any trending repositories" in normalized_page
        or "don’t have any trending repositories" in normalized_page
    )
    if parser.developers_selected or not parser.repositories_selected:
        raise ValueError("GitHub did not return the Trending repositories view")
    if not parser.saw_results_container:
        raise ValueError("GitHub returned no Trending results container")
    ids = [card["id"] for card in parser.cards]
    if len(ids) != len(set(ids)):
        raise ValueError("GitHub returned duplicate repository cards")
    if not parser.cards and not parser.saw_empty_state:
        raise ValueError("GitHub returned no repository cards and no explicit empty state")
    return parser


def resolve_option(value, options, kind):
    if value is None:
        return None
    normalized = urllib.parse.unquote(value).strip().casefold()
    option = options.get(normalized)
    if option:
        return option
    labels = sorted({item["label"] for item in options.values()}, key=str.casefold)
    suggestions = [label for label in labels if normalized in label.casefold()][:5]
    suffix = f" Similar values: {', '.join(suggestions)}." if suggestions else ""
    raise ValueError(f"unknown GitHub {kind} {value!r}.{suffix}")


def build_url(date_range, language=None, spoken=None):
    url = BASE_URL
    if language:
        slug = urllib.parse.quote(language["slug"], safe="-._~")
        url += f"/{slug}"
    query = {"since": date_range}
    if spoken:
        query["spoken_language_code"] = spoken["code"]
    return f"{url}?{urllib.parse.urlencode(query)}"


def get_text(url, tries=4, delay=1.0):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in TRANSIENT_STATUS or attempt == tries:
                raise
            retry_after = exc.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay * 2 ** (attempt - 1)
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries:
                raise
            wait = delay * 2 ** (attempt - 1)
        time.sleep(min(wait, 30.0))
    raise RuntimeError("unreachable")


def fetch(date_range, language_value, spoken_value, getter=get_text):
    discovery_url = build_url("daily")
    discovery_html = getter(discovery_url)
    discovery = parse_trending_page(discovery_html)
    language = resolve_option(language_value, discovery.language_options, "programming language")
    spoken = resolve_option(spoken_value, discovery.spoken_options, "spoken language")
    requested_url = build_url(date_range, language, spoken)

    if requested_url == discovery_url:
        parsed = discovery
    else:
        parsed = parse_trending_page(getter(requested_url))

    expected_period = {"daily": "today", "weekly": "this week", "monthly": "this month"}[date_range]
    expected_filters = {
        "date": expected_period,
        "language": (language or {}).get("label", "Any"),
        "spoken": (spoken or {}).get("label", "Any"),
    }
    for filter_name, expected in expected_filters.items():
        actual = parsed.selected_filters.get(filter_name)
        if not actual or actual.casefold() != expected.casefold():
            raise ValueError(
                f"GitHub returned {filter_name} filter {actual!r}; expected {expected!r}"
            )
    for card in parsed.cards:
        period = (card.get("period_label") or "").casefold()
        if expected_period not in period:
            raise ValueError(
                f"repository {card['full_name']} lacks the expected {expected_period!r} star count"
            )

    return {
        "source": requested_url,
        "scope": requested_url,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "filters": {
            "date_range": date_range,
            "language": language,
            "spoken_language": spoken,
        },
        "result_count": len(parsed.cards),
        "documents": parsed.cards,
    }


def main():
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--date-range", required=True, choices=["daily", "weekly", "monthly"])
    argument_parser.add_argument("--language")
    argument_parser.add_argument("--spoken-language")
    argument_parser.add_argument("--out", required=True)
    args = argument_parser.parse_args()

    try:
        manifest = fetch(args.date_range, args.language, args.spoken_language)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"GitHub Trending fetch failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2, ensure_ascii=False)
    ids_path = os.path.join(args.out, "all_ids.txt")
    with open(ids_path, "w", encoding="utf-8") as output:
        for document in manifest["documents"]:
            output.write(f"{document['id']}\n")

    filters = manifest["filters"]
    language = (filters["language"] or {}).get("label", "Any")
    spoken = (filters["spoken_language"] or {}).get("label", "Any")
    print(json.dumps({
        "source": manifest["source"],
        "date_range": filters["date_range"],
        "language": language,
        "spoken_language": spoken,
        "repositories": manifest["result_count"],
        "manifest": manifest_path,
        "ids": ids_path,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
