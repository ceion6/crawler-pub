import os
import re
import time
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import cloudscraper
from requests import RequestException
from bs4 import BeautifulSoup

from runner.report_client import report_results
from runner.safe_logger import error, info, warn
from runner.strategy_runtime import StrategyRuntime
from runner.task_client import pull_tasks


IN_STOCK_TOKENS = ('in stock', 'available', 'add to cart', 'buy now')
OUT_OF_STOCK_TOKENS = ('out of stock', 'sold out', 'unavailable')
PRICE_PATTERN = re.compile(r'([$€£]\s?\d+(?:[.,]\d{2})?)')
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
STRATEGY_RUNTIME = StrategyRuntime()


@dataclass(frozen=True)
class HostPolicy:
    max_parallel: int = 2
    min_interval_seconds: float = 0.0
    max_attempts: int = 3
    backoff_base_seconds: float = 1.2
    backoff_cap_seconds: float = 12.0


DEFAULT_HOST_POLICY = HostPolicy()
DEFAULT_HOST_POLICY_OVERRIDES = {
    '4noggins.com': HostPolicy(max_parallel=1, min_interval_seconds=2.0, max_attempts=4, backoff_base_seconds=2.0),
    'www.4noggins.com': HostPolicy(max_parallel=1, min_interval_seconds=2.0, max_attempts=4, backoff_base_seconds=2.0),
}


class HostGate:
    def __init__(self, policy: HostPolicy):
        self._policy = policy
        self._semaphore = threading.BoundedSemaphore(value=max(1, policy.max_parallel))
        self._interval_lock = threading.Lock()
        self._next_ready_at = 0.0

    def acquire(self) -> None:
        self._semaphore.acquire()
        delay = 0.0
        if self._policy.min_interval_seconds > 0:
            with self._interval_lock:
                now = time.monotonic()
                delay = max(0.0, self._next_ready_at - now)
                scheduled = max(now, self._next_ready_at)
                self._next_ready_at = scheduled + self._policy.min_interval_seconds
        if delay > 0:
            time.sleep(delay)

    def release(self) -> None:
        self._semaphore.release()


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


def _normalize_host(url: str) -> str:
    return urlparse(url).netloc.lower().strip()


def _load_host_policy_overrides() -> Dict[str, HostPolicy]:
    raw = os.getenv('HOST_POLICY_JSON', '').strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f'HOST_POLICY_JSON 解析失败，忽略自定义 host 策略: {exc}')
        return {}
    if not isinstance(payload, dict):
        warn('HOST_POLICY_JSON 不是对象，忽略自定义 host 策略')
        return {}

    policies: Dict[str, HostPolicy] = {}
    for host, config in payload.items():
        if not isinstance(host, str) or not isinstance(config, dict):
            continue
        normalized_host = host.lower().strip()
        policies[normalized_host] = HostPolicy(
            max_parallel=max(1, int(config.get('max_parallel', DEFAULT_HOST_POLICY.max_parallel))),
            min_interval_seconds=max(0.0, float(config.get('min_interval_seconds', DEFAULT_HOST_POLICY.min_interval_seconds))),
            max_attempts=max(1, int(config.get('max_attempts', DEFAULT_HOST_POLICY.max_attempts))),
            backoff_base_seconds=max(0.1, float(config.get('backoff_base_seconds', DEFAULT_HOST_POLICY.backoff_base_seconds))),
            backoff_cap_seconds=max(0.5, float(config.get('backoff_cap_seconds', DEFAULT_HOST_POLICY.backoff_cap_seconds))),
        )
    return policies


def _resolve_host_policy(host: str, host_policy_overrides: Optional[Dict[str, HostPolicy]] = None) -> HostPolicy:
    if host_policy_overrides and host in host_policy_overrides:
        return host_policy_overrides[host]
    if host in DEFAULT_HOST_POLICY_OVERRIDES:
        return DEFAULT_HOST_POLICY_OVERRIDES[host]
    return DEFAULT_HOST_POLICY


def _build_host_gates(tasks: List[Dict], host_policy_overrides: Optional[Dict[str, HostPolicy]] = None) -> Dict[str, HostGate]:
    gates: Dict[str, HostGate] = {}
    for task in tasks:
        host = _normalize_host(task.get('url', ''))
        if not host or host in gates:
            continue
        gates[host] = HostGate(_resolve_host_policy(host, host_policy_overrides))
    return gates


def _retry_after_seconds(response) -> Optional[float]:
    value = (response.headers or {}).get('Retry-After')
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return max(0.0, float(value))
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return max(0.0, retry_at.timestamp() - time.time())


def _should_retry_status(status_code: int) -> bool:
    return status_code in TRANSIENT_STATUS_CODES


def _should_retry_exception(exc: Exception) -> bool:
    return isinstance(exc, RequestException)


def _compute_retry_delay(attempt_index: int, policy: HostPolicy, response=None) -> float:
    retry_after = _retry_after_seconds(response) if response is not None else None
    if retry_after is not None:
        return min(policy.backoff_cap_seconds, retry_after)
    backoff = policy.backoff_base_seconds * (2 ** max(0, attempt_index - 1))
    jitter = random.uniform(0.0, 0.35)
    return min(policy.backoff_cap_seconds, backoff + jitter)


def _request_once(scraper, url: str, gate: Optional[HostGate]):
    if gate is None:
        return scraper.get(url, timeout=20)
    gate.acquire()
    try:
        return scraper.get(url, timeout=20)
    finally:
        gate.release()


def crawl_one(
    task: Dict,
    host_gates: Optional[Dict[str, HostGate]] = None,
    host_policy_overrides: Optional[Dict[str, HostPolicy]] = None,
) -> Dict:
    url = task.get('url', '').strip()
    if not url:
        return {'url': '', 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': 'empty_url'}

    host = _normalize_host(url)
    host_policy = _resolve_host_policy(host, host_policy_overrides)
    host_gate = host_gates.get(host) if host_gates else None
    scraper = cloudscraper.create_scraper()
    for attempt in range(1, host_policy.max_attempts + 1):
        try:
            response = _request_once(scraper, url, host_gate)
            if response.status_code != 200:
                if attempt < host_policy.max_attempts and _should_retry_status(response.status_code):
                    delay = _compute_retry_delay(attempt, host_policy, response=response)
                    warn(
                        f'请求返回 {response.status_code}，准备重试 host={host} '
                        f'attempt={attempt}/{host_policy.max_attempts} delay={delay:.1f}s url={url}'
                    )
                    time.sleep(delay)
                    continue
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
            if attempt < host_policy.max_attempts and _should_retry_exception(exc):
                delay = _compute_retry_delay(attempt, host_policy)
                warn(
                    f'请求异常，准备重试 host={host} exception={type(exc).__name__} '
                    f'attempt={attempt}/{host_policy.max_attempts} delay={delay:.1f}s url={url}'
                )
                time.sleep(delay)
                continue
            return {
                'url': url,
                'fetch_ok': False,
                'in_stock': False,
                'price': '',
                'reason': f'exception:{type(exc).__name__}',
            }


def crawl_all(tasks: List[Dict], max_workers: int) -> List[Dict]:
    results: List[Dict] = []
    host_policy_overrides = _load_host_policy_overrides()
    host_gates = _build_host_gates(tasks, host_policy_overrides)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(crawl_one, t, host_gates, host_policy_overrides) for t in tasks]
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
