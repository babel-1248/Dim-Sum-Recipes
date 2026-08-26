import json
import os
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config  # noqa: E402
import convert  # noqa: E402
import feedstate  # noqa: E402
import fetch  # noqa: E402
import httpclient  # noqa: E402


FAKE_KEY = "a" * 32


def release_metadata(release_id=10, name="Consumer Price Index"):
    return {
        "release_id": release_id,
        "name": name,
        "url": "https://www.bls.gov/cpi/",
        "sources": [{"name": "U.S. Bureau of Labor Statistics", "url": "https://www.bls.gov/"}],
    }


def series_metadata(series_id, title, observations, units="Index 1982-1984=100"):
    return {
        "series_id": series_id,
        "title": title,
        "frequency": "Monthly",
        "units": units,
        "seasonal_adjustment": "Seasonally Adjusted",
        "last_updated": "2026-08-12T12:34:56Z",
        "copyright_id": "public domain: citation requested",
        "notes": "",
        "observations": observations,
    }


class ConfigurationTests(unittest.TestCase):
    def test_defaults_include_every_indicator_with_compact_history(self):
        resolved = config.load_configuration({"FRED_API_KEY": FAKE_KEY})

        self.assertEqual("defaults", resolved["filter_source"])
        self.assertEqual("13", resolved["history"])
        self.assertEqual(
            [item["label"] for item in config.INDICATORS],
            [item["label"] for item in resolved["indicators"]],
        )
        self.assertNotIn(FAKE_KEY, json.dumps(resolved))

    def test_bundled_checklist_matches_code_defaults(self):
        checklist = (
            Path(config.__file__).resolve().parents[4]
            / "fred-indicators-filter.md"
        ).read_text(encoding="utf-8")

        configured = config.parse_checklist(checklist)
        defaults = config.default_configuration()

        self.assertEqual(defaults["history"], configured["history"])
        self.assertEqual(
            [item["key"] for item in defaults["indicators"]],
            [item["key"] for item in configured["indicators"]],
        )

    def test_configured_checklist_without_history_uses_default_history(self):
        resolved = config.parse_checklist("- [x] Headline CPI")

        self.assertEqual("13", resolved["history"])

    def test_checklist_uses_checked_items_and_topmost_history_choice(self):
        markdown = """
## Inflation
- [ ] Headline CPI
- [x] Core CPI
## Labor
- [X] Initial unemployment claims
## History — pick one
- [x] Last 13 observations
- [x] Last 5 years
"""
        resolved = config.parse_checklist(markdown)

        self.assertEqual("13", resolved["history"])
        self.assertEqual(
            ["Core CPI", "Initial unemployment claims"],
            [item["label"] for item in resolved["indicators"]],
        )
        self.assertEqual(1, len(resolved["warnings"]))

    def test_unknown_checked_option_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown checked options"):
            config.parse_checklist("- [x] Mystery prosperity index")

    def test_at_least_one_indicator_is_required(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            config.parse_checklist(
                "- [ ] Headline CPI\n- [x] Latest and previous observation\n"
            )

    def test_api_key_shape_is_validated_without_echoing_value(self):
        invalid = "NOT-A-VALID-SECRET"
        with self.assertRaises(ValueError) as raised:
            config.load_configuration({"FRED_API_KEY": invalid})
        self.assertNotIn(invalid, str(raised.exception))


class HttpClientTests(unittest.TestCase):
    def test_key_is_only_in_authorization_header(self):
        client = httpclient.Client(FAKE_KEY, interval=0)
        request = client.build_request(
            fetch.ENDPOINT,
            {"release_id": 10, "format": "json", "limit": 500000},
        )

        self.assertNotIn(FAKE_KEY, request.full_url)
        self.assertEqual(f"Bearer {FAKE_KEY}", request.get_header("Authorization"))

    def test_only_fred_https_host_is_allowed(self):
        httpclient.validate_url(fetch.ENDPOINT)
        with self.assertRaises(ValueError):
            httpclient.validate_url("http://api.stlouisfed.org/fred/v2/release/observations")
        with self.assertRaises(ValueError):
            httpclient.validate_url("https://api.stlouisfed.org.evil.example/data")


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if not self.payloads:
            raise AssertionError("unexpected request")
        return self.payloads.pop(0)


class FetchTests(unittest.TestCase):
    def test_cursor_pages_merge_selected_series_and_ignore_unselected_data(self):
        release = release_metadata()
        cpi_page_one = series_metadata(
            "CPIAUCSL",
            "Consumer Price Index for All Urban Consumers: All Items",
            [{"date": "2025-07-01", "value": "320.0"}],
        )
        cpi_page_two = series_metadata(
            "CPIAUCSL",
            "Consumer Price Index for All Urban Consumers: All Items",
            [{"date": "2026-07-01", "value": "330.0"}],
        )
        core = series_metadata(
            "CPILFESL",
            "Consumer Price Index for All Urban Consumers: All Items Less Food and Energy",
            [{"date": "2026-07-01", "value": "340.0"}],
        )
        ignored = series_metadata(
            "UNSELECTED",
            "Unselected series",
            [{"date": "2026-07-01", "value": "1"}],
        )
        client = FakeClient([
            {
                "has_more": True,
                "next_cursor": "CPIAUCSL,2026-01-01",
                "release": release,
                "series": [cpi_page_one, ignored],
            },
            {
                "has_more": False,
                "release": release,
                "series": [cpi_page_two, core],
            },
        ])

        result = fetch.fetch_release(
            client, 10, "Consumer Price Index", ["CPIAUCSL", "CPILFESL"]
        )

        self.assertEqual(2, result["pages"])
        self.assertEqual(["CPIAUCSL", "CPILFESL"], [item["series_id"] for item in result["series"]])
        self.assertEqual(2, len(result["series"][0]["observations"]))
        self.assertEqual(
            "CPIAUCSL,2026-01-01", client.calls[1][1]["next_cursor"]
        )

    def test_wrong_release_and_missing_selected_series_fail(self):
        wrong = FakeClient([{
            "has_more": False,
            "release": release_metadata(46, "Producer Price Index"),
            "series": [],
        }])
        with self.assertRaisesRegex(RuntimeError, "returned release 46"):
            fetch.fetch_release(wrong, 10, "Consumer Price Index", ["CPIAUCSL"])

        missing = FakeClient([{
            "has_more": False,
            "release": release_metadata(),
            "series": [],
        }])
        with self.assertRaisesRegex(RuntimeError, "did not contain selected series"):
            fetch.fetch_release(missing, 10, "Consumer Price Index", ["CPIAUCSL"])


class ConvertTests(unittest.TestCase):
    @staticmethod
    def manifest():
        indicator = dict(config.INDICATORS_BY_KEY["headline-cpi"])
        observations = [
            {"date": "2025-06-01", "value": "290"},
            {"date": "2025-07-01", "value": "291"},
            {"date": "2026-06-01", "value": "300"},
            {"date": "2026-07-01", "value": "303"},
        ]
        release = release_metadata()
        release["pages"] = 1
        release["series"] = [series_metadata(
            "CPIAUCSL",
            "Consumer Price Index for All Urban Consumers: All Items",
            observations,
        )]
        return {
            "schema_version": 1,
            "fetched_at": "2026-08-19T12:00:00+00:00",
            "filter_source": "configured",
            "history": "13",
            "warnings": [],
            "indicators": [indicator],
            "releases": [release],
        }

    def test_markdown_contains_comparisons_history_and_attribution(self):
        title, body = convert.build_markdown(self.manifest())

        self.assertEqual("FRED Economic Indicators — 2026-08-12 update", title)
        self.assertIn("Consumer Price Index (headline)", body)
        self.assertIn(
            "| Category | Indicator | As of | Latest level | Change vs prior observation | Change vs year ago | Units |",
            body,
        )
        self.assertNotIn("| Observation | Latest | Previous | Period change |", body)
        self.assertIn("+3 (+1%)", body)
        self.assertIn("+4.12% (+12 index points)", body)
        self.assertIn("| 2026-07-01 | 303 |", body)
        self.assertIn("Each row has its own **As of** date", body)
        self.assertIn("Most recent observation in this snapshot", body)
        self.assertIn("| Observation date | Reported level |", body)
        self.assertIn("FRED API Terms of Use", body)
        self.assertIn("public domain: citation requested", body)

    def test_change_formatting_explains_rates_and_thousands(self):
        self.assertEqual(
            "-23,000 jobs (-0.01%)",
            convert.change_text(
                Decimal("158858"),
                Decimal("158881"),
                "Thousands of Persons",
                "PAYEMS",
            ),
        )
        self.assertEqual(
            "-176,000 units (-12.44%)",
            convert.change_text(
                Decimal("1239"),
                Decimal("1415"),
                "Thousands of Units",
                "HOUST",
            ),
        )
        self.assertEqual(
            "-0.1 pp (-10 bp)",
            convert.change_text(
                Decimal("4.1"), Decimal("4.2"), "Percent", "UNRATE"
            ),
        )

    def test_snapshot_id_is_deterministic_and_changes_with_rendered_data(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            _, first = convert.write_output(manifest, directory)
            _, second = convert.write_output(manifest, directory)
            manifest["releases"][0]["series"][0]["observations"][-1]["value"] = "304"
            _, changed = convert.write_output(manifest, directory)

        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["id"], changed["id"])
        self.assertRegex(first["id"], r"^fred-[0-9a-f]{64}$")


class FeedStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ, {"FRED_INDICATORS_STATE": self.temporary.name}
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def write_index(self, snapshot_id):
        note_path = Path(self.temporary.name, "note.md")
        note_path.write_text("snapshot", encoding="utf-8")
        index_path = Path(self.temporary.name, "index.json")
        index_path.write_text(json.dumps([{
            "id": snapshot_id,
            "note_title": "FRED Economic Indicators",
            "file": str(note_path.resolve()),
            "source_url": "https://fred.stlouisfed.org/",
        }]), encoding="utf-8")
        return index_path

    def test_checkpoint_is_destination_scoped_and_atomic(self):
        snapshot_id = "fred-" + "1" * 64
        index_path = self.write_index(snapshot_id)

        self.assertEqual(1, feedstate.pending("project-a", index_path)["remaining"])
        feedstate.mark_seen("project-a", snapshot_id, "note-1")
        self.assertEqual(0, feedstate.pending("project-a", index_path)["remaining"])
        self.assertEqual(1, feedstate.pending("project-b", index_path)["remaining"])

        state_path, _ = feedstate.state_paths()
        persisted = json.loads(Path(state_path).read_text(encoding="utf-8"))
        self.assertEqual(
            "note-1",
            persisted["destinations"]["project-a"]["snapshots"][snapshot_id]["note_id"],
        )
        self.assertEqual(0o600, Path(state_path).stat().st_mode & 0o777)

    def test_checkpoint_conflict_does_not_overwrite_note_id(self):
        snapshot_id = "fred-" + "2" * 64
        feedstate.mark_seen("default", snapshot_id, "note-1")
        with self.assertRaisesRegex(RuntimeError, "another note"):
            feedstate.mark_seen("default", snapshot_id, "note-2")


if __name__ == "__main__":
    unittest.main()
