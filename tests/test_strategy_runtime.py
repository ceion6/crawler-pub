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


class _StockStrategy:
    site_identifier = 'default'

    def requires_selenium(self):
        return False

    def check_stock(self, soup, url, text_content):
        return False

    def extract_price(self, soup, url, task=None):
        return '€10.00'


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

    def test_bundled_default_requires_the_same_evidence_and_keeps_its_price(self):
        runtime = StrategyRuntime()
        runtime._registry = _StaticRegistry(_StockStrategy())
        task = {'url': 'https://example.test/products/sample'}
        cases = (
            ('Auf Lager', True, True, ''),
            ('Nicht auf Lager', True, False, ''),
            ('Maintenance', False, False, 'missing_stock_evidence'),
            ('Ausverkauft <button>Add to bag</button>', False, False, 'conflicting_stock_evidence'),
        )
        for text, fetch_ok, in_stock, reason in cases:
            with self.subTest(text=text):
                result = runtime.evaluate(task, f'<html><body>{text}</body></html>')
                self.assertEqual(result['fetch_ok'], fetch_ok)
                self.assertEqual(result['in_stock'], in_stock)
                self.assertEqual(result['reason'], reason)
                self.assertEqual(result['price'], '€10.00')

    def test_explicit_strategy_keeps_its_own_stock_decision(self):
        runtime = StrategyRuntime()
        strategy = _StockStrategy()
        strategy.site_identifier = 'example.test'
        runtime._registry = _StaticRegistry(strategy)

        result = runtime.evaluate({'url': 'https://example.test/products/sample'},
                                  '<html><body>Sold out <button>Add to cart</button></body></html>')

        self.assertEqual(result, {'fetch_ok': True, 'in_stock': False, 'price': '€10.00', 'reason': ''})


if __name__ == '__main__':
    unittest.main()
