import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from runner import main


class FakeResponse:
    def __init__(self, status_code: int, text: str = '', headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class RunnerMainTests(unittest.TestCase):
    def test_resolve_host_policy_uses_strict_default_for_fournoggins(self):
        policy = main._resolve_host_policy('www.4noggins.com')
        self.assertEqual(policy.max_parallel, 1)
        self.assertEqual(policy.max_attempts, 4)
        self.assertGreaterEqual(policy.min_interval_seconds, 2.0)

    def test_compute_retry_delay_prefers_retry_after_header(self):
        policy = main.HostPolicy(backoff_cap_seconds=20.0)
        response = FakeResponse(503, headers={'Retry-After': '7'})
        self.assertEqual(main._compute_retry_delay(1, policy, response=response), 7.0)

    def test_crawl_one_retries_transient_status_then_succeeds(self):
        task = {'url': 'https://www.4noggins.com/products/sample'}
        policy = main.HostPolicy(max_parallel=1, min_interval_seconds=0.0, max_attempts=3, backoff_base_seconds=0.1, backoff_cap_seconds=0.2)
        host = main._normalize_host(task['url'])
        host_gates = {host: main.HostGate(policy)}
        scraper = Mock()
        scraper.get.side_effect = [
            FakeResponse(503, headers={'Retry-After': '0'}),
            FakeResponse(200, '<html><body>Add to cart $12.50</body></html>'),
        ]

        with patch('runner.main.cloudscraper.create_scraper', return_value=scraper):
            with patch.object(main.STRATEGY_RUNTIME, 'evaluate', return_value={}):
                with patch('runner.main.time.sleep') as sleep_mock:
                    result = main.crawl_one(task, host_gates=host_gates, host_policy_overrides={host: policy})

        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])
        self.assertEqual(result['price'], '$12.50')
        self.assertEqual(scraper.get.call_count, 2)
        sleep_mock.assert_called()

    def test_crawl_one_keeps_failure_shape_after_retry_exhausted(self):
        task = {'url': 'https://www.4noggins.com/products/sample'}
        policy = main.HostPolicy(max_parallel=1, min_interval_seconds=0.0, max_attempts=3, backoff_base_seconds=0.1, backoff_cap_seconds=0.2)
        host = main._normalize_host(task['url'])
        host_gates = {host: main.HostGate(policy)}
        scraper = Mock()
        scraper.get.side_effect = [
            FakeResponse(503),
            FakeResponse(503),
            FakeResponse(503),
        ]

        with patch('runner.main.cloudscraper.create_scraper', return_value=scraper):
            with patch('runner.main.time.sleep'):
                result = main.crawl_one(task, host_gates=host_gates, host_policy_overrides={host: policy})

        self.assertFalse(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['reason'], 'http_503')
        self.assertEqual(scraper.get.call_count, 3)

    def test_parse_tiers_keeps_unique_supported_values(self):
        self.assertEqual(main._parse_tiers('high,low,high,invalid'), ['high', 'low'])

    def test_main_paginates_and_runs_multiple_tiers(self):
        task_a = {'url': 'https://example.test/a'}
        task_b = {'url': 'https://example.test/b'}
        task_c = {'url': 'https://example.test/c'}

        pull_side_effect = [
            {'tasks': [task_a, task_b]},
            {'tasks': []},
            {'tasks': [task_c]},
        ]
        crawl_side_effect = [
            [
                {'url': task_a['url'], 'fetch_ok': True, 'in_stock': True, 'price': '$1.00', 'reason': ''},
                {'url': task_b['url'], 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': 'http_503'},
            ],
            [
                {'url': task_c['url'], 'fetch_ok': True, 'in_stock': False, 'price': '$2.00', 'reason': ''},
            ],
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    'os.environ',
                    {
                        'MONITOR_TIERS': 'low,high',
                        'MONITOR_SOURCE_MODE': 'subscription',
                        'MONITOR_PAGE_SIZE': '2',
                        'MAX_WORKERS': '4',
                    },
                    clear=False,
                )
            )
            load_mock = stack.enter_context(patch.object(main.STRATEGY_RUNTIME, 'load'))
            pull_mock = stack.enter_context(patch('runner.main.pull_task_page', side_effect=pull_side_effect))
            crawl_mock = stack.enter_context(patch('runner.main.crawl_all', side_effect=crawl_side_effect))
            report_mock = stack.enter_context(
                patch('runner.main.report_results', side_effect=[{'updated': 2}, {'updated': 1}])
            )
            info_mock = stack.enter_context(patch('runner.main.info'))
            warn_mock = stack.enter_context(patch('runner.main.warn'))

            main.main()

        load_mock.assert_called_once()
        self.assertEqual(pull_mock.call_count, 3)
        self.assertEqual(crawl_mock.call_count, 2)
        self.assertEqual(report_mock.call_count, 2)
        warn_mock.assert_not_called()

        first_call = pull_mock.call_args_list[0].kwargs
        self.assertEqual(first_call['tier'], 'low')
        self.assertEqual(first_call['offset'], 0)
        self.assertTrue(first_call['refresh'])
        self.assertEqual(first_call['source_mode'], 'subscription')

        second_call = pull_mock.call_args_list[1].kwargs
        self.assertEqual(second_call['tier'], 'low')
        self.assertEqual(second_call['offset'], 2)
        self.assertFalse(second_call['refresh'])

        third_call = pull_mock.call_args_list[2].kwargs
        self.assertEqual(third_call['tier'], 'high')
        self.assertEqual(third_call['offset'], 0)
        self.assertFalse(third_call['refresh'])

        self.assertTrue(any('运行结束' in call.args[0] for call in info_mock.call_args_list))


if __name__ == '__main__':
    unittest.main()
