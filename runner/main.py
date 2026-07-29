import base64
import hashlib
import os
import re
import time
import json
import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import cloudscraper
import requests
from curl_cffi import requests as curl_requests
from requests import RequestException
from bs4 import BeautifulSoup
from Crypto.Cipher import AES

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
VALID_SOURCE_MODES = ('all', 'subscription', 'baseline', 'catalog')
VALID_RESULT_MODES = ('subscription', 'catalog')
SKIPPED_HOSTS = {
    'smokingpipes.com',
    'www.smokingpipes.com',
}
PIPEUNCLE_HOSTS = {
    'pipeuncle.com',
    'www.pipeuncle.com',
}
FOURNOGGINS_HOSTS = {
    '4noggins.com',
    'www.4noggins.com',
}
SEVENTYCIGARS_HOSTS = {
    '70cigars.com',
    'www.70cigars.com',
}
TOBACCOLIFESTYLE_HOSTS = {
    'tobaccolifestyle.com',
    'www.tobaccolifestyle.com',
}
HAVAHAVANA_HOSTS = {
    'havahavana.com',
    'www.havahavana.com',
}
PIPEMOMENT_HOSTS = {
    'pipemoment.com',
    'www.pipemoment.com',
}
FOURNOGGINS_UCP_ENDPOINT = 'https://4noggins-com.myshopify.com/api/ucp/mcp'
SEVENTYCIGARS_UCP_ENDPOINT = 'https://70cigars.com/api/ucp/mcp'
TOBACCOLIFESTYLE_UCP_ENDPOINT = 'https://tobaccolifestyle.com/api/ucp/mcp'
HAVAHAVANA_UCP_ENDPOINT = 'https://www.havahavana.com/api/ucp/mcp'
PIPEMOMENT_UCP_ENDPOINT = 'https://pipemoment.com/api/ucp/mcp'
PIPEMOMENT_CATALOG_ENDPOINT = (
    'https://pipemoment.com/en/collections/all-pipetobacco/'
    'products.json?limit=250&page=1'
)
UCP_AGENT_PROFILE = 'https://youdou.shop/.well-known/ucp'
PIPEUNCLE_AES_KEY = b'0f5ef28c56b64e67'
TLS_IMPERSONATION_HOSTS = {
    'smokingpipes.com',
    'www.smokingpipes.com',
    '4noggins.com',
    'www.4noggins.com',
    '70cigars.com',
    'www.70cigars.com',
    'cgarsltd.co.uk',
    'www.cgarsltd.co.uk',
    'havahavana.com',
    'www.havahavana.com',
    'tobaccolifestyle.com',
    'www.tobaccolifestyle.com',
    'pipemoment.com',
    'www.pipemoment.com',
}
HOST_IMPERSONATION_FALLBACKS = {
    'cgarsltd.co.uk': ('chrome', 'chrome136', 'chrome131', 'chrome124', 'edge101'),
    'www.cgarsltd.co.uk': ('chrome', 'chrome136', 'chrome131', 'chrome124', 'edge101'),
}
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Upgrade-Insecure-Requests': '1',
}
_pipemoment_catalog_lock = threading.Lock()
_pipemoment_catalog_cache: Optional[Dict[str, Dict]] = None


@dataclass(frozen=True)
class RunConfig:
    tiers: List[str]
    source_mode: str
    result_mode: str
    page_size: int
    max_workers: int
    refresh_on_first_pull: bool
    shard_total: int
    shard_index: int


@dataclass(frozen=True)
class HostPolicy:
    max_parallel: int = 2
    min_interval_seconds: float = 0.0
    max_attempts: int = 3
    backoff_base_seconds: float = 1.2
    backoff_cap_seconds: float = 12.0


DEFAULT_HOST_POLICY = HostPolicy()
DEFAULT_HOST_POLICY_OVERRIDES = {
    '4noggins.com': HostPolicy(max_parallel=1, min_interval_seconds=5.0, max_attempts=4, backoff_base_seconds=2.5),
    'www.4noggins.com': HostPolicy(max_parallel=1, min_interval_seconds=5.0, max_attempts=4, backoff_base_seconds=2.5),
    '70cigars.com': HostPolicy(max_parallel=1, min_interval_seconds=3.0, max_attempts=4, backoff_base_seconds=2.0),
    'www.70cigars.com': HostPolicy(max_parallel=1, min_interval_seconds=3.0, max_attempts=4, backoff_base_seconds=2.0),
    'cgarsltd.co.uk': HostPolicy(max_parallel=1, min_interval_seconds=2.5, max_attempts=3, backoff_base_seconds=2.0),
    'www.cgarsltd.co.uk': HostPolicy(max_parallel=1, min_interval_seconds=2.5, max_attempts=3, backoff_base_seconds=2.0),
    'dreamingpipes.com': HostPolicy(max_parallel=1, min_interval_seconds=1.5, max_attempts=4, backoff_base_seconds=2.0),
    'www.dreamingpipes.com': HostPolicy(max_parallel=1, min_interval_seconds=1.5, max_attempts=4, backoff_base_seconds=2.0),
    'havahavana.com': HostPolicy(max_parallel=1, min_interval_seconds=2.5, max_attempts=4, backoff_base_seconds=2.0),
    'www.havahavana.com': HostPolicy(max_parallel=1, min_interval_seconds=2.5, max_attempts=4, backoff_base_seconds=2.0),
    'pipeuncle.com': HostPolicy(max_parallel=1, min_interval_seconds=1.0, max_attempts=3, backoff_base_seconds=1.5),
    'www.pipeuncle.com': HostPolicy(max_parallel=1, min_interval_seconds=1.0, max_attempts=3, backoff_base_seconds=1.5),
    'tobaccolifestyle.com': HostPolicy(max_parallel=1, min_interval_seconds=2.5, max_attempts=4, backoff_base_seconds=2.0),
    'www.tobaccolifestyle.com': HostPolicy(max_parallel=1, min_interval_seconds=2.5, max_attempts=4, backoff_base_seconds=2.0),
    'pipemoment.com': HostPolicy(max_parallel=1, min_interval_seconds=1.5, max_attempts=4, backoff_base_seconds=1.5),
    'www.pipemoment.com': HostPolicy(max_parallel=1, min_interval_seconds=1.5, max_attempts=4, backoff_base_seconds=1.5),
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


def _format_price(value) -> str:
    if value in (None, ''):
        return ''
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ''
        if text[0] in '$€£¥':
            return text
        value = text
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return f'${numeric:.2f}'


def _format_ucp_price(amount, currency: str = 'USD', divisor: float = 1.0) -> str:
    try:
        numeric = float(amount) / 100 / divisor
    except (TypeError, ValueError, ZeroDivisionError):
        return ''

    currency = (currency or 'USD').upper()
    symbol = {
        'USD': '$',
        'GBP': '£',
        'HKD': 'HK$',
        'EUR': '€',
    }.get(currency)
    return f'{symbol}{numeric:.2f}' if symbol else f'{currency} {numeric:.2f}'


def _normalize_host(url: str) -> str:
    return urlparse(url).netloc.lower().strip()


def _format_counter(counter: Counter, limit: int = 12) -> str:
    if not counter:
        return ''
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return ', '.join(f'{key}={value}' for key, value in items[:limit])


def _is_skip_result(result: Dict) -> bool:
    return bool(result.get('skip_update'))


def _build_skip_result(url: str, reason: str) -> Dict:
    return {
        'url': url,
        'fetch_ok': False,
        'in_stock': False,
        'price': '',
        'reason': reason,
        'skip_update': True,
    }


def _extract_pipeuncle_goods_id(url: str) -> str:
    goods_ids = parse_qs(urlparse(url).query).get('id', [])
    if not goods_ids:
        return ''
    goods_id = str(goods_ids[0]).strip()
    return goods_id if goods_id.isdigit() else ''


def _decrypt_pipeuncle_payload(encrypted_text: str) -> Dict:
    raw = base64.b64decode((encrypted_text or '').strip())
    if not raw:
        raise ValueError('empty_pipeuncle_payload')

    plain = AES.new(PIPEUNCLE_AES_KEY, AES.MODE_ECB).decrypt(raw)
    pad_size = plain[-1]
    if pad_size < 1 or pad_size > AES.block_size:
        raise ValueError('invalid_pipeuncle_padding')
    if plain[-pad_size:] != bytes([pad_size]) * pad_size:
        raise ValueError('corrupted_pipeuncle_padding')

    return json.loads(plain[:-pad_size].decode('utf-8'))


def _crawl_pipeuncle_via_api(url: str, gate: Optional[HostGate] = None) -> Dict:
    goods_id = _extract_pipeuncle_goods_id(url)
    if not goods_id:
        return {'url': url, 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': 'pipeuncle_goods_id_missing'}

    api_url = f'https://www.pipeuncle.com/api/goods/detail?id={goods_id}&activity=default'
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://www.pipeuncle.com',
        'Referer': url,
    }
    impersonate = os.getenv('MONITOR_HTTP_IMPERSONATE', 'chrome120').strip() or 'chrome120'

    if gate is not None:
        gate.acquire()
    try:
        response = curl_requests.get(api_url, timeout=20, headers=headers, impersonate=impersonate)
    except Exception as exc:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'pipeuncle_api_exception:{type(exc).__name__}',
        }
    finally:
        if gate is not None:
            gate.release()

    if response.status_code != 200:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'pipeuncle_http_{response.status_code}',
        }

    try:
        payload = response.json()
    except ValueError as exc:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'pipeuncle_json_error:{type(exc).__name__}',
        }

    if payload.get('code') == 314:
        return {
            'url': url,
            'fetch_ok': True,
            'in_stock': False,
            'price': '',
            'reason': 'pipeuncle_product_missing',
        }

    if payload.get('code') != 200:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'pipeuncle_api_code_{payload.get("code", "unknown")}',
        }

    try:
        detail = _decrypt_pipeuncle_payload(payload.get('data', ''))
    except Exception as exc:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'pipeuncle_decrypt_failed:{type(exc).__name__}',
        }

    try:
        total_stock = int(detail.get('totalStock') or 0)
    except (TypeError, ValueError):
        total_stock = 0

    return {
        'url': url,
        'fetch_ok': True,
        'in_stock': total_stock > 0,
        'price': _format_price(detail.get('sellPrice')),
        'reason': '',
    }


def _crawl_shopify_via_ucp(
    url: str,
    endpoint: str,
    reason_prefix: str,
    price_divisor: float = 1.0,
    search_limit: int = 10,
    gate: Optional[HostGate] = None,
) -> Dict:
    path_parts = [part for part in urlparse(url).path.split('/') if part]
    try:
        products_index = path_parts.index('products')
        handle = path_parts[products_index + 1]
    except (ValueError, IndexError):
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'{reason_prefix}_handle_missing',
        }

    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    request_payload = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'tools/call',
        'params': {
            'name': 'search_catalog',
            'arguments': {
                'meta': {
                    'ucp-agent': {
                        'profile': UCP_AGENT_PROFILE,
                    }
                },
                'catalog': {
                    'query': handle.replace('-', ' '),
                    'pagination': {'limit': search_limit},
                },
            },
        },
    }
    impersonate = os.getenv('MONITOR_HTTP_IMPERSONATE', 'chrome').strip() or 'chrome'

    response = None
    for attempt in range(1, 3):
        if gate is not None:
            gate.acquire()
        try:
            response = curl_requests.post(
                endpoint,
                timeout=30,
                headers=headers,
                json=request_payload,
                impersonate=impersonate,
            )
        except Exception as exc:
            if attempt < 2:
                time.sleep(1.0)
                continue
            return {
                'url': url,
                'fetch_ok': False,
                'in_stock': False,
                'price': '',
                'reason': f'{reason_prefix}_api_exception:{type(exc).__name__}',
            }
        finally:
            if gate is not None:
                gate.release()

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < 2:
            time.sleep(1.0)
            continue
        break

    if response is None or response.status_code != 200:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'{reason_prefix}_http_{response.status_code if response is not None else "unknown"}',
        }

    try:
        payload = response.json()
    except ValueError as exc:
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'{reason_prefix}_json_error:{type(exc).__name__}',
        }

    if not isinstance(payload, dict):
        return {'url': url, 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': f'{reason_prefix}_ucp_invalid'}

    if payload.get('error'):
        error_code = payload['error'].get('code', 'unknown') if isinstance(payload['error'], dict) else 'unknown'
        return {
            'url': url,
            'fetch_ok': False,
            'in_stock': False,
            'price': '',
            'reason': f'{reason_prefix}_ucp_error_{error_code}',
        }

    result = payload.get('result')
    if not isinstance(result, dict) or result.get('isError'):
        return {'url': url, 'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': f'{reason_prefix}_ucp_invalid'}

    structured_content = result.get('structuredContent')
    products = structured_content.get('products', []) if isinstance(structured_content, dict) else []
    product = next(
        (item for item in products if isinstance(item, dict) and item.get('handle') == handle),
        None,
    )
    if product is None:
        alias_products = [
            item
            for item in products
            if isinstance(item, dict)
            and isinstance(item.get('handle'), str)
            and (
                item['handle'].endswith(f'-{handle}')
                or handle.endswith(f'-{item["handle"]}')
            )
        ]
        if len(alias_products) == 1:
            product = alias_products[0]
    if product is None:
        return {
            'url': url,
            'fetch_ok': True,
            'in_stock': False,
            'price': '',
            'reason': f'{reason_prefix}_product_missing',
        }

    variants = product.get('variants') if isinstance(product, dict) else []
    variants = variants if isinstance(variants, list) else []
    available_variants = [
        variant
        for variant in variants
        if isinstance(variant, dict)
        and isinstance(variant.get('availability'), dict)
        and variant['availability'].get('available')
    ]
    price_variant = available_variants[0] if available_variants else next(
        (variant for variant in variants if isinstance(variant, dict)),
        {},
    )
    price_detail = price_variant.get('price')
    price_cents = price_detail.get('amount') if isinstance(price_detail, dict) else None
    currency = price_detail.get('currency', 'USD') if isinstance(price_detail, dict) else 'USD'
    price = _format_ucp_price(price_cents, currency, price_divisor) if price_cents is not None else ''

    return {
        'url': url,
        'fetch_ok': True,
        'in_stock': bool(available_variants),
        'price': price,
        'reason': '',
    }


def _get_pipemoment_json(
    endpoint: str,
    gate: Optional[HostGate] = None,
    policy: Optional[HostPolicy] = None,
) -> Optional[Dict]:
    headers = {'Accept': 'application/json'}
    request_policy = policy or DEFAULT_HOST_POLICY_OVERRIDES['pipemoment.com']
    response = None
    for attempt in range(1, request_policy.max_attempts + 1):
        if gate is not None:
            gate.acquire()
        try:
            response = requests.get(
                endpoint,
                timeout=20,
                headers=headers,
            )
        except Exception:
            if attempt < request_policy.max_attempts:
                time.sleep(_compute_retry_delay(attempt, request_policy))
                continue
            return None
        finally:
            if gate is not None:
                gate.release()

        if (
            response.status_code in TRANSIENT_STATUS_CODES
            and attempt < request_policy.max_attempts
        ):
            time.sleep(_compute_retry_delay(attempt, request_policy, response=response))
            continue
        break

    if response is None or response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_pipemoment_catalog(
    gate: Optional[HostGate] = None,
    policy: Optional[HostPolicy] = None,
) -> Optional[Dict[str, Dict]]:
    global _pipemoment_catalog_cache
    if _pipemoment_catalog_cache is not None:
        return _pipemoment_catalog_cache

    with _pipemoment_catalog_lock:
        if _pipemoment_catalog_cache is not None:
            return _pipemoment_catalog_cache
        payload = _get_pipemoment_json(
            PIPEMOMENT_CATALOG_ENDPOINT,
            gate=gate,
            policy=policy,
        )
        products = payload.get('products') if isinstance(payload, dict) else None
        if not isinstance(products, list):
            return None
        _pipemoment_catalog_cache = {
            str(product['handle']): product
            for product in products
            if isinstance(product, dict) and product.get('handle')
        }
        return _pipemoment_catalog_cache


def _select_pipemoment_variant(product: Dict, parsed_url) -> Optional[Dict]:
    variants = product.get('variants')
    if not isinstance(variants, list) or not variants:
        return None

    target_variant = None
    target_ids = parse_qs(parsed_url.query).get('variant', [])
    if target_ids:
        target_id = str(target_ids[0])
        target_variant = next(
            (
                variant
                for variant in variants
                if isinstance(variant, dict) and str(variant.get('id')) == target_id
            ),
            None,
        )
        if target_variant is None:
            return None

    available_variants = [
        variant
        for variant in variants
        if isinstance(variant, dict) and bool(variant.get('available'))
    ]
    selected_variant = target_variant or (
        available_variants[0]
        if available_variants
        else next((variant for variant in variants if isinstance(variant, dict)), {})
    )
    return {
        'variant': selected_variant,
        'in_stock': (
            bool(target_variant.get('available'))
            if target_variant
            else bool(available_variants)
        ),
    }


def _crawl_pipemoment_via_catalog_json(
    url: str,
    gate: Optional[HostGate] = None,
    policy: Optional[HostPolicy] = None,
) -> Optional[Dict]:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split('/') if part]
    try:
        products_index = path_parts.index('products')
        handle = path_parts[products_index + 1]
    except (ValueError, IndexError):
        return None

    catalog = _load_pipemoment_catalog(gate=gate, policy=policy)
    product = catalog.get(handle) if catalog else None
    selected = _select_pipemoment_variant(product, parsed) if product else None
    if selected is None:
        return None
    price = _format_price(selected['variant'].get('price'))
    return {
        'url': url,
        'fetch_ok': True,
        'in_stock': selected['in_stock'],
        'price': price,
        'reason': '',
    }


def _crawl_pipemoment_via_product_json(
    url: str,
    gate: Optional[HostGate] = None,
    policy: Optional[HostPolicy] = None,
) -> Optional[Dict]:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split('/') if part]
    try:
        products_index = path_parts.index('products')
        handle = path_parts[products_index + 1]
    except (ValueError, IndexError):
        return None

    product_url = f'{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip("/")}.js'
    product = _get_pipemoment_json(product_url, gate=gate, policy=policy)
    if not isinstance(product, dict) or product.get('handle') != handle:
        return None
    selected = _select_pipemoment_variant(product, parsed)
    if selected is None:
        return None
    selected_variant = selected['variant']
    price = _format_ucp_price(selected_variant.get('price'), 'USD')
    return {
        'url': url,
        'fetch_ok': True,
        'in_stock': selected['in_stock'],
        'price': price,
        'reason': '',
    }


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
    request_headers = dict(DEFAULT_REQUEST_HEADERS)
    request_headers.update(headers or {})
    if use_impersonation:
        host = _normalize_host(url)
        impersonate = os.getenv('MONITOR_HTTP_IMPERSONATE', 'chrome').strip() or 'chrome'
        variants = [impersonate]
        for candidate in HOST_IMPERSONATION_FALLBACKS.get(host, ()):
            if candidate not in variants:
                variants.append(candidate)
        response = None
        last_exception = None
        for candidate in variants:
            try:
                response = scraper.get(url, timeout=20, headers=request_headers, impersonate=candidate)
            except Exception as exc:
                last_exception = exc
                warn(f'TLS impersonation profile failed host={host} profile={candidate} exception={type(exc).__name__}')
                continue
            if response.status_code != 403:
                return response
        if response is not None:
            return response
        if last_exception is not None:
            raise last_exception
        raise RuntimeError('all_impersonation_profiles_failed')
    return scraper.get(url, timeout=20, headers=request_headers)


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
    host_gate = host_gates.get(host) if host_gates else None
    if host in SKIPPED_HOSTS:
        return _build_skip_result(url, 'skipped_smokingpipes')
    if host in PIPEUNCLE_HOSTS:
        return _crawl_pipeuncle_via_api(url, gate=host_gate)
    if host in FOURNOGGINS_HOSTS:
        return _crawl_shopify_via_ucp(
            url,
            FOURNOGGINS_UCP_ENDPOINT,
            'fournoggins',
            gate=host_gate,
        )
    if host in SEVENTYCIGARS_HOSTS:
        return _crawl_shopify_via_ucp(
            url,
            SEVENTYCIGARS_UCP_ENDPOINT,
            'seventycigars',
            gate=host_gate,
        )
    if host in TOBACCOLIFESTYLE_HOSTS:
        return _crawl_shopify_via_ucp(
            url,
            TOBACCOLIFESTYLE_UCP_ENDPOINT,
            'tobaccolifestyle',
            gate=host_gate,
        )
    if host in HAVAHAVANA_HOSTS:
        return _crawl_shopify_via_ucp(
            url,
            HAVAHAVANA_UCP_ENDPOINT,
            'havahavana',
            price_divisor=1.2,
            gate=host_gate,
        )
    if host in PIPEMOMENT_HOSTS:
        pipemoment_policy = _resolve_host_policy(host, host_policy_overrides)
        catalog_json_result = _crawl_pipemoment_via_catalog_json(
            url,
            gate=host_gate,
            policy=pipemoment_policy,
        )
        if catalog_json_result is not None:
            return catalog_json_result
        product_json_result = _crawl_pipemoment_via_product_json(
            url,
            gate=host_gate,
            policy=pipemoment_policy,
        )
        if product_json_result is not None:
            return product_json_result
        return _crawl_shopify_via_ucp(
            url,
            PIPEMOMENT_UCP_ENDPOINT,
            'pipemoment',
            search_limit=50,
            gate=host_gate,
        )

    host_policy = _resolve_host_policy(host, host_policy_overrides)
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


def _parse_shard_config() -> Tuple[int, int]:
    shard_total = max(1, int(os.getenv('MONITOR_SHARD_TOTAL', '1')))
    shard_index = int(os.getenv('MONITOR_SHARD_INDEX', '0'))
    if shard_index < 0 or shard_index >= shard_total:
        raise ValueError(f'MONITOR_SHARD_INDEX 超出范围: index={shard_index} total={shard_total}')
    return shard_total, shard_index


def _task_shard_key(task: Dict) -> str:
    url = str(task.get('url', '') or '').strip()
    if url:
        return url
    payload = {
        'product_name': str(task.get('product_name', '') or ''),
        'site_name': str(task.get('site_name', '') or ''),
        'brand': str(task.get('brand', '') or ''),
        'category': str(task.get('category', '') or ''),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _task_belongs_to_shard(task: Dict, shard_total: int, shard_index: int) -> bool:
    if shard_total <= 1:
        return True
    key = _task_shard_key(task).encode('utf-8')
    digest = hashlib.sha256(key).digest()
    bucket = int.from_bytes(digest[:8], 'big') % shard_total
    return bucket == shard_index


def _filter_tasks_for_shard(tasks: List[Dict], shard_total: int, shard_index: int) -> List[Dict]:
    if shard_total <= 1:
        return list(tasks)
    return [task for task in tasks if _task_belongs_to_shard(task, shard_total, shard_index)]


def _load_run_config() -> RunConfig:
    raw_tiers = os.getenv('MONITOR_TIERS') or os.getenv('MONITOR_TIER', 'low')
    source_mode = (os.getenv('MONITOR_SOURCE_MODE', 'all') or 'all').strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        source_mode = 'all'
    result_mode = (os.getenv('MONITOR_RESULT_MODE', '') or '').strip().lower()
    if result_mode not in VALID_RESULT_MODES:
        result_mode = 'catalog' if source_mode == 'catalog' else 'subscription'

    page_size_raw = os.getenv('MONITOR_PAGE_SIZE') or os.getenv('MONITOR_TASK_LIMIT', '200')
    page_size = max(1, int(page_size_raw))
    max_workers = max(1, int(os.getenv('MAX_WORKERS', '12')))
    refresh_on_first_pull = _parse_bool(os.getenv('MONITOR_REFRESH_ON_FIRST_PULL', 'true'), default=True)
    shard_total, shard_index = _parse_shard_config()

    return RunConfig(
        tiers=_parse_tiers(raw_tiers),
        source_mode=source_mode,
        result_mode=result_mode,
        page_size=page_size,
        max_workers=max_workers,
        refresh_on_first_pull=refresh_on_first_pull,
        shard_total=shard_total,
        shard_index=shard_index,
    )


def _summarize_results(results: List[Dict]) -> Dict[str, int]:
    reportable_results = [row for row in results if not _is_skip_result(row)]
    skipped_count = len(results) - len(reportable_results)
    ok_count = sum(1 for row in reportable_results if row.get('fetch_ok'))
    stock_count = sum(1 for row in reportable_results if row.get('in_stock'))
    failed_count = len(reportable_results) - ok_count
    return {
        'total': len(results),
        'reported': len(reportable_results),
        'ok': ok_count,
        'failed': failed_count,
        'skipped': skipped_count,
        'in_stock': stock_count,
    }


def _summarize_issues(results: List[Dict]) -> Tuple[Counter, Counter]:
    reason_counts: Counter = Counter()
    host_counts: Counter = Counter()
    for row in results:
        if row.get('fetch_ok') and not _is_skip_result(row):
            continue
        reason = str(row.get('reason', '') or 'unknown').strip() or 'unknown'
        host = _normalize_host(row.get('url', '')) or 'unknown'
        reason_counts[reason] += 1
        host_counts[host] += 1
    return reason_counts, host_counts


def _log_issue_summary(scope: str, run_id: str, tier: str, source_mode: str, page_index: Optional[int], reason_counts: Counter, host_counts: Counter) -> None:
    if not reason_counts:
        return
    page_suffix = '' if page_index is None else f' page={page_index}'
    warn(
        f'{scope}问题聚合 run_id={run_id} tier={tier} source_mode={source_mode}'
        f'{page_suffix} reasons={_format_counter(reason_counts)}'
    )
    warn(
        f'{scope}站点聚合 run_id={run_id} tier={tier} source_mode={source_mode}'
        f'{page_suffix} hosts={_format_counter(host_counts)}'
    )


def _process_task_page(run_id: str, tier: str, config: RunConfig, page_index: int, offset: int, tasks: List[Dict]) -> Dict[str, object]:
    filtered_tasks = _filter_tasks_for_shard(tasks, config.shard_total, config.shard_index)
    info(
        f'开始执行任务，tier={tier} source_mode={config.source_mode} '
        f'page={page_index} offset={offset} shard={config.shard_index + 1}/{config.shard_total} '
        f'原始任务数={len(tasks)} 分片任务数={len(filtered_tasks)}'
    )
    if not filtered_tasks:
        return {
            'total': 0,
            'reported': 0,
            'ok': 0,
            'failed': 0,
            'skipped': 0,
            'in_stock': 0,
            'updated': 0,
            'reason_counts': Counter(),
            'host_counts': Counter(),
        }

    results = crawl_all(filtered_tasks, max_workers=config.max_workers)
    summary = _summarize_results(results)
    reason_counts, host_counts = _summarize_issues(results)
    reportable_results = [row for row in results if not _is_skip_result(row)]
    payload = {'updated': 0, 'push_enabled': False}
    if reportable_results:
        payload = report_results(run_id=run_id, results=reportable_results, result_mode=config.result_mode)
    info(
        f'页执行完成 run_id={run_id} tier={tier} source_mode={config.source_mode} '
        f'page={page_index} shard={config.shard_index + 1}/{config.shard_total} '
        f'total={summary["total"]} reported={summary["reported"]} '
        f'ok={summary["ok"]} failed={summary["failed"]} skipped={summary["skipped"]} '
        f'in_stock={summary["in_stock"]} updated={payload.get("updated", 0)} '
        f'push_enabled={payload.get("push_enabled", False)}'
    )
    _log_issue_summary('页', run_id, tier, config.source_mode, page_index, reason_counts, host_counts)
    return {
        'total': summary['total'],
        'reported': summary['reported'],
        'ok': summary['ok'],
        'failed': summary['failed'],
        'skipped': summary['skipped'],
        'in_stock': summary['in_stock'],
        'updated': int(payload.get('updated', 0)),
        'reason_counts': reason_counts,
        'host_counts': host_counts,
    }


def main() -> None:
    config = _load_run_config()
    run_id = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    start = time.time()
    STRATEGY_RUNTIME.load()
    info(
        f'运行配置 tiers={",".join(config.tiers)} source_mode={config.source_mode} '
        f'result_mode={config.result_mode} page_size={config.page_size} '
        f'max_workers={config.max_workers} shard={config.shard_index + 1}/{config.shard_total}'
    )
    totals = {
        'total': 0,
        'reported': 0,
        'ok': 0,
        'failed': 0,
        'skipped': 0,
        'in_stock': 0,
        'updated': 0,
    }
    run_reason_counts: Counter = Counter()
    run_host_counts: Counter = Counter()
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
            run_reason_counts.update(page_totals['reason_counts'])
            run_host_counts.update(page_totals['host_counts'])

            batch_size = len(tasks)
            offset += batch_size
            page_index += 1
            if batch_size < config.page_size:
                break

    if not fetched_any_task:
        return

    elapsed = time.time() - start
    info(
        f'运行结束 run_id={run_id} total={totals["total"]} reported={totals["reported"]} '
        f'ok={totals["ok"]} failed={totals["failed"]} skipped={totals["skipped"]} '
        f'in_stock={totals["in_stock"]} updated={totals["updated"]} '
        f'elapsed={elapsed:.1f}s'
    )
    _log_issue_summary('运行', run_id, 'all', config.source_mode, None, run_reason_counts, run_host_counts)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        error(f'执行失败: {type(exc).__name__}')
        raise
