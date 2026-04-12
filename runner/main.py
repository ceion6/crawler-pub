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
from curl_cffi import requests as curl_requests
from requests import RequestException
from bs4 import BeautifulSoup

from runner.report_client import report_results
from runner.safe_logger import error, info, warn
from runner.strategy_runtime import StrategyRuntime
from runner.task_client import pull_task_page


IN_STOCK_TOKENS = ('in stock', 'available', 'add to cart', 'buy now')
OUT_OF_STOCK_TOKENS = ('out of stock', 'sold out', 'unavailable')
PRICE_PATTERN = re.compile(r'([$€£]\s?\d+(?:[.,]\d{2})?)')
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
STRATEGY_RUNTIME = StrategyRuntime()
VALID_TIERS = ('low', 'high')
VALID_SOURCE_MODES = ('all', 'subscription', 'baseline')
TLS_IMPERSONATION_HOSTS = {
    'smokingpipes.com',
    'www.smokingpipes.com',
    'cgarsltd.co.uk',
    'www.cgarsltd.co.uk',
}


@dataclass(frozen=True)
class RunConfig:
    tiers: List[str]
    source_mode: str
    page_size: int
    max_workers: int
    refresh_on_first_pull: bool


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


def _build_http_client(host: str):
    if host in TLS_IMPERSONATION_HOSTS:
        return curl_requests.Session()
    return cloudscraper.create_scraper()


def _request_with_headers(scraper, url: str, headers: Dict[str, str], use_impersonation: bool):
    if use_impersonation:
        impersonate = os.getenv('MONITOR_HTTP_IMPERSONATE', 'chrome120').strip() or 'chrome120'
        return scraper.get(url, timeout=20, headers=headers, impersonate=impersonate)
    return scraper.get(url, timeout=20, headers=headers)


def _request_once(scraper, url: str, gate: Optional[HostGate], headers: Optional[Dict[str, str]] = None):
    request_headers = headers or {}
    use_impersonation = _normalize_host(url) in TLS_IMPERSONATION_HOSTS
    if gate is None:
        return _request_with_headers(scraper, url, request_headers, use_impersonation)
    gate.acquire()
    try:
        return _request_with_headers(scraper, url, request_headers, use_impersonation)
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
    if STRATEGY_RUNTIME.requires_selenium(task):
        return {'url': url, **STRATEGY_RUNTIME.evaluate(task, '')}

    scraper = _build_http_client(host)
    request_headers = STRATEGY_RUNTIME.get_request_headers(task)
    for attempt in range(1, host_policy.max_attempts + 1):
        try:
            response = _request_once(scraper, url, host_gate, headers=request_headers)
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


def _parse_bool(raw: str, default: bool = False) -> bool:
    text = (raw or '').strip().lower()
    if not text:
        return default
    return text in {'1', 'true', 'yes', 'y', 'on'}


def _parse_tiers(raw: str) -> List[str]:
    tiers: List[str] = []
    for part in (raw or '').split(','):
        tier = part.strip().lower()
        if tier in VALID_TIERS and tier not in tiers:
            tiers.append(tier)
    return tiers or ['low']


def _load_run_config() -> RunConfig:
    raw_tiers = os.getenv('MONITOR_TIERS') or os.getenv('MONITOR_TIER', 'low')
    source_mode = (os.getenv('MONITOR_SOURCE_MODE', 'all') or 'all').strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        source_mode = 'all'

    page_size_raw = os.getenv('MONITOR_PAGE_SIZE') or os.getenv('MONITOR_TASK_LIMIT', '200')
    page_size = max(1, int(page_size_raw))
    max_workers = max(1, int(os.getenv('MAX_WORKERS', '12')))
    refresh_on_first_pull = _parse_bool(os.getenv('MONITOR_REFRESH_ON_FIRST_PULL', 'true'), default=True)

    return RunConfig(
        tiers=_parse_tiers(raw_tiers),
        source_mode=source_mode,
        page_size=page_size,
        max_workers=max_workers,
        refresh_on_first_pull=refresh_on_first_pull,
    )


def _summarize_results(results: List[Dict]) -> Dict[str, int]:
    ok_count = sum(1 for row in results if row.get('fetch_ok'))
    stock_count = sum(1 for row in results if row.get('in_stock'))
    failed_count = len(results) - ok_count
    return {
        'total': len(results),
        'ok': ok_count,
        'failed': failed_count,
        'in_stock': stock_count,
    }


def _process_task_page(run_id: str, tier: str, config: RunConfig, page_index: int, offset: int, tasks: List[Dict]) -> Dict[str, int]:
    info(
        f'开始执行任务，tier={tier} source_mode={config.source_mode} '
        f'page={page_index} offset={offset} 任务数={len(tasks)}'
    )
    results = crawl_all(tasks, max_workers=config.max_workers)
    summary = _summarize_results(results)
    payload = report_results(run_id=run_id, results=results)
    info(
        f'页执行完成 run_id={run_id} tier={tier} source_mode={config.source_mode} '
        f'page={page_index} total={summary["total"]} ok={summary["ok"]} failed={summary["failed"]} '
        f'in_stock={summary["in_stock"]} updated={payload.get("updated", 0)} '
        f'push_enabled={payload.get("push_enabled", False)}'
    )
    return {
        'total': summary['total'],
        'ok': summary['ok'],
        'failed': summary['failed'],
        'in_stock': summary['in_stock'],
        'updated': int(payload.get('updated', 0)),
    }


def main() -> None:
    config = _load_run_config()
    run_id = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    start = time.time()
    STRATEGY_RUNTIME.load()
    totals = {
        'total': 0,
        'ok': 0,
        'failed': 0,
        'in_stock': 0,
        'updated': 0,
    }
    refresh_on_this_page = config.refresh_on_first_pull
    fetched_any_task = False

    for tier in config.tiers:
        offset = 0
        page_index = 0
        while True:
            payload = pull_task_page(
                tier=tier,
                limit=config.page_size,
                offset=offset,
                refresh=refresh_on_this_page,
                source_mode=config.source_mode,
            )
            refresh_on_this_page = False
            tasks = payload.get('tasks', [])

            if not tasks:
                if page_index == 0:
                    warn(f'任务为空，tier={tier} source_mode={config.source_mode}')
                break

            fetched_any_task = True
            page_totals = _process_task_page(run_id, tier, config, page_index, offset, tasks)
            for key in totals:
                totals[key] += page_totals[key]

            batch_size = len(tasks)
            offset += batch_size
            page_index += 1
            if batch_size < config.page_size:
                break

    if not fetched_any_task:
        return

    elapsed = time.time() - start
    info(
        f'运行结束 run_id={run_id} total={totals["total"]} ok={totals["ok"]} '
        f'failed={totals["failed"]} in_stock={totals["in_stock"]} updated={totals["updated"]} '
        f'elapsed={elapsed:.1f}s'
    )


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        error(f'执行失败: {type(exc).__name__}')
        raise
