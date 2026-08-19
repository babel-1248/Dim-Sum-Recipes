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
import syncstate  # noqa: E402
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
        }, hours, "2026-08-18 10:00 EDT", "2026-08-11", "2026-08-19")

        self.assertEqual(2, len(hours))
        self.assertEqual("now", hours[0]["when"])
        self.assertEqual("Unhealthy", hours[1]["aqi_category"])
        self.assertIn("10 AM (Now)", markdown.replace("**", ""))
        self.assertIn("0.02 in", markdown)
        self.assertIn("Open-Meteo", markdown)
        self.assertIn("Zippopotam.us", markdown)
        self.assertIn("2026-08-11 through 2026-08-19", markdown)

    def test_weather_and_aqi_lookups_handle_boundaries(self):
        self.assertEqual("Clear sky", wmo.weather_text(0))
        self.assertEqual("Moderate", wmo.aqi_category(100))
        self.assertEqual("Unhealthy (Sensitive Groups)", wmo.aqi_category(101))

    def test_provider_requests_use_the_exact_date_window(self):
        with mock.patch.object(fetch.CLIENT, "get_json", return_value={}) as get_json:
            fetch.fetch_forecast(42.357, -71.064, "2026-08-12", "2026-08-20")
            forecast_params = get_json.call_args.args[1]
            fetch.fetch_aqi(42.357, -71.064, "2026-08-12", "2026-08-20")
            aqi_params = get_json.call_args.args[1]

        for params in (forecast_params, aqi_params):
            self.assertEqual("2026-08-12", params["start_date"])
            self.assertEqual("2026-08-20", params["end_date"])
            self.assertNotIn("past_days", params)
            self.assertNotIn("forecast_days", params)

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
                    "fetch.py", "02108", "--start", "2026-08-11",
                    "--end", "2026-08-19", "--out", output,
                ]):
            fetch.main()
            index = json.loads(pathlib.Path(output, "index.json").read_text())
            manifest = json.loads(pathlib.Path(output, "manifest.json").read_text())

        self.assertEqual(1, len(index))
        self.assertEqual("02108", index[0]["zip"])
        self.assertEqual(
            "US Weather — 02108 (Boston, MA) — 2026-08-11 to 2026-08-19",
            index[0]["note_title"])
        self.assertEqual("2026-08-11", index[0]["window_start"])
        self.assertEqual("2026-08-19", manifest["window_end"])
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


class SyncStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ, {"RESEARCH_FEED_STATE": self.temp.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    @staticmethod
    def success_args(note_id="note-1"):
        return argparse.Namespace(
            destination="project-1",
            zip_code="02108",
            note_id=note_id,
            sync_date="2026-08-18",
        )

    def test_success_record_is_durable_and_destination_scoped(self):
        successful = syncstate.record_success(self.success_args())
        persisted = json.loads(pathlib.Path(
            self.temp.name, "weather.json").read_text())

        self.assertEqual("note-1", successful["note_id"])
        self.assertEqual("note-1", persisted["last_successful"][
            "project-1\n02108"]["note_id"])
        self.assertIsNone(
            syncstate.read()["last_successful"].get("default\n02108"))
        self.assertEqual(0o600, pathlib.Path(
            self.temp.name, "weather.json").stat().st_mode & 0o777)

    def test_first_run_starts_seven_days_back_and_ends_tomorrow(self):
        window = syncstate.resolve_window(
            "project-1", "02108", today="2026-08-19")

        self.assertTrue(window["first_run"])
        self.assertEqual("2026-08-12", window["start"])
        self.assertEqual("2026-08-20", window["end"])
        self.assertEqual(1, window["days_ahead"])

    def test_configured_days_ahead_controls_the_end_date(self):
        window = syncstate.resolve_window(
            "project-1", "02108", today="2026-08-19", days_ahead="3")

        self.assertEqual("2026-08-22", window["end"])
        self.assertEqual(3, window["days_ahead"])

    def test_zero_days_ahead_ends_on_today(self):
        window = syncstate.resolve_window(
            "project-1", "02108", today="2026-08-19", days_ahead="0")

        self.assertEqual("2026-08-19", window["end"])

    def test_days_ahead_must_be_between_zero_and_six(self):
        for invalid in ("-1", "7", "1.5", "one", ""):
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                syncstate.resolve_window(
                    "project-1", "02108", today="2026-08-19",
                    days_ahead=invalid)

    def test_repeat_run_starts_on_last_successful_sync_date(self):
        syncstate.record_success(self.success_args())

        window = syncstate.resolve_window(
            "project-1", "02108", today="2026-08-19")

        self.assertFalse(window["first_run"])
        self.assertEqual("2026-08-18", window["start"])
        self.assertEqual("2026-08-20", window["end"])

    def test_repeat_run_never_looks_back_more_than_seven_days(self):
        args = self.success_args()
        args.sync_date = "2026-07-01"
        syncstate.record_success(args)

        window = syncstate.resolve_window(
            "project-1", "02108", today="2026-08-19")

        self.assertEqual("2026-08-12", window["start"])
        self.assertEqual("2026-08-20", window["end"])

    def test_resolving_and_fetching_a_window_does_not_mark_success(self):
        syncstate.resolve_window("project-1", "02108", today="2026-08-19")

        self.assertFalse(pathlib.Path(self.temp.name, "weather.json").exists())

    def test_success_requires_a_returned_note_id(self):
        args = self.success_args(note_id="")
        with self.assertRaises(SystemExit):
            syncstate.record_success(args)

        self.assertFalse(pathlib.Path(self.temp.name, "weather.json").exists())

    def test_destination_cannot_create_a_state_key_collision(self):
        args = self.success_args()
        args.destination = "project-1\n02108"
        with self.assertRaises(SystemExit):
            syncstate.record_success(args)


if __name__ == "__main__":
    unittest.main()
