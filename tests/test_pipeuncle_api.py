import base64
import json
import unittest
from unittest.mock import Mock, patch

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from runner import main


def encrypted_response(payload):
    ciphertext = AES.new(b'9610371598423434', AES.MODE_ECB).encrypt(
        pad(json.dumps(payload).encode(), AES.block_size)
    )
    return Mock(status_code=200, headers={'X-Api-Encrypt': 'true'},
                text=base64.b64encode(ciphertext).decode())


class PipeUncleApiTests(unittest.TestCase):
    def setUp(self):
        main._pipeuncle_blocked_reason = ''

    def crawl(self, payload):
        with patch('runner.main.curl_requests.get', return_value=encrypted_response(payload)):
            return main.crawl_one({'url': 'https://www.pipeuncle.com/detail/goods?id=572'})

    def test_current_encrypted_response_reads_boolean_stock_even_when_quantity_is_hidden(self):
        for available in (True, False):
            with self.subTest(available=available):
                result = self.crawl({'code': 0, 'data': {
                    'id': 572, 'stock': None, 'stockStatus': available, 'price': 4409,
                }})
                self.assertTrue(result['fetch_ok'])
                self.assertEqual(result['in_stock'], available)
                self.assertEqual(result['price'], '$44.09')

    def test_unknown_or_wrong_product_remains_fetch_failure(self):
        for data, reason in [
            ({'id': 573, 'stockStatus': True}, 'pipeuncle_product_id_mismatch'),
            ({'id': 572, 'stockStatus': None}, 'pipeuncle_stock_status_missing'),
            ({'id': 572, 'stockStatus': 'false'}, 'pipeuncle_stock_status_missing'),
            (None, 'pipeuncle_invalid_product_data'),
        ]:
            with self.subTest(reason=reason):
                result = self.crawl({'code': 0, 'data': data})
                self.assertFalse(result['fetch_ok'])
                self.assertEqual(result['reason'], reason)

    def test_missing_product_is_confirmed_by_current_api_error(self):
        result = self.crawl({'code': 1008005000, 'msg': '商品不存在或已下架'})
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['reason'], 'pipeuncle_product_missing')

    def test_explicit_unlisted_product_is_valid_unavailable_result(self):
        result = self.crawl({'code': 1008005003, 'msg': '商品 SPU 不处于上架状态'})
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['reason'], 'pipeuncle_product_unlisted')

    def test_bad_encrypted_response_remains_fetch_failure(self):
        response = Mock(status_code=200, headers={'X-Api-Encrypt': 'true'}, text='broken-ciphertext')
        with patch('runner.main.curl_requests.get', return_value=response):
            result = main.crawl_one({'url': 'https://www.pipeuncle.com/detail/goods?id=572'})
        self.assertFalse(result['fetch_ok'])
        self.assertTrue(result['reason'].startswith('pipeuncle_decrypt_failed:'))
