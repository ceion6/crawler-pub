from unittest.mock import Mock, patch

import pytest

from runner import main


def test_empty_catalog_search_is_not_proof_of_out_of_stock():
    response = Mock(status_code=200)
    response.json.return_value = {'result': {'structuredContent': {'products': []}}}
    with patch('runner.main.curl_requests.post', return_value=response):
        result = main._crawl_shopify_via_ucp('https://4noggins.com/products/old-handle',
                                           main.FOURNOGGINS_UCP_ENDPOINT, 'fournoggins')
    assert result['fetch_ok'] is False
    assert result['reason'] == 'fournoggins_product_missing'


def crawl_variants(variants, query=''):
    response = Mock(status_code=200, headers={})
    response.json.return_value = {'result': {'structuredContent': {'products': [
        {'handle': 'sample', 'variants': variants},
    ]}}}
    with patch('runner.main.curl_requests.post', return_value=response):
        return main._crawl_shopify_via_ucp('https://4noggins.com/products/sample' + query,
                                          main.FOURNOGGINS_UCP_ENDPOINT, 'fournoggins')


def test_requested_ucp_variant_uses_its_own_stock_and_price():
    result = crawl_variants([
        {'id': 'gid://shopify/ProductVariant/1', 'availability': {'available': True},
         'price': {'amount': 1000}},
        {'id': 'gid://shopify/ProductVariant/2', 'availability': {'available': False},
         'price': {'amount': 2500}},
    ], '?variant=2')
    assert result['fetch_ok'] is True
    assert result['in_stock'] is False
    assert result['price'] == '$25.00'


@pytest.mark.parametrize('variants,query', [
    ([], ''),
    ([{'id': 1, 'price': {'amount': 1000}}], ''),
    ([{'id': 1, 'availability': {'available': 'false'}}], ''),
    ([{'id': 1, 'availability': {'available': True}}], '?variant=2'),
    ([{'id': 1, 'availability': {'available': False}}, {'id': 2}], ''),
])
def test_unknown_or_unmatched_variant_is_not_trusted_out_of_stock(variants, query):
    result = crawl_variants(variants, query)
    assert result['fetch_ok'] is False
    assert result['in_stock'] is False
    assert result['price'] == ''


def test_all_explicitly_unavailable_variants_are_trusted_out_of_stock():
    result = crawl_variants([{'id': 1, 'availability': {'available': False},
                              'price': {'amount': 1000}}])
    assert result['fetch_ok'] is True
    assert result['in_stock'] is False
