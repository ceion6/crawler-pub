import unittest
from unittest.mock import Mock, patch

from runner import main


class SourcePermissionTests(unittest.TestCase):
    def test_paused_source_skips_before_cooldown_or_fetch(self):
        urls = (
            'https://dreamingpipes.com/fixture',
            'https://WWW.DREAMINGPIPES.COM/fixture',
            'https://store.dreamingpipes.com.:443/fixture',
        )
        with patch.object(main, '_crawl_one_with_gate') as fetch:
            for url in urls:
                with self.subTest(url=url):
                    gate = Mock()
                    result = main.crawl_one({'url': url}, {main._normalize_host(url): gate})
                    self.assertEqual(result['reason'], 'source_permission_paused')
                    self.assertTrue(result['skip_update'])
                    self.assertFalse(result['fetch_ok'])
                    gate.check_cooldown.assert_not_called()
        fetch.assert_not_called()

    def test_paused_source_is_counted_but_not_reported(self):
        config = main.RunConfig(['low'], 'catalog', 'catalog', 100, 1, True, 1, 0)
        paused = {'url': 'https://store.dreamingpipes.com/fixture'}
        other = {'url': 'https://dreamingpipes.com.example.test/fixture'}
        observed = {'url': other['url'], 'fetch_ok': True, 'in_stock': False}
        with patch.object(main, '_crawl_one_with_gate', return_value=observed) as fetch, \
                patch.object(main, 'report_results', return_value={'updated': 1}) as report:
            totals = main._process_task_page('sample-run', 'low', config, 0, 0, [paused, other])

        self.assertEqual(fetch.call_args.args[0], other)
        report.assert_called_once_with(run_id='sample-run', results=[observed], result_mode='catalog')
        self.assertEqual(totals['total'], 2)
        self.assertEqual(totals['reported'], 1)
        self.assertEqual(totals['skipped'], 1)
        self.assertEqual(totals['failed'], 0)
        self.assertEqual(totals['reason_counts']['source_permission_paused'], 1)
