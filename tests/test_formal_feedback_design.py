"""Regression checks for feedback interpretation, not the product scorer."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


analysis = module("feedback_analysis", "analyze-formal-feedback.py")
builder = module("feedback_builder", "build-feedback-local-evaluations.py")


def bulletin(time, notices):
    return {"issued_at_utc": time, "notices": [{"event_kind": k, "direction": d} for k,d in notices]}


class FeedbackDesignTest(unittest.TestCase):
    def test_repeated_notices_and_daytime_gap_are_one_episode(self):
        rows = [bulletin("2027-01-02T02:00:00Z", [("rain","ALL")]),
                bulletin("2027-01-01T23:45:00Z", [("rain","ALL"), ("rain","ALL")]),
                bulletin("2027-01-03T01:00:00Z", [])]
        result = analysis.notice_episodes(rows)
        self.assertEqual(len(result["rain"]), 1)
        self.assertEqual(result["rain"][0]["published_slots"], 2)

    def test_separated_weather_and_simultaneous_directions(self):
        rows = [bulletin("2027-01-01T01:00:00Z", [("overcast","E"),("overcast","W")]),
                bulletin("2027-01-01T01:15:00Z", []),
                bulletin("2027-01-01T01:30:00Z", [("overcast","E")])]
        self.assertEqual(len(analysis.notice_episodes(rows)["overcast"]), 3)

    def test_request_window_counts_observing_nights_not_utc_dates(self):
        req = {"issued_at_utc":"2027-01-01T23:00:00Z", "deadline_utc":"2027-01-03T05:00:00Z",
               "target_ids":list(range(8)), "minimum_completed":6, "completion_reward":100,
               "completion_factor_threshold":.5}
        nights = [{"observing_start_utc":"2027-01-01T23:00:00Z", "observing_end_utc":"2027-01-02T05:00:00Z"},
                  {"observing_start_utc":"2027-01-02T23:00:00Z", "observing_end_utc":"2027-01-03T05:00:00Z"}]
        shape = analysis.request_shape(req, nights)
        self.assertEqual(shape["window_nights"], 2)
        self.assertEqual(shape["window_hours"], 30)

    def test_v2_config_unchanged_and_explicit_overrides_applied(self):
        original = builder.load_builder()
        profile = original.PROFILES["d"]
        config = original._weather_config("d",ROOT/"cards/d",profile)
        self.assertEqual(config["stress_tests"]["data_loss"]["window_max_fraction"], .05)
        overridden = dict(profile, weather_overrides={"stress_tests":{"data_loss":{"window_max_fraction":.15}}})
        changed = original._weather_config("d",ROOT/"cards/d",overridden)
        self.assertEqual(changed["stress_tests"]["data_loss"]["window_max_fraction"],.15)
        self.assertEqual(changed["stress_tests"]["pointing_offset"],config["stress_tests"]["pointing_offset"])
        self.assertEqual(config, original._weather_config("d",ROOT/"cards/d",profile))

    def test_incompatible_new_request_shape_is_rejected(self):
        feedback = {"cards":{"a":{"public_event_summary":{}, "runs":[], "unique_requests":7}}}
        with self.assertRaises(ValueError):
            builder.profile_for("a","nominal",1,feedback,builder.load_builder())


if __name__ == "__main__":
    unittest.main()
