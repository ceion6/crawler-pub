from unittest.mock import Mock, patch

from runner import main


def test_empty_catalog_search_is_not_proof_of_out_of_stock():
    response = Mock(status_code=200)
    response.json.return_value = {'result': {'structuredContent': {'products': []}}}
    with patch('runner.main.curl_requests.post', return_value=response):
        result = main._crawl_shopify_via_ucp('https://4noggins.com/products/old-handle',
                                           main.FOURNOGGINS_UCP_ENDPOINT, 'fournoggins')
    assert result['fetch_ok'] is False
    assert result['reason'] == 'fournoggins_product_missing'
