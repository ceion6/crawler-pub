import os
from typing import Dict, List

from runner.auth_client import signed_post


def report_results(run_id: str, results: List[Dict]) -> Dict:
    base_url = os.environ['MONITOR_API_BASE_URL'].rstrip('/')
    url = f'{base_url}/internal/monitor/report-results'
    response = signed_post(url, {'run_id': run_id, 'results': results}, timeout=30)
    response.raise_for_status()
    return response.json()
