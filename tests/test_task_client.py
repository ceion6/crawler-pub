import unittest
from unittest.mock import Mock, patch

from runner import task_client


class TaskClientTests(unittest.TestCase):
    def test_pull_task_page_posts_full_filter_payload(self):
        response = Mock()
        response.json.return_value = {'status': 'ok', 'tasks': []}

        with patch.dict('os.environ', {'MONITOR_API_BASE_URL': 'https://example.test'}, clear=False):
            with patch('runner.task_client.signed_post', return_value=response) as signed_post_mock:
                payload = task_client.pull_task_page(
                    tier='high',
                    limit=50,
                    offset=100,
                    refresh=True,
                    source_mode='subscription',
                )

        self.assertEqual(payload, {'status': 'ok', 'tasks': []})
        signed_post_mock.assert_called_once_with(
            'https://example.test/internal/monitor/pull-tasks',
            {
                'tier': 'high',
                'limit': 50,
                'offset': 100,
                'refresh': True,
                'source_mode': 'subscription',
            },
            timeout=30,
        )


if __name__ == '__main__':
    unittest.main()
