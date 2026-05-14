import hashlib
import importlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Optional

from bs4 import BeautifulSoup

from runner.auth_client import signed_get
from runner.safe_logger import info, warn


class StrategyRuntime:
    def __init__(self):
        self._registry = None
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if os.getenv('MONITOR_STRATEGY_BUNDLE_ENABLED', 'true').lower() != 'true':
            warn('策略包加载已禁用，回退到通用解析')
            return

        base_url = os.environ['MONITOR_API_BASE_URL'].rstrip('/')
        manifest_url = f'{base_url}/internal/monitor/pull-strategy'
        resp = signed_get(manifest_url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        strategy_meta = payload.get('strategy', {})
        sha256_expect = strategy_meta.get('sha256')
        download_path = strategy_meta.get('download_path')
        if not sha256_expect or not download_path:
            warn('策略清单缺失字段，回退到通用解析')
            return

        download_url = f'{base_url}{download_path}'
        file_resp = signed_get(download_url, timeout=60)
        file_resp.raise_for_status()
        content = file_resp.content

        digest = hashlib.sha256(content).hexdigest()
        if digest != sha256_expect:
            raise RuntimeError('策略包校验失败，sha256 不匹配')

        temp_dir = Path(tempfile.mkdtemp(prefix='strategy-pack-'))
        zip_path = temp_dir / 'strategy_pack.zip'
        zip_path.write_bytes(content)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)

        sys.path.insert(0, str(temp_dir))
        strategies_mod = importlib.import_module('strategies')
        self._registry = getattr(strategies_mod, 'StrategyRegistry', None)
        if self._registry is None:
            warn('策略包缺少 StrategyRegistry，回退到通用解析')
            return
        info('策略包已加载成功')

    def _get_strategy(self, task: Dict):
        if self._registry is None:
            return None
        url = task.get('url', '')
        return self._registry.get_strategy(url)

    @staticmethod
    def _normalize_strategy_task(task: Dict) -> Dict:
        normalized = dict(task or {})
        normalized.setdefault('name', normalized.get('product_name', '') or '')
        normalized.setdefault('site', normalized.get('site_name', '') or '')
        return normalized

    def requires_selenium(self, task: Dict) -> bool:
        strategy = self._get_strategy(task)
        if strategy is None:
            return False
        try:
            return bool(strategy.requires_selenium())
        except Exception:
            return False

    def get_request_headers(self, task: Dict) -> Dict[str, str]:
        strategy = self._get_strategy(task)
        if strategy is None:
            return {}
        try:
            headers = strategy.get_request_headers() or {}
        except Exception:
            return {}
        return headers if isinstance(headers, dict) else {}

    def evaluate(self, task: Dict, html: str) -> Dict:
        strategy = self._get_strategy(task)
        if strategy is None:
            return {}

        normalized_task = self._normalize_strategy_task(task)
        url = normalized_task.get('url', '')

        if strategy.requires_selenium():
            return self._evaluate_selenium(strategy, url, normalized_task)

        soup = BeautifulSoup(html, 'lxml')
        text_content = soup.get_text(' ', strip=True).lower()
        page_complete = getattr(strategy, 'is_page_complete', None)
        if callable(page_complete):
            try:
                if not page_complete(soup, url, text_content):
                    return {
                        'fetch_ok': False,
                        'in_stock': False,
                        'price': '',
                        'reason': 'incomplete_product_page',
                    }
            except Exception as exc:
                return {
                    'fetch_ok': False,
                    'in_stock': False,
                    'price': '',
                    'reason': f'page_complete_error:{type(exc).__name__}',
                }
        in_stock = bool(strategy.check_stock(soup, url, text_content))
        try:
            price = strategy.extract_price(soup, url, normalized_task) or ''
        except TypeError:
            price = strategy.extract_price(soup, url) or ''
        return {
            'fetch_ok': True,
            'in_stock': in_stock,
            'price': price,
            'reason': '',
        }

    def _evaluate_selenium(self, strategy, url: str, task: Dict) -> Dict:
        """使用 webdriver-manager 自动获取 chromedriver，执行 Selenium 策略。"""
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service as ChromeService
            driver_path = ChromeDriverManager().install()
        except Exception as exc:
            warn(f'chromedriver 获取失败: {exc}')
            return {'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': f'chromedriver_install_failed:{type(exc).__name__}'}

        try:
            result = strategy.fetch_with_selenium(url, driver_path, task)
            return {
                'fetch_ok': bool(result.get('fetch_ok', False)),
                'in_stock': bool(result.get('in_stock', False)),
                'price': result.get('price', '') or '',
                'reason': result.get('reason', '') or '',
            }
        except Exception as exc:
            warn(f'Selenium 策略执行失败 url={url}: {exc}')
            return {'fetch_ok': False, 'in_stock': False, 'price': '', 'reason': f'selenium_error:{type(exc).__name__}'}
