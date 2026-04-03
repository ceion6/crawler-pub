import hashlib
import hmac
import os
import unittest
from unittest.mock import Mock, patch

from runner import auth_client


class _FixedUuid:
    hex = 'nonce-fixed'


class AuthClientTests(unittest.TestCase):
    def test_canonical_request_target_sorts_query_pairs_from_url_and_params(self):
        path, query = auth_client._canonical_request_target(
            'https://example.test/internal/monitor/run-stats?b=2',
            params={'a': 1, 'c': ['3', '4']},
        )

        self.assertEqual(path, '/internal/monitor/run-stats')
        self.assertEqual(query, 'a=1&b=2&c=3&c=4')

    @patch.dict(
        os.environ,
        {
            'MONITOR_API_CLIENT_ID': 'crawler-public',
            'MONITOR_API_SECRET': 'top-secret',
        },
        clear=False,
    )
    def test_signed_headers_include_path_and_nonce_in_signature(self):
        with patch('runner.auth_client.time.time', return_value=1700000000):
            with patch('runner.auth_client.uuid.uuid4', return_value=_FixedUuid()):
                headers_pull = auth_client._signed_headers(
                    method='GET',
                    url='https://example.test/internal/monitor/pull-strategy',
                    body=b'',
                )
                headers_download = auth_client._signed_headers(
                    method='GET',
                    url='https://example.test/internal/monitor/download-strategy',
                    body=b'',
                )

        self.assertEqual(headers_pull['X-Nonce'], 'nonce-fixed')
        self.assertEqual(headers_pull['X-Timestamp'], '1700000000')
        self.assertNotEqual(headers_pull['X-Signature'], headers_download['X-Signature'])

    @patch.dict(
        os.environ,
        {
            'MONITOR_API_CLIENT_ID': 'crawler-public',
            'MONITOR_API_SECRET': 'top-secret',
        },
        clear=False,
    )
    def test_signed_get_sends_nonce_header_and_canonical_signature(self):
        response = Mock()

        with patch('runner.auth_client.requests.get', return_value=response) as get_mock:
            with patch('runner.auth_client.time.time', return_value=1700000000):
                with patch('runner.auth_client.uuid.uuid4', return_value=_FixedUuid()):
                    result = auth_client.signed_get(
                        'https://example.test/internal/monitor/run-stats?b=2',
                        timeout=15,
                        params={'a': 1},
                    )

        self.assertIs(result, response)
        _, kwargs = get_mock.call_args
        headers = kwargs['headers']
        expected_payload = auth_client._signature_payload(
            method='GET',
            url='https://example.test/internal/monitor/run-stats?b=2',
            body=b'',
            ts='1700000000',
            nonce='nonce-fixed',
            params={'a': 1},
        )
        expected_signature = hmac.new(
            b'top-secret',
            expected_payload,
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['params'], {'a': 1})
        self.assertEqual(headers['X-Client-Id'], 'crawler-public')
        self.assertEqual(headers['X-Nonce'], 'nonce-fixed')
        self.assertEqual(headers['X-Signature'], expected_signature)


if __name__ == '__main__':
    unittest.main()
