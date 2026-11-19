import unittest
from unittest.mock import Mock, patch

from runner import report_client


class ReportClientTests(unittest.TestCase):
    def setUp(self):
        self.response = Mock()
        patches = (
            patch.dict('os.environ', {'MONITOR_API_BASE_URL': 'https://example.test'}),
            patch('runner.report_client.signed_post', return_value=self.response),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_report_accepts_duplicate_batch_acknowledgement(self):
        self.response.json.return_value = {'status': 'ok', 'updated': 0}
        results = [{'url': 'https://example.test/product'}]

        payload = report_client.report_results(run_id='sample-run', results=results)

        self.response.raise_for_status.assert_called_once()
        self.assertEqual(payload['updated'], 0)

    def test_report_rejects_invalid_acknowledgement(self):
        for payload in ({'status': 'error', 'updated': 0}, {'status': 'ok'},
                        {'status': 'ok', 'updated': True}, {'status': 'ok', 'updated': -1},
                        {'status': 'ok', 'updated': 2}):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                with self.assertRaisesRegex(RuntimeError, '结果回报响应无效'):
                    report_client.report_results(run_id='sample-run', results=[{'url': 'https://example.test/product'}])

    def test_report_rejects_invalid_json(self):
        self.response.json.side_effect = ValueError('invalid JSON')
        with self.assertRaisesRegex(RuntimeError, '结果回报响应无效'):
            report_client.report_results(run_id='sample-run', results=[])
