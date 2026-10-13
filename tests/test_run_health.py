import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from runner import main


class RunHealthTests(unittest.TestCase):
    def test_large_failure_rate_fails_after_writing_summary(self):
        totals = {'total': 140, 'reported': 100, 'ok': 70, 'failed': 30, 'skipped': 40}
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}):
                with self.assertRaisesRegex(RuntimeError, 'crawl_failure_rate'):
                    main._check_run_health(totals, Counter({'http_429': 30}))
            self.assertIn('30.0%', summary.read_text())
            self.assertIn('http_429', summary.read_text())

    def test_expected_skips_do_not_count_as_failures(self):
        main._check_run_health(
            {'total': 140, 'reported': 100, 'ok': 99, 'failed': 1, 'skipped': 40},
            Counter({'http_404': 1, 'skipped_smokingpipes': 40}),
        )

    def test_all_failed_small_batch_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'crawl_failure_rate'):
            main._check_run_health(
                {'total': 3, 'reported': 3, 'ok': 0, 'failed': 3, 'skipped': 0},
                Counter({'http_403': 3}),
            )
