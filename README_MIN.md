# Crawler Runner

Public execution runner for scheduled crawl jobs.

## Required Secrets

- `MONITOR_API_BASE_URL`
- `MONITOR_API_CLIENT_ID`
- `MONITOR_API_SECRET`

## Optional Runtime Controls

- `MAX_WORKERS`: 全局线程池大小，默认由调度环境传入
- `HOST_POLICY_JSON`: 可选的 host 级限流/重试配置，JSON 对象格式

示例：

```json
{
  "www.4noggins.com": {
    "max_parallel": 1,
    "min_interval_seconds": 2.0,
    "max_attempts": 4,
    "backoff_base_seconds": 2.0,
    "backoff_cap_seconds": 12.0
  }
}
```

## Notes

- This repository is execution-only.
- Runtime state and target catalogs are provided by the private service.
- 内置对 `4noggins.com` / `www.4noggins.com` 的保守策略，避免瞬时并发过高导致临时 `503`。
- 内部监控 API 签名采用请求级 `X-Nonce`，签名覆盖 `timestamp + method + path + query + nonce + body`。
