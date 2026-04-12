import unittest
from unittest.mock import patch

from runner.strategy_runtime import StrategyRuntime


class _FakeStrategy:
    def requires_selenium(self):
        return True

    def fetch_with_selenium(self, url, driver_path, task):
        assert task['name'] == 'Royal Jersey'
        assert task['site'] == 'PipeUncle'
        return {
            'fetch_ok': True,
            'in_stock': True,
            'price': '$12.00',
            'reason': '',
        }


class _FakeRegistry:
    def get_strategy(self, url):
        return _FakeStrategy()


class StrategyRuntimeTests(unittest.TestCase):
    def test_evaluate_normalizes_task_keys_for_selenium_strategy(self):
        runtime = StrategyRuntime()
        runtime._registry = _FakeRegistry()

        with patch('webdriver_manager.chrome.ChromeDriverManager.install', return_value='/tmp/chromedriver'):
            result = runtime.evaluate(
                {
                    'url': 'https://www.pipeuncle.com/detail/goods?id=261',
                    'product_name': 'Royal Jersey',
                    'site_name': 'PipeUncle',
                },
                '',
            )

        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])
        self.assertEqual(result['price'], '$12.00')


if __name__ == '__main__':
    unittest.main()
