"""Backend credentials must be present before starting a long-running evaluator."""
import os
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/worker-preflight.sh'


class WorkerPreflightTests(unittest.TestCase):
    def test_missing_backend_credentials_fail_without_disclosing_values(self):
        for url, key in [('', ''), ('', 'test-private-key'), ('https://test.invalid', '')]:
            with self.subTest(url_present=bool(url), key_present=bool(key)):
                result = subprocess.run(['bash', str(SCRIPT)], text=True, capture_output=True,
                                        env={**os.environ, 'SUPABASE_URL': url, 'SUPABASE_SERVICE_ROLE_KEY': key})
                self.assertEqual(result.returncode, 1)
                self.assertIn('Evaluation worker requires', result.stdout)
                self.assertNotIn('test-private-key', result.stdout + result.stderr)

    def test_complete_configuration_passes_without_network_access_or_output(self):
        result = subprocess.run(['bash', str(SCRIPT)], text=True, capture_output=True,
                                env={**os.environ, 'SUPABASE_URL': 'https://test.invalid',
                                     'SUPABASE_SERVICE_ROLE_KEY': 'test-private-key'})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, '')


if __name__ == '__main__':
    unittest.main()
