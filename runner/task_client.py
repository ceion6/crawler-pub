import os
from typing import Dict, List

from runner.auth_client import signed_post


def pull_tasks(tier: str, limit: int) -> List[Dict]:
    base_url = os.environ['MONITOR_API_BASE_URL'].rstrip('/')
    url = f'{base_url}/internal/monitor/pull-tasks'
    response = signed_post(url, {'tier': tier, 'limit': limit}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload.get('tasks', [])
