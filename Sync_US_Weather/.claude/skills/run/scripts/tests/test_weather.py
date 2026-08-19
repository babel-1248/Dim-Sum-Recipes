import argparse
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
os.sys.path.insert(0, str(SCRIPTS))

import fetch  # noqa: E402
import geocode  # noqa: E402
import httpclient  # noqa: E402
import refreshstate  # noqa: E402
import wmo  # noqa: E402


class ZipTests(unittest.TestCase):
    def test_zip_requires_exactly_five_ascii_digits(self):
        self.assertEqual("02108", geocode.normalize("02108"))
        for invalid in ("2108", "02108-1234", "abcde", "１２３４５", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                geocode.normalize(invalid)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.forecast = {"hourly": {
            "time": ["2026-08-18T10:00", "2026-08-18T11:00"],
            "temperature_2m": [70.2, 71.8],
            "apparent_temperature": [69.6, 72.1],
            "precipitation": [0.0, 0.02],
            "precipitation_probability": [10, 80],
            "weather_code": [1, 61],
            "wind_speed_10m": [4.2, 6.8],
        }}
        self.aqi = {"hourly": {
            "time": ["2026-08-18T10:00", "2026-08-18T11:00"],
            "us_aqi": [55, 160],
            "pm2_5": [8.2, 42.4],
        }}

    def test_hourly_data_is_merged_and_rendered_with_attribution(self):
        hours = fetch.build_hours(
            self.forecast,
            fetch.hourly_aqi_by_time(self.aqi),
            "2026-08-18T10:00",
        )
        markdown = fetch.render_markdown({
            "zip": "02108", "place": "Boston", "state_abbr": "MA"
        }, hours, "2026-08-18 10:00 EDT")

        self.assertEqual(2, len(hours))
        self.assertEqual("now", hours[0]["when"])
        self.assertEqual("Unhealthy", hours[1]["aqi_category"])
        self.assertIn("10 AM (Now)", markdown.replace("**", ""))
        self.assertIn("0.02 in", markdown)
        self.assertIn("Open-Meteo", markdown)
        self.assertIn("Zippopotam.us", markdown)

    def test_weather_and_aqi_lookups_handle_boundaries(self):
        self.assertEqual("Clear sky", wmo.weather_text(0))
        self.assertEqual("Moderate", wmo.aqi_category(100))
        self.assertEqual("Unhealthy (Sensitive Groups)", wmo.aqi_category(101))

    def test_fetch_main_writes_one_note_manifest_and_index(self):
        place = {
            "zip": "02108", "place": "Boston", "state": "Massachusetts",
            "state_abbr": "MA", "latitude": 42.357, "longitude": -71.064,
        }
        forecast = dict(self.forecast, timezone="America/New_York")
        with tempfile.TemporaryDirectory() as output, \
                mock.patch.object(fetch.geocode, "resolve", return_value=place), \
                mock.patch.object(fetch, "fetch_forecast", return_value=forecast), \
                mock.patch.object(fetch, "fetch_aqi", return_value=self.aqi), \
                mock.patch.object(os.sys, "argv", [
                    "fetch.py", "02108", "--out", output,
                ]):
            fetch.main()
            index = json.loads(pathlib.Path(output, "index.json").read_text())
            manifest = json.loads(pathlib.Path(output, "manifest.json").read_text())

        self.assertEqual(1, len(index))
        self.assertEqual("02108", index[0]["zip"])
        self.assertEqual("US Weather — 02108 (Boston, MA)", index[0]["note_title"])
        self.assertEqual(2, index[0]["hour_count"])
        self.assertEqual(1, index[0]["unhealthy_hours"])
        self.assertEqual(2, len(manifest["hours"]))


class HttpClientTests(unittest.TestCase):
    def test_only_approved_https_hosts_are_allowed(self):
        httpclient._validate_url("https://api.open-meteo.com/v1/forecast")
        with self.assertRaises(ValueError):
            httpclient._validate_url("http://api.open-meteo.com/v1/forecast")
        with self.assertRaises(ValueError):
            httpclient._validate_url("https://api.open-meteo.com.evil.example/data")


class RefreshStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ, {"RESEARCH_FEED_STATE": self.temp.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    @staticmethod
    def stage_args(new_id="new-1"):
        return argparse.Namespace(
            destination="project-1",
            zip_code="02108",
            title="US Weather — 02108 (Boston, MA)",
            new_note_id=new_id,
            old_note_id=["old-1", "old-1", "old-2"],
            content_sha256="abc123",
            generated_at="2026-08-18T10:00:00-04:00",
        )

    def test_stage_is_durable_deduplicated_and_destination_scoped(self):
        staged = refreshstate.stage(self.stage_args())
        persisted = json.loads(pathlib.Path(
            self.temp.name, "weather.json").read_text())

        self.assertEqual(["old-1", "old-2"], staged["old_note_ids"])
        self.assertEqual("new-1", persisted["staged"][
            "project-1\n02108"]["new_note_id"])
        self.assertIsNone(refreshstate.read()["active"].get("default\n02108"))
        self.assertEqual(0o600, pathlib.Path(
            self.temp.name, "weather.json").stat().st_mode & 0o777)

    def test_completion_promotes_new_note_and_clears_pending_cleanup(self):
        refreshstate.stage(self.stage_args())
        active = refreshstate.complete("project-1", "02108")
        data = refreshstate.read()

        self.assertEqual("new-1", active["new_note_id"])
        self.assertEqual({}, data["staged"])
        self.assertEqual("new-1", data["active"]["project-1\n02108"]["new_note_id"])

    def test_second_new_note_cannot_overwrite_unfinished_replacement(self):
        refreshstate.stage(self.stage_args())
        with self.assertRaises(SystemExit):
            refreshstate.stage(self.stage_args(new_id="new-2"))

    def test_destination_cannot_create_a_state_key_collision(self):
        args = self.stage_args()
        args.destination = "project-1\n02108"
        with self.assertRaises(SystemExit):
            refreshstate.stage(args)


if __name__ == "__main__":
    unittest.main()
