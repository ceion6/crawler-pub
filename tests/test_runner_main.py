import base64
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from Crypto.Cipher import AES

from runner import main


class FakeResponse:
    def __init__(self, status_code: int, text: str = '', headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class RunnerMainTests(unittest.TestCase):
    def test_resolve_host_policy_uses_strict_default_for_fournoggins(self):
        policy = main._resolve_host_policy('www.4noggins.com')
        self.assertEqual(policy.max_parallel, 1)
        self.assertEqual(policy.max_attempts, 4)
        self.assertGreaterEqual(policy.min_interval_seconds, 2.0)

    def test_resolve_host_policy_uses_conservative_defaults_for_havahavana(self):
        policy = main._resolve_host_policy('www.havahavana.com')
        self.assertEqual(policy.max_parallel, 1)
        self.assertEqual(policy.max_attempts, 4)
        self.assertGreaterEqual(policy.min_interval_seconds, 2.5)

    def test_compute_retry_delay_prefers_retry_after_header(self):
        policy = main.HostPolicy(backoff_cap_seconds=20.0)
        response = FakeResponse(503, headers={'Retry-After': '7'})
        self.assertEqual(main._compute_retry_delay(1, policy, response=response), 7.0)

    def test_crawl_one_retries_transient_status_then_succeeds(self):
        task = {'url': 'https://example.test/products/sample'}
        policy = main.HostPolicy(max_parallel=1, min_interval_seconds=0.0, max_attempts=3, backoff_base_seconds=0.1, backoff_cap_seconds=0.2)
        host = main._normalize_host(task['url'])
        host_gates = {host: main.HostGate(policy)}
        scraper = Mock()
        scraper.get.side_effect = [
            FakeResponse(503, headers={'Retry-After': '0'}),
            FakeResponse(200, '<html><body>Add to cart $12.50</body></html>'),
        ]

        with patch('runner.main.cloudscraper.create_scraper', return_value=scraper):
            with patch.object(main.STRATEGY_RUNTIME, 'evaluate', return_value={}):
                with patch('runner.main.time.sleep') as sleep_mock:
                    result = main.crawl_one(task, host_gates=host_gates, host_policy_overrides={host: policy})

        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])
        self.assertEqual(result['price'], '$12.50')
        self.assertEqual(scraper.get.call_count, 2)
        sleep_mock.assert_called()

    def test_crawl_one_keeps_failure_shape_after_retry_exhausted(self):
        task = {'url': 'https://example.test/products/sample'}
        policy = main.HostPolicy(max_parallel=1, min_interval_seconds=0.0, max_attempts=3, backoff_base_seconds=0.1, backoff_cap_seconds=0.2)
        host = main._normalize_host(task['url'])
        host_gates = {host: main.HostGate(policy)}
        scraper = Mock()
        scraper.get.side_effect = [
            FakeResponse(503),
            FakeResponse(503),
            FakeResponse(503),
        ]

        with patch('runner.main.cloudscraper.create_scraper', return_value=scraper):
            with patch('runner.main.time.sleep'):
                result = main.crawl_one(task, host_gates=host_gates, host_policy_overrides={host: policy})

        self.assertFalse(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['reason'], 'http_503')
        self.assertEqual(scraper.get.call_count, 3)

    def test_parse_tiers_keeps_unique_supported_values(self):
        self.assertEqual(main._parse_tiers('high,low,high,invalid'), ['high', 'low'])

    def test_build_http_client_uses_tls_impersonation_session_for_smokingpipes(self):
        with patch('runner.main.curl_requests.Session', return_value='curl-session') as curl_session_mock:
            client = main._build_http_client('www.smokingpipes.com')

        self.assertEqual(client, 'curl-session')
        curl_session_mock.assert_called_once()

    def test_request_with_headers_retries_alternate_impersonation_for_cgars_403(self):
        scraper = Mock()
        scraper.get.side_effect = [
            FakeResponse(403, '<html>blocked</html>'),
            FakeResponse(200, '<html>ok</html>'),
        ]

        response = main._request_with_headers(
            scraper,
            'https://www.cgarsltd.co.uk/product/sample',
            headers={},
            use_impersonation=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(scraper.get.call_count, 2)

    def test_request_with_headers_skips_unsupported_impersonation_profile(self):
        scraper = Mock()
        scraper.get.side_effect = [
            RuntimeError('unsupported profile'),
            FakeResponse(200, '<html>ok</html>'),
        ]

        with patch('runner.main.warn') as warn_mock:
            response = main._request_with_headers(
                scraper,
                'https://www.cgarsltd.co.uk/product/sample',
                headers={},
                use_impersonation=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(scraper.get.call_count, 2)
        self.assertTrue(any('profile=chrome' in call.args[0] for call in warn_mock.call_args_list))

    def test_crawl_one_short_circuits_http_for_selenium_strategy(self):
        task = {'url': 'https://selenium-only.example.test/product/1'}

        with patch.object(main.STRATEGY_RUNTIME, 'requires_selenium', return_value=True):
            with patch.object(
                main.STRATEGY_RUNTIME,
                'evaluate',
                return_value={'fetch_ok': True, 'in_stock': True, 'price': '$9.99', 'reason': ''},
            ) as evaluate_mock:
                with patch('runner.main.cloudscraper.create_scraper') as create_scraper_mock:
                    result = main.crawl_one(task)

        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])
        self.assertEqual(result['price'], '$9.99')
        evaluate_mock.assert_called_once_with(task, '')
        create_scraper_mock.assert_not_called()

    def test_crawl_one_skips_smokingpipes_without_reporting(self):
        task = {'url': 'https://www.smokingpipes.com/pipe-tobacco/sample/product_id/1'}

        with patch('runner.main.cloudscraper.create_scraper') as create_scraper_mock:
            result = main.crawl_one(task)

        self.assertFalse(result['fetch_ok'])
        self.assertTrue(result['skip_update'])
        self.assertEqual(result['reason'], 'skipped_smokingpipes')
        create_scraper_mock.assert_not_called()

    def test_crawl_one_fetches_fournoggins_from_ucp_catalog(self):
        task = {'url': 'https://4noggins.com/products/sample-blend'}
        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            'result': {
                'isError': False,
                'structuredContent': {
                    'products': [
                        {
                            'handle': 'different-blend',
                            'variants': [{'availability': {'available': True}, 'price': {'amount': 999}}],
                        },
                        {
                            'handle': 'sample-blend',
                            'variants': [
                                {'availability': {'available': True}, 'price': {'amount': 1250}},
                                {'availability': {'available': False}, 'price': {'amount': 1400}},
                            ],
                        },
                    ]
                },
            }
        }

        with patch('runner.main.curl_requests.post', return_value=response) as curl_post_mock:
            with patch.object(main.STRATEGY_RUNTIME, 'requires_selenium') as requires_selenium_mock:
                result = main.crawl_one(task)

        self.assertEqual(curl_post_mock.call_args.args[0], main.FOURNOGGINS_UCP_ENDPOINT)
        request_payload = curl_post_mock.call_args.kwargs['json']
        self.assertEqual(request_payload['params']['name'], 'search_catalog')
        self.assertEqual(request_payload['params']['arguments']['catalog']['query'], 'sample blend')
        self.assertEqual(
            request_payload['params']['arguments']['meta']['ucp-agent']['profile'],
            main.UCP_AGENT_PROFILE,
        )
        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])
        self.assertEqual(result['price'], '$12.50')
        self.assertEqual(result['url'], task['url'])
        requires_selenium_mock.assert_not_called()

    def test_crawl_one_fetches_70cigars_from_ucp_catalog(self):
        task = {'url': 'https://70cigars.com/products/sample-blend'}
        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            'result': {
                'isError': False,
                'structuredContent': {
                    'products': [
                        {
                            'handle': 'sample-blend',
                            'variants': [
                                {'availability': {'available': False}, 'price': {'amount': 2300}},
                            ],
                        },
                    ]
                },
            }
        }

        with patch('runner.main.curl_requests.post', return_value=response) as curl_post_mock:
            result = main.crawl_one(task)

        self.assertEqual(curl_post_mock.call_args.args[0], main.SEVENTYCIGARS_UCP_ENDPOINT)
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['price'], '$23.00')
        self.assertEqual(result['url'], task['url'])

    def test_crawl_one_fetches_tobaccolifestyle_nested_product_from_ucp_catalog(self):
        task = {
            'url': (
                'https://tobaccolifestyle.com/collections/pipe-tobacco/products/'
                'cornell-diehl-briar-fox-2oz-56-7-gram-tin'
            )
        }
        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            'result': {
                'isError': False,
                'structuredContent': {
                    'products': [
                        {
                            'handle': 'cornell-diehl-briar-fox-2oz-56-7-gram-tin',
                            'variants': [
                                {
                                    'availability': {'available': False},
                                    'price': {'amount': 8200, 'currency': 'HKD'},
                                },
                            ],
                        },
                    ]
                },
            }
        }

        with patch('runner.main.curl_requests.post', return_value=response) as curl_post_mock:
            result = main.crawl_one(task)

        self.assertEqual(curl_post_mock.call_args.args[0], main.TOBACCOLIFESTYLE_UCP_ENDPOINT)
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['price'], 'HK$82.00')

    def test_crawl_one_retries_transient_ucp_connection_failure(self):
        task = {'url': 'https://tobaccolifestyle.com/products/sample-blend'}
        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            'result': {
                'isError': False,
                'structuredContent': {
                    'products': [
                        {
                            'handle': 'sample-blend',
                            'variants': [
                                {
                                    'availability': {'available': True},
                                    'price': {'amount': 8200, 'currency': 'HKD'},
                                },
                            ],
                        },
                    ]
                },
            }
        }

        with patch('runner.main.curl_requests.post', side_effect=[RuntimeError('temporary'), response]) as curl_post_mock:
            with patch('runner.main.time.sleep') as sleep_mock:
                result = main.crawl_one(task)

        self.assertEqual(curl_post_mock.call_count, 2)
        sleep_mock.assert_called_once()
        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])

    def test_crawl_one_fetches_havahavana_from_ucp_catalog_and_removes_vat(self):
        task = {'url': 'https://www.havahavana.com/products/germains-brown-flake-pipe-tobacco-50g-tin'}
        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            'result': {
                'isError': False,
                'structuredContent': {
                    'products': [
                        {
                            'handle': 'germains-brown-flake-pipe-tobacco-50g-tin',
                            'variants': [
                                {
                                    'availability': {'available': False},
                                    'price': {'amount': 3000, 'currency': 'GBP'},
                                },
                            ],
                        },
                    ]
                },
            }
        }

        with patch('runner.main.curl_requests.post', return_value=response) as curl_post_mock:
            result = main.crawl_one(task)

        self.assertEqual(curl_post_mock.call_args.args[0], main.HAVAHAVANA_UCP_ENDPOINT)
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['price'], '£25.00')

    def test_crawl_one_matches_pipemoment_ucp_handle_with_brand_prefix(self):
        task = {'url': 'https://pipemoment.com/en/products/salty-dogs-50g'}
        response = Mock(status_code=200, headers={})
        response.json.return_value = {
            'result': {
                'isError': False,
                'structuredContent': {
                    'products': [
                        {
                            'handle': 'dtm-salty-dogs-50g',
                            'variants': [
                                {
                                    'availability': {'available': False},
                                    'price': {'amount': 1700, 'currency': 'USD'},
                                },
                            ],
                        },
                    ]
                },
            }
        }

        with patch('runner.main.curl_requests.post', return_value=response) as curl_post_mock:
            result = main.crawl_one(task)

        self.assertEqual(curl_post_mock.call_args.args[0], main.PIPEMOMENT_UCP_ENDPOINT)
        request_payload = curl_post_mock.call_args.kwargs['json']
        self.assertEqual(request_payload['params']['arguments']['catalog']['pagination']['limit'], 50)
        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['price'], '$17.00')
        self.assertEqual(result['reason'], '')

    def test_decrypt_pipeuncle_payload_uses_known_aes_key(self):
        payload = json.dumps({'totalStock': 2, 'sellPrice': 20.15}).encode('utf-8')
        pad_size = AES.block_size - (len(payload) % AES.block_size)
        cipher_text = AES.new(main.PIPEUNCLE_AES_KEY, AES.MODE_ECB).encrypt(payload + bytes([pad_size]) * pad_size)
        encoded = base64.b64encode(cipher_text).decode('ascii')

        detail = main._decrypt_pipeuncle_payload(encoded)

        self.assertEqual(detail['totalStock'], 2)
        self.assertEqual(detail['sellPrice'], 20.15)

    def test_crawl_one_fetches_pipeuncle_via_api_without_selenium(self):
        task = {'url': 'https://www.pipeuncle.com/detail/goods?id=261'}
        payload = json.dumps({'totalStock': 3, 'sellPrice': 20.15}).encode('utf-8')
        pad_size = AES.block_size - (len(payload) % AES.block_size)
        cipher_text = AES.new(main.PIPEUNCLE_AES_KEY, AES.MODE_ECB).encrypt(payload + bytes([pad_size]) * pad_size)
        encoded = base64.b64encode(cipher_text).decode('ascii')
        response = Mock(status_code=200)
        response.json.return_value = {'code': 200, 'data': encoded}

        with patch('runner.main.curl_requests.get', return_value=response) as curl_get_mock:
            with patch.object(main.STRATEGY_RUNTIME, 'requires_selenium') as requires_selenium_mock:
                with patch('runner.main.cloudscraper.create_scraper') as create_scraper_mock:
                    result = main.crawl_one(task)

        self.assertTrue(result['fetch_ok'])
        self.assertTrue(result['in_stock'])
        self.assertEqual(result['price'], '$20.15')
        requires_selenium_mock.assert_not_called()
        create_scraper_mock.assert_not_called()
        curl_get_mock.assert_called_once()

    def test_crawl_one_maps_missing_pipeuncle_product_to_out_of_stock(self):
        task = {'url': 'https://www.pipeuncle.com/detail/goods?id=2239'}
        response = Mock(status_code=200)
        response.json.return_value = {'code': 314, 'msg': '商品不存在!'}

        with patch('runner.main.curl_requests.get', return_value=response):
            result = main.crawl_one(task)

        self.assertTrue(result['fetch_ok'])
        self.assertFalse(result['in_stock'])
        self.assertEqual(result['reason'], 'pipeuncle_product_missing')

    def test_process_task_page_reports_only_reportable_results_and_logs_issues(self):
        results = [
            {'url': 'https://www.smokingpipes.com/p/1', 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': 'skipped_smokingpipes', 'skip_update': True},
            {'url': 'https://www.havahavana.com/p/1', 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': 'http_429'},
            {'url': 'https://example.test/p/1', 'fetch_ok': True, 'in_stock': True, 'price': '$9.99', 'reason': ''},
        ]
        config = main.RunConfig(
            tiers=['low'],
            source_mode='all',
            result_mode='subscription',
            page_size=100,
            max_workers=4,
            refresh_on_first_pull=True,
            shard_total=1,
            shard_index=0,
        )

        with patch('runner.main.crawl_all', return_value=results):
            with patch('runner.main.report_results', return_value={'updated': 2, 'push_enabled': False}) as report_mock:
                with patch('runner.main.info') as info_mock:
                    with patch('runner.main.warn') as warn_mock:
                        totals = main._process_task_page('run-1', 'low', config, 0, 0, [{'url': 'https://example.test'}])

        report_mock.assert_called_once_with(run_id='run-1', results=results[1:], result_mode='subscription')
        self.assertEqual(totals['total'], 3)
        self.assertEqual(totals['reported'], 2)
        self.assertEqual(totals['failed'], 1)
        self.assertEqual(totals['skipped'], 1)
        self.assertTrue(any('reported=2' in call.args[0] for call in info_mock.call_args_list))
        self.assertTrue(any('skipped_smokingpipes=1' in call.args[0] for call in warn_mock.call_args_list))

    def test_filter_tasks_for_shard_is_deterministic(self):
        tasks = [
            {'url': 'https://example.test/a'},
            {'url': 'https://example.test/b'},
            {'url': 'https://example.test/c'},
            {'url': 'https://example.test/d'},
        ]

        shard_zero = main._filter_tasks_for_shard(tasks, shard_total=3, shard_index=0)
        shard_zero_again = main._filter_tasks_for_shard(tasks, shard_total=3, shard_index=0)
        shard_one = main._filter_tasks_for_shard(tasks, shard_total=3, shard_index=1)
        shard_two = main._filter_tasks_for_shard(tasks, shard_total=3, shard_index=2)

        self.assertEqual(shard_zero, shard_zero_again)
        self.assertEqual(
            sorted(task['url'] for task in tasks),
            sorted(task['url'] for task in shard_zero + shard_one + shard_two),
        )
        self.assertEqual(len(tasks), len(shard_zero) + len(shard_one) + len(shard_two))

    def test_process_task_page_skips_empty_shard_without_crawling(self):
        config = main.RunConfig(
            tiers=['low'],
            source_mode='catalog',
            result_mode='catalog',
            page_size=100,
            max_workers=4,
            refresh_on_first_pull=True,
            shard_total=2,
            shard_index=0,
        )
        task = {'url': 'https://example.test/nonmatching'}
        while main._task_belongs_to_shard(task, config.shard_total, config.shard_index):
            task['url'] += '-x'

        with patch('runner.main.crawl_all') as crawl_mock:
            with patch('runner.main.report_results') as report_mock:
                totals = main._process_task_page('run-1', 'low', config, 0, 0, [task])

        crawl_mock.assert_not_called()
        report_mock.assert_not_called()
        self.assertEqual(totals['total'], 0)
        self.assertEqual(totals['reported'], 0)

    def test_main_paginates_and_runs_multiple_tiers(self):
        task_a = {'url': 'https://example.test/a'}
        task_b = {'url': 'https://example.test/b'}
        task_c = {'url': 'https://example.test/c'}

        pull_side_effect = [
            {'tasks': [task_a, task_b]},
            {'tasks': []},
            {'tasks': [task_c]},
        ]
        crawl_side_effect = [
            [
                {'url': task_a['url'], 'fetch_ok': True, 'in_stock': True, 'price': '$1.00', 'reason': ''},
                {'url': task_b['url'], 'fetch_ok': True, 'in_stock': False, 'price': '$1.20', 'reason': ''},
            ],
            [
                {'url': task_c['url'], 'fetch_ok': True, 'in_stock': False, 'price': '$2.00', 'reason': ''},
            ],
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    'os.environ',
                    {
                        'MONITOR_TIERS': 'low,high',
                        'MONITOR_SOURCE_MODE': 'subscription',
                        'MONITOR_PAGE_SIZE': '2',
                        'MAX_WORKERS': '4',
                    },
                    clear=False,
                )
            )
            load_mock = stack.enter_context(patch.object(main.STRATEGY_RUNTIME, 'load'))
            pull_mock = stack.enter_context(patch('runner.main.pull_task_page', side_effect=pull_side_effect))
            crawl_mock = stack.enter_context(patch('runner.main.crawl_all', side_effect=crawl_side_effect))
            report_mock = stack.enter_context(
                patch('runner.main.report_results', side_effect=[{'updated': 2}, {'updated': 1}])
            )
            info_mock = stack.enter_context(patch('runner.main.info'))
            warn_mock = stack.enter_context(patch('runner.main.warn'))

            main.main()

        load_mock.assert_called_once()
        self.assertEqual(pull_mock.call_count, 3)
        self.assertEqual(crawl_mock.call_count, 2)
        self.assertEqual(report_mock.call_count, 2)
        warn_mock.assert_not_called()

        first_call = pull_mock.call_args_list[0].kwargs
        self.assertEqual(first_call['tier'], 'low')
        self.assertEqual(first_call['offset'], 0)
        self.assertTrue(first_call['refresh'])
        self.assertEqual(first_call['source_mode'], 'subscription')

        second_call = pull_mock.call_args_list[1].kwargs
        self.assertEqual(second_call['tier'], 'low')
        self.assertEqual(second_call['offset'], 2)
        self.assertFalse(second_call['refresh'])

        third_call = pull_mock.call_args_list[2].kwargs
        self.assertEqual(third_call['tier'], 'high')
        self.assertEqual(third_call['offset'], 0)
        self.assertFalse(third_call['refresh'])

        self.assertTrue(any('运行结束' in call.args[0] for call in info_mock.call_args_list))
        self.assertTrue(any('reported=' in call.args[0] for call in info_mock.call_args_list))

    def test_load_run_config_defaults_catalog_result_mode_for_catalog_source(self):
        with patch.dict(
            'os.environ',
            {
                'MONITOR_SOURCE_MODE': 'catalog',
                'MONITOR_TIERS': 'low',
                'MONITOR_PAGE_SIZE': '50',
            },
            clear=False,
        ):
            config = main._load_run_config()

        self.assertEqual(config.source_mode, 'catalog')
        self.assertEqual(config.result_mode, 'catalog')

    def test_load_run_config_reads_shard_settings(self):
        with patch.dict(
            'os.environ',
            {
                'MONITOR_SOURCE_MODE': 'catalog',
                'MONITOR_TIERS': 'low',
                'MONITOR_SHARD_TOTAL': '3',
                'MONITOR_SHARD_INDEX': '2',
            },
            clear=False,
        ):
            config = main._load_run_config()

        self.assertEqual(config.shard_total, 3)
        self.assertEqual(config.shard_index, 2)


if __name__ == '__main__':
    unittest.main()
