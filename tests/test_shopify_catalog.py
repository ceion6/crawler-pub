import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from runner import main


def product(handle, variant_id=1, available=True, price='12.50'):
    return {'handle': handle, 'variants': [
        {'id': variant_id, 'available': available, 'price': price},
    ]}


def response(products, status=200):
    result = Mock(status_code=status, headers={})
    result.json.return_value = {'products': products}
    return result


class ShopifyCatalogTests(unittest.TestCase):
    def setUp(self):
        main._shopify_catalog_cache.clear()

    def test_catalog_is_loaded_once_across_tasks_and_pages(self):
        first_page = [product(f'blend-{i}') for i in range(250)]
        second_page = [product('last-blend', available=False, price='20.00')]
        urls = ['https://4noggins.com/products/blend-1',
                'https://4noggins.com/products/last-blend']
        with patch('runner.main.requests.get', side_effect=[response(first_page), response(second_page)]) as get:
            with patch('runner.main.curl_requests.post') as post:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda url: main.crawl_one({'url': url}), urls))
                # A later task page must reuse the same run's catalog.
                main.crawl_one({'url': urls[0]})
        self.assertEqual(get.call_count, 2)
        post.assert_not_called()
        self.assertTrue(all(r['fetch_ok'] for r in results))
        self.assertTrue(results[0]['in_stock'])
        self.assertFalse(results[1]['in_stock'])
        self.assertEqual(results[1]['price'], '$20.00')
        self.assertIn('page=2', get.call_args_list[1].args[0])

    def test_requested_variant_stock_is_used(self):
        item = product('sample', variant_id=1, available=True)
        item['variants'].append({'id': 2, 'available': False, 'price': '25.00'})
        with patch('runner.main.requests.get', return_value=response([item])):
            with patch('runner.main.curl_requests.post') as post:
                result = main.crawl_one({'url': 'https://70cigars.com/products/sample?variant=2'})
        post.assert_not_called()
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['price'], '$25.00')

    def test_rate_limit_is_retried_then_cached_on_failure(self):
        rate_limited = response([], status=429)
        rate_limited.headers = {'Retry-After': '7'}
        with patch('runner.main.requests.get', return_value=rate_limited) as get:
            with patch('runner.main.time.sleep') as sleep:
                with patch('runner.main.curl_requests.post') as post:
                    first = main.crawl_one({'url': 'https://4noggins.com/products/one'})
                    calls = get.call_count
                    second = main.crawl_one({'url': 'https://4noggins.com/products/two'})
        self.assertGreater(calls, 1)
        self.assertLessEqual(calls, 4)
        self.assertEqual(get.call_count, calls)
        post.assert_not_called()
        sleep.assert_any_call(7.0)
        for result in [first, second]:
            self.assertFalse(result['fetch_ok'])
            self.assertEqual(result['reason'], 'fournoggins_catalog_http_429')

    def test_partial_catalog_failure_does_not_claim_missing_products(self):
        first_page = [product(f'blend-{i}') for i in range(250)]
        with patch('runner.main.requests.get', side_effect=[response(first_page), response([], status=403)]) as get:
            with patch('runner.main.curl_requests.post') as post:
                first = main.crawl_one({'url': 'https://70cigars.com/products/blend-1'})
                second = main.crawl_one({'url': 'https://70cigars.com/products/not-in-first-page'})
        self.assertEqual(get.call_count, 2)
        post.assert_not_called()
        self.assertFalse(first['fetch_ok'])
        self.assertEqual(second['reason'], 'seventycigars_catalog_http_403')

    def test_missing_catalog_handle_uses_existing_ucp_lookup(self):
        expected = {'url': 'https://4noggins.com/products/renamed', 'fetch_ok': True}
        with patch('runner.main.requests.get', return_value=response([product('other')])):
            with patch('runner.main._crawl_shopify_via_ucp', return_value=expected) as ucp:
                result = main.crawl_one({'url': expected['url']})
        self.assertEqual(result, expected)
        ucp.assert_called_once()
