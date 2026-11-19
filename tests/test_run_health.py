import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from runner import main


class RunHealthTests(unittest.TestCase):
    def test_large_failure_rate_fails_after_writing_summary(self):
        totals = {'total': 140, 'reported': 100, 'ok': 70, 'failed': 30, 'skipped': 40, 'updated': 90}
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}):
                with self.assertRaisesRegex(RuntimeError, 'crawl_failure_rate'):
                    main._check_run_health(totals, Counter({'http_429': 30}))
            self.assertIn('30.0%', summary.read_text())
            self.assertIn('http_429', summary.read_text())
            self.assertIn('Newly recorded by server: 90 of 100 reported.', summary.read_text())

    def test_expected_skips_do_not_count_as_failures(self):
        main._check_run_health(
            {'total': 140, 'reported': 100, 'ok': 99, 'failed': 1, 'skipped': 40, 'updated': 0},
            Counter({'http_404': 1, 'skipped_smokingpipes': 40}),
        )

    def test_all_failed_small_batch_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'crawl_failure_rate'):
            main._check_run_health(
                {'total': 3, 'reported': 3, 'ok': 0, 'failed': 3, 'skipped': 0, 'updated': 3},
                Counter({'http_403': 3}),
            )

    def test_empty_subscription_pool_still_writes_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary),
                                         'MONITOR_API_BASE_URL': 'https://example.test',
                                         'MONITOR_SOURCE_MODE': 'subscription'}, clear=True), \
                    patch.object(main.STRATEGY_RUNTIME, 'load'), \
                    patch('runner.task_client.signed_post') as post:
                post.return_value.json.return_value = {'status': 'ok', 'tasks': []}
                main.main()
            self.assertIn('No tasks available for the selected source and tiers.', summary.read_text())

    def test_empty_shard_still_writes_summary(self):
        config = main.RunConfig(['low'], 'catalog', 'catalog', 100, 4, True, 2, 0)
        task = {'url': 'https://example.test/product'}
        while main._task_belongs_to_shard(task, config.shard_total, config.shard_index):
            task['url'] += '-next'

        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary),
                                         'MONITOR_API_BASE_URL': 'https://example.test'}), \
                    patch.object(main.STRATEGY_RUNTIME, 'load'), \
                    patch.object(main, '_load_run_config', return_value=config), \
                    patch('runner.task_client.signed_post') as post, \
                    patch.object(main, 'crawl_all') as crawl:
                post.return_value.json.return_value = {'status': 'ok', 'tasks': [task]}
                main.main()
            crawl.assert_not_called()
            self.assertIn('Tasks were available, but none matched this shard.', summary.read_text())
