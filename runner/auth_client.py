import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

import requests


def _signed_headers(body: bytes) -> Dict[str, str]:
    client_id = os.environ['MONITOR_API_CLIENT_ID']
    secret = os.environ['MONITOR_API_SECRET'].encode('utf-8')
    ts = str(int(time.time()))
    payload = f'{ts}.'.encode('utf-8') + body
    sign = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return {
        'Content-Type': 'application/json',
        'X-Client-Id': client_id,
        'X-Timestamp': ts,
        'X-Signature': sign,
    }


def signed_post(url: str, payload: Dict[str, Any], timeout: int = 20) -> requests.Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    headers = _signed_headers(body)
    return requests.post(url, data=body, headers=headers, timeout=timeout)


def signed_get(url: str, timeout: int = 20, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    body = b''
    headers = _signed_headers(body)
    return requests.get(url, headers=headers, timeout=timeout, params=params)
