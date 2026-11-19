import os
from typing import Dict, List, Optional

from runner.auth_client import signed_post


def pull_task_page(
    tier: str,
    limit: int,
    offset: int = 0,
    refresh: bool = False,
    source_mode: str = 'all',
    *,
    after_url: Optional[str] = None,
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
    if after_url is not None:
        payload['after_url'] = after_url
    response = signed_post(url, payload, timeout=30)
    response.raise_for_status()
    try:
        page = response.json()
    except ValueError:
        raise RuntimeError('拉取任务响应无效：不是有效 JSON') from None
    if not isinstance(page, dict) or page.get('status') != 'ok':
        raise RuntimeError('拉取任务响应无效：缺少成功确认')
    tasks = page.get('tasks')
    if not isinstance(tasks, list) or any(
        not isinstance(task, dict) or not isinstance(task.get('url'), str) or not task['url'].strip()
        for task in tasks
    ):
        raise RuntimeError('拉取任务响应无效：任务列表格式错误')
    if after_url is not None and 'next_after_url' in page:
        _validate_cursor_page(tasks, limit, after_url, page['next_after_url'])
    return page


def _validate_cursor_page(tasks: List[Dict], limit: int, after_url: str, next_after_url: object) -> None:
    page_size = max(1, limit)
    if len(tasks) > page_size:
        raise RuntimeError('拉取任务响应无效：游标页超过请求数量')
    previous_url = after_url
    for task in tasks:
        url = task['url']
        if url <= previous_url:
            raise RuntimeError('拉取任务响应无效：任务游标未前进')
        previous_url = url
    expected_cursor = previous_url if len(tasks) == page_size else None
    if next_after_url != expected_cursor:
        raise RuntimeError('拉取任务响应无效：下一页游标与页面不一致')


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
    return payload['tasks']
