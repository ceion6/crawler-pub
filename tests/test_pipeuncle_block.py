import unittest
from unittest.mock import Mock, patch

from runner import main


class PipeUncleBlockTests(unittest.TestCase):
    def setUp(self):
        main._pipeuncle_blocked_reason = ''

    def test_site_block_is_probed_once_per_run_and_stays_failed(self):
        response = Mock(status_code=403, text='<html>Service unavailable</html>')
        with patch('runner.main.curl_requests.get', return_value=response) as get:
            first = main.crawl_one({'url': 'https://www.pipeuncle.com/goods/detail?id=1'})
            second = main.crawl_one({'url': 'https://www.pipeuncle.com/goods/detail?id=2'})
        self.assertEqual(get.call_count, 1)
        for result in [first, second]:
            self.assertFalse(result['fetch_ok'])
            self.assertEqual(result['reason'], 'pipeuncle_http_403')

    def test_html_interception_is_not_misreported_as_json_decode_error(self):
        response = Mock(status_code=200, text='<html><title>Service unavailable</title></html>')
        response.json.side_effect = ValueError('HTML is not JSON')
        with patch('runner.main.curl_requests.get', return_value=response) as get:
            first = main.crawl_one({'url': 'https://www.pipeuncle.com/goods/detail?id=1'})
            second = main.crawl_one({'url': 'https://www.pipeuncle.com/goods/detail?id=2'})
        self.assertEqual(get.call_count, 1)
        self.assertFalse(first['fetch_ok'])
        self.assertEqual(second['reason'], 'pipeuncle_non_json_response')
