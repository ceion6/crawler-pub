from collections import Counter
from unittest.mock import Mock, patch

from runner import main


def config():
    return main.RunConfig(tiers=['low'], source_mode='catalog', result_mode='catalog',
                          page_size=1, max_workers=1, refresh_on_first_pull=False,
                          shard_total=1, shard_index=0)


def response(status=200, retry_after=None):
    return Mock(status_code=status, headers=({'Retry-After': retry_after} if retry_after else {}),
                text='<html>Add to cart $12.50</html>')


def test_long_retry_after_suppresses_same_host_across_pages_and_not_other_hosts():
    run = config()
    scraper = Mock()
    scraper.get.side_effect = [response(503, '3600'), response()]
    with patch('runner.main._build_http_client', return_value=scraper), \
            patch.object(main.STRATEGY_RUNTIME, 'requires_selenium', return_value=False), \
            patch.object(main.STRATEGY_RUNTIME, 'evaluate', return_value={}), \
            patch('runner.main.report_results', return_value={'updated': 1}), \
            patch('runner.main.time.sleep') as sleep:
        one = main._process_task_page('run', 'low', run, 0, 0, [{'url': 'https://maintenance.test/a'}])
        two = main._process_task_page('run', 'low', run, 1, 1, [{'url': 'https://maintenance.test/b'}])
        other = main._process_task_page('run', 'low', run, 2, 2, [{'url': 'https://healthy.test/a'}])
    assert scraper.get.call_count == 2
    assert one['failed'] == two['failed'] == 1
    assert two['skipped'] == 0  # Deferred work remains a health failure, not an expected skip.
    assert other['ok'] == 1
    assert not any(call.args[0] >= 12 for call in sleep.call_args_list)
    scraper.get.side_effect = None
    scraper.get.return_value = response()
    with patch('runner.main._build_http_client', return_value=scraper), \
            patch.object(main.STRATEGY_RUNTIME, 'requires_selenium', return_value=False), \
            patch.object(main.STRATEGY_RUNTIME, 'evaluate', return_value={}):
        fresh = main.crawl_all([{'url': 'https://maintenance.test/a'}], 1, host_gates=config().host_gates)
    assert fresh[0]['fetch_ok'] is True
    assert scraper.get.call_count == 3


def test_404_is_per_product_and_new_run_can_probe_maintenance_host():
    scraper = Mock()
    scraper.get.side_effect = [response(404, '3600'), response()]
    run = config()
    with patch('runner.main._build_http_client', return_value=scraper), \
            patch.object(main.STRATEGY_RUNTIME, 'requires_selenium', return_value=False), \
            patch.object(main.STRATEGY_RUNTIME, 'evaluate', return_value={}):
        first = main.crawl_all([{'url': 'https://missing.test/a'}], 1, host_gates=run.host_gates)
        second = main.crawl_all([{'url': 'https://missing.test/b'}], 1, host_gates=run.host_gates)
    assert first[0]['reason'] == 'http_404'
    assert second[0]['fetch_ok'] is True
    assert scraper.get.call_count == 2
    fresh = config()
    assert not fresh.host_gates


def test_host_summary_keeps_cooldown_failures_visible(tmp_path, monkeypatch):
    results = [
        {'url': 'https://a.test/1', 'fetch_ok': True, 'in_stock': True, 'reason': ''},
        {'url': 'https://a.test/2', 'fetch_ok': False, 'in_stock': False, 'reason': 'host_cooldown:http_503'},
        {'url': 'https://a.test/3', 'fetch_ok': False, 'in_stock': False, 'reason': 'http_404'},
        {'url': 'https://www.smokingpipes.com/1', 'fetch_ok': False, 'reason': 'skipped_smokingpipes'},
    ]
    sites = main._summarize_sites(results)
    assert sites['a.test'] == Counter(total=3, ok=1, failed=2, skipped=0, cooldown=1)
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    main._check_run_health({'total': 4, 'reported': 3, 'ok': 1, 'failed': 2, 'skipped': 1, 'updated': 3},
                           Counter({'host_cooldown:http_503': 1, 'http_404': 1}), sites)
    assert 'a.test' in summary.read_text()
    assert 'Cooldown' in summary.read_text()
