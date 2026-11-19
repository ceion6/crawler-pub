from unittest.mock import call, patch

import pytest

from runner.verify_monitor import verify_subscription_feed


def page(tasks=(), **overrides):
    return {
        'tasks': list(tasks), 'source_mode': 'subscription',
        'refreshed': 0, 'next_after_url': None, **overrides,
    }


def test_verification_checks_each_tier_with_no_refresh(capsys):
    with patch('runner.verify_monitor.pull_task_page', side_effect=[
        page([{'url': 'fixture-a'}], next_after_url='fixture-a'),
        page([{'url': 'fixture-b'}], next_after_url='fixture-b'),
        page(),
    ]) as pull:
        verify_subscription_feed()

    assert pull.call_args_list == [
        call('low', 1, refresh=False, source_mode='subscription', after_url=''),
        call('low', 1, refresh=False, source_mode='subscription', after_url='fixture-a'),
        call('high', 1, refresh=False, source_mode='subscription', after_url=''),
    ]
    output = capsys.readouterr().out
    assert 'sampled_tasks=2' in output and 'sampled_tasks=0' in output
    assert 'fixture-a' not in output


@pytest.mark.parametrize('response,expected', [
    (page(refreshed=1), 'refresh status'),
    ({'tasks': [], 'source_mode': 'subscription', 'refreshed': 0}, 'cursor pagination'),
])
def test_verification_requires_production_cursor_contract(response, expected):
    with patch('runner.verify_monitor.pull_task_page', return_value=response):
        with pytest.raises(RuntimeError, match=expected):
            verify_subscription_feed()
