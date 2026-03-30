import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List

import cloudscraper
from bs4 import BeautifulSoup

from runner.report_client import report_results
from runner.safe_logger import error, info, warn
from runner.strategy_runtime import StrategyRuntime
from runner.task_client import pull_tasks


IN_STOCK_TOKENS = ('in stock', 'available', 'add to cart', 'buy now')
OUT_OF_STOCK_TOKENS = ('out of stock', 'sold out', 'unavailable')
PRICE_PATTERN = re.compile(r'([$€£]\s?\d+(?:[.,]\d{2})?)')
STRATEGY_RUNTIME = StrategyRuntime()


def _detect_stock(text: str) -> bool:
    lower = text.lower()
    if any(token in lower for token in OUT_OF_STOCK_TOKENS):
        return False
    if any(token in lower for token in IN_STOCK_TOKENS):
        return True
    return False


def _extract_price(text: str) -> str:
    match = PRICE_PATTERN.search(text)
    return match.group(1) if match else ''


def crawl_one(task: Dict) -> Dict:
    url = task.get('url', '')
    if not url:
        return {'url': '', 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': 'empty_url'}

    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(url, timeout=20)
        if response.status_code != 200:
            return {
                'url': url,
                'fetch_ok': False,
                'in_stock': False,
                'price': '',
                'reason': f'http_{response.status_code}',
            }
        soup = BeautifulSoup(response.text, 'lxml')
        text = soup.get_text(' ', strip=True)
        strategy_result = STRATEGY_RUNTIME.evaluate(task, response.text)
        if strategy_result:
            return {'url': url, **strategy_result}
        return {'url': url, 'fetch_ok': True, 'in_stock': _detect_stock(text), 'price': _extract_price(text), 'reason': ''}
    except Exception as exc:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'exception:{type(exc).__name__}',
        }


def crawl_all(tasks: List[Dict], max_workers: int) -> List[Dict]:
    results: List[Dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(crawl_one, t) for t in tasks]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def main() -> None:
    tier = os.getenv('MONITOR_TIER', 'low')
    limit = int(os.getenv('MONITOR_TASK_LIMIT', '200'))
    max_workers = int(os.getenv('MAX_WORKERS', '12'))
    run_id = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    start = time.time()
    STRATEGY_RUNTIME.load()
    tasks = pull_tasks(tier=tier, limit=limit)
    if not tasks:
        warn(f'任务为空，tier={tier}')
        return

    info(f'开始执行任务，tier={tier}，任务数={len(tasks)}')
    results = crawl_all(tasks, max_workers=max_workers)
    ok_count = sum(1 for r in results if r.get('fetch_ok'))
    stock_count = sum(1 for r in results if r.get('in_stock'))
    failed_count = len(results) - ok_count

    payload = report_results(run_id=run_id, results=results)
    elapsed = time.time() - start
    info(
        f'运行结束 run_id={run_id} total={len(results)} ok={ok_count} failed={failed_count} in_stock={stock_count} '
        f'updated={payload.get("updated", 0)} push_enabled={payload.get("push_enabled", False)} '
        f'elapsed={elapsed:.1f}s'
    )


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        error(f'执行失败: {type(exc).__name__}')
        raise
