"""Idle-poll backoff must slow down empty claims without drifting past the configured cap."""
import unittest

from worker.main import idle_backoff_seconds


class IdlePollBackoffTests(unittest.TestCase):
    def test_doubles_from_base_up_to_cap(self):
        base, cap = 5.0, 30.0
        expected = [5.0, 10.0, 20.0, 30.0, 30.0]
        for idle_streak, want in enumerate(expected):
            with self.subTest(idle_streak=idle_streak):
                got = idle_backoff_seconds(idle_streak, base, cap, jitter=0.0)
                self.assertEqual(got, want)

    def test_jitter_stays_within_twenty_percent_and_never_negative(self):
        base, cap = 5.0, 30.0
        for idle_streak in range(6):
            interval = min(base * (2 ** idle_streak), cap)
            for _ in range(200):
                got = idle_backoff_seconds(idle_streak, base, cap)
                self.assertGreaterEqual(got, 0.0)
                self.assertGreaterEqual(got, interval * 0.8 - 1e-9)
                self.assertLessEqual(got, interval * 1.2 + 1e-9)

    def test_never_exceeds_max_even_with_a_long_idle_streak(self):
        for _ in range(100):
            got = idle_backoff_seconds(idle_streak=50, base=5.0, max_seconds=30.0)
            self.assertLessEqual(got, 30.0 * 1.2 + 1e-9)


if __name__ == '__main__':
    unittest.main()
