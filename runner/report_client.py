import os
from typing import Dict, List

from runner.auth_client import signed_post


def report_results(run_id: str, results: List[Dict], result_mode: str = 'subscription') -> Dict:
    base_url = os.environ['MONITOR_API_BASE_URL'].rstrip('/')
    url = f'{base_url}/internal/monitor/report-results'
    response = signed_post(
        url,
        {
            'run_id': run_id,
            'results': results,
            'result_mode': result_mode,
        },
        timeout=30,
    )
    response.raise_for_status()
    try:
        acknowledgement = response.json()
    except ValueError:
        raise RuntimeError('结果回报响应无效：不是有效 JSON') from None
    if not isinstance(acknowledgement, dict) or acknowledgement.get('status') != 'ok':
        raise RuntimeError('结果回报响应无效：缺少成功确认')
    updated = acknowledgement.get('updated')
    if type(updated) is not int or not 0 <= updated <= len(results):
        raise RuntimeError('结果回报响应无效：服务器入库数量错误')
    return acknowledgement
