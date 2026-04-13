# Crawler Runner

Public execution runner for scheduled crawl jobs.

## Workflows

- `crawl_runner.yml`: 每小时执行一次 VIP 订阅相关链接抓取，使用 `source_mode=subscription`
- `daily_full_crawl.yml`: 每天执行一次 listings 目录轮询，使用 `source_mode=catalog`

## Required Secrets

- `MONITOR_API_BASE_URL`
- `MONITOR_API_CLIENT_ID`
- `MONITOR_API_SECRET`

## Optional Runtime Controls

- `MAX_WORKERS`: 全局线程池大小，默认由调度环境传入
- `MONITOR_TIERS`: 风险层级列表，逗号分隔，支持 `low,high`
- `MONITOR_SOURCE_MODE`: 任务来源范围，支持 `subscription` / `baseline` / `all` / `catalog`
- `MONITOR_RESULT_MODE`: 结果上报模式，支持 `subscription` / `catalog`
- `MONITOR_PAGE_SIZE`: 每次向私有服务拉取的分页大小
- `MONITOR_REFRESH_ON_FIRST_PULL`: 首次拉任务时是否要求私有服务刷新目标集，默认 `true`
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
- 公开仓库不保存任何目标目录、业务数据或私有 URL 清单；所有任务集合都由私有服务下发。
- 内置对 `4noggins.com` / `www.4noggins.com` 的保守策略，避免瞬时并发过高导致临时 `503`。
- 内部监控 API 签名采用请求级 `X-Nonce`，签名覆盖 `timestamp + method + path + query + nonce + body`。
