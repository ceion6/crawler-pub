import os
from typing import Dict, List

from runner.auth_client import signed_post


def pull_task_page(
    tier: str,
    limit: int,
    offset: int = 0,
    refresh: bool = False,
    source_mode: str = 'all',
) -> Dict:
    base_url = os.environ['MONITOR_API_BASE_URL'].rstrip('/')
    url = f'{base_url}/internal/monitor/pull-tasks'
    payload = {
        'tier': tier,
        'limit': limit,
        'offset': offset,
        'refresh': bool(refresh),
        'source_mode': source_mode,
    }
    response = signed_post(url, payload, timeout=30)
    response.raise_for_status()
    return response.json()


def pull_tasks(
    tier: str,
    limit: int,
    offset: int = 0,
    refresh: bool = False,
    source_mode: str = 'all',
) -> List[Dict]:
    payload = pull_task_page(
        tier=tier,
        limit=limit,
        offset=offset,
        refresh=refresh,
        source_mode=source_mode,
    )
    return payload.get('tasks', [])
