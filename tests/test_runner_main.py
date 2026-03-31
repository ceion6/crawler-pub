import unittest
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


if __name__ == '__main__':
    unittest.main()
