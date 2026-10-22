from unittest.mock import Mock, patch

import pytest

from runner import main


URL = 'https://pipemoment.com/en/products/sample'


def json_response(payload, status=200):
    response = Mock(status_code=status, headers={})
    response.json.return_value = payload
    return response


def crawl_product(variants, source='catalog', query=''):
    product = {'handle': 'sample', 'variants': variants}
    catalog = {'products': [product] if source == 'catalog' else []}
    main._pipemoment_catalog_cache = None
    with patch('runner.main.requests.get', side_effect=[
        json_response(catalog), json_response(product),
    ]):
        with patch('runner.main.curl_requests.post', return_value=json_response({
            'result': {'structuredContent': {'products': [
                {'handle': 'sample', 'variants': []},
            ]}},
        })):
            return main.crawl_one({'url': URL + query})


@pytest.mark.parametrize('source', ['catalog', 'product'])
@pytest.mark.parametrize('variant', [
    {'id': 1, 'price': '12.50'},
    {'id': 1, 'available': None},
    {'id': 1, 'available': 'false'},
    {'id': 1, 'available': 'true'},
    {'id': 1, 'available': 0},
    {'id': 1, 'available': 1},
    {'id': 1, 'available': {}},
    {'id': 1, 'available': []},
])
def test_unknown_pipemoment_availability_is_not_trusted_out_of_stock(source, variant):
    result = crawl_product([variant], source)
    assert result['fetch_ok'] is False
    assert result['in_stock'] is False
    assert result['price'] == ''


@pytest.mark.parametrize('source,price,expected_price', [
    ('catalog', '12.50', '$12.50'),
    ('product', 1250, '$12.50'),
])
@pytest.mark.parametrize('available', [False, True])
def test_explicit_pipemoment_stock_remains_trusted(source, price, expected_price, available):
    result = crawl_product([{'id': 1, 'available': available, 'price': price}], source)
    assert result['fetch_ok'] is True
    assert result['in_stock'] is available
    assert result['price'] == expected_price


def test_pipemoment_requested_variant_does_not_use_another_variants_availability():
    result = crawl_product([
        {'id': 1, 'available': True, 'price': '12.50'},
        {'id': 2, 'price': '25.00'},
    ], query='?variant=2')
    assert result['fetch_ok'] is False
    assert result['price'] == ''


def test_pipemoment_explicit_requested_variant_ignores_unknown_sibling():
    result = crawl_product([
        {'id': 1, 'price': '12.50'},
        {'id': 2, 'available': False, 'price': '25.00'},
    ], query='?variant=2')
    assert result['fetch_ok'] is True
    assert result['in_stock'] is False
    assert result['price'] == '$25.00'


@pytest.mark.parametrize('variants', [None, {}, [], [None], [1], [
    {'id': 1, 'available': False}, {'id': 2},
]])
def test_invalid_or_partial_pipemoment_variants_are_not_trusted(variants):
    result = crawl_product(variants)
    assert result['fetch_ok'] is False
    assert result['price'] == ''


@pytest.mark.parametrize('products', [None, True, 1, {}, 'invalid'])
def test_malformed_pipemoment_fallback_products_return_failure(products):
    main._pipemoment_catalog_cache = None
    with patch('runner.main.requests.get', return_value=json_response({}, status=404)):
        with patch('runner.main.curl_requests.post', return_value=json_response({
            'result': {'structuredContent': {'products': products}},
        })):
            result = main.crawl_one({'url': URL})
    assert result['fetch_ok'] is False
    assert result['price'] == ''
    assert result['reason'] == 'pipemoment_ucp_products_invalid'
