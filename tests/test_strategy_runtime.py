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


class _IncompletePageStrategy:
    def requires_selenium(self):
        return False

    def is_page_complete(self, soup, url, text_content):
        return False

    def check_stock(self, soup, url, text_content):
        raise AssertionError('check_stock should not run for incomplete pages')

    def extract_price(self, soup, url, task=None):
        return '$10.00'


class _StaticRegistry:
    def __init__(self, strategy):
        self.strategy = strategy

    def get_strategy(self, url):
        return self.strategy


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

    def test_evaluate_marks_incomplete_strategy_page_as_bad_fetch(self):
        runtime = StrategyRuntime()
        runtime._registry = _StaticRegistry(_IncompletePageStrategy())

        result = runtime.evaluate(
            {'url': 'https://dreamingpipes.com/product/item'},
            '<html><body><button>Add to Cart</button></body></html>',
        )

        self.assertFalse(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['price'], '')
        self.assertEqual(result['reason'], 'incomplete_product_page')


if __name__ == '__main__':
    unittest.main()
