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

    def test_pull_task_page_rejects_invalid_response(self):
        with patch.dict('os.environ', {'MONITOR_API_BASE_URL': 'https://example.test'}), \
                patch('runner.task_client.signed_post') as post:
            for payload in ({'status': 'error', 'tasks': []}, {'status': 'ok'},
                            {'status': 'ok', 'tasks': None}, {'status': 'ok', 'tasks': [{}]}):
                with self.subTest(payload=payload):
                    post.return_value.json.return_value = payload
                    with self.assertRaisesRegex(RuntimeError, '拉取任务响应无效'):
                        task_client.pull_task_page(tier='low', limit=20)

    def test_pull_task_page_rejects_invalid_json(self):
        response = Mock()
        response.json.side_effect = ValueError('invalid JSON')
        with patch.dict('os.environ', {'MONITOR_API_BASE_URL': 'https://example.test'}), \
                patch('runner.task_client.signed_post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, '拉取任务响应无效'):
                task_client.pull_task_page(tier='low', limit=20)

    def test_pull_task_page_posts_the_cursor_as_received(self):
        after_url = 'https://example.test/product?size=2%20oz'
        next_url = 'https://example.test/product?size=3%20oz'
        response = Mock()
        response.json.return_value = {
            'status': 'ok',
            'tasks': [{'url': next_url}],
            'next_after_url': next_url,
        }
        with patch.dict('os.environ', {'MONITOR_API_BASE_URL': 'https://example.test'}), \
                patch('runner.task_client.signed_post', return_value=response) as post:
            result = task_client.pull_task_page(tier='low', limit=1, after_url=after_url)

        self.assertEqual(post.call_args.args[1]['after_url'], after_url)
        self.assertEqual(post.call_args.args[1]['offset'], 0)
        self.assertEqual(result['next_after_url'], next_url)

    def test_pull_task_page_rejects_invalid_cursor_pages(self):
        after_url = 'https://example.test/a'
        next_url = 'https://example.test/b'
        final_url = 'https://example.test/c'
        cases = (
            ([{'url': after_url}], after_url),
            ([{'url': next_url}, {'url': next_url}], None),
            ([{'url': next_url}, {'url': after_url}], None),
            ([{'url': next_url}, {'url': final_url}], next_url),
            ([{'url': next_url}, {'url': final_url}], None),
            ([{'url': next_url}], next_url),
            ([{'url': next_url}], False),
            ([], next_url),
        )
        with patch.dict('os.environ', {'MONITOR_API_BASE_URL': 'https://example.test'}), \
                patch('runner.task_client.signed_post') as post:
            for tasks, cursor in cases:
                with self.subTest(tasks=tasks, cursor=cursor):
                    post.return_value.json.return_value = {
                        'status': 'ok',
                        'tasks': tasks,
                        'next_after_url': cursor,
                    }
                    with self.assertRaisesRegex(RuntimeError, '任务游标未前进|游标与页面不一致'):
                        task_client.pull_task_page(tier='low', limit=2, after_url=after_url)


if __name__ == '__main__':
    unittest.main()
