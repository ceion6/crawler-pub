import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests


def _normalize_query_pairs(query_pairs: List[Tuple[str, str]]) -> str:
    if not query_pairs:
        return ''
    normalized_pairs = sorted(
        (str(key), '' if value is None else str(value))
        for key, value in query_pairs
    )
    return urlencode(normalized_pairs, doseq=True)


def _params_to_query_pairs(params: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    if not params:
        return []

    query_pairs: List[Tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for item in value:
                query_pairs.append((str(key), '' if item is None else str(item)))
            continue
        query_pairs.append((str(key), '' if value is None else str(value)))
    return query_pairs


def _canonical_request_target(url: str, params: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    parsed_url = urlsplit(url)
    path = parsed_url.path or '/'
    query_pairs = parse_qsl(parsed_url.query, keep_blank_values=True)
    query_pairs.extend(_params_to_query_pairs(params))
    return path, _normalize_query_pairs(query_pairs)


def _signature_payload(
    method: str,
    url: str,
    body: bytes,
    ts: str,
    nonce: str,
    params: Optional[Dict[str, Any]] = None,
) -> bytes:
    path, query = _canonical_request_target(url, params=params)
    prefix = '\n'.join([ts, method.upper(), path, query, nonce]).encode('utf-8')
    return prefix + b'\n' + body


def _signed_headers(method: str, url: str, body: bytes, params: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    client_id = os.environ['MONITOR_API_CLIENT_ID']
    secret = os.environ['MONITOR_API_SECRET'].encode('utf-8')
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    payload = _signature_payload(method=method, url=url, body=body, ts=ts, nonce=nonce, params=params)
    sign = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return {
        'Content-Type': 'application/json',
        'X-Client-Id': client_id,
        'X-Timestamp': ts,
        'X-Nonce': nonce,
        'X-Signature': sign,
    }


def signed_post(url: str, payload: Dict[str, Any], timeout: int = 20) -> requests.Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    headers = _signed_headers(method='POST', url=url, body=body)
    return requests.post(url, data=body, headers=headers, timeout=timeout)


def signed_get(url: str, timeout: int = 20, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    body = b''
    headers = _signed_headers(method='GET', url=url, body=body, params=params)
    return requests.get(url, headers=headers, timeout=timeout, params=params)
