# Crawler health checks

Check workflow state as well as the latest run. Public repositories with no
activity for 60 days can have their scheduled workflows disabled by GitHub:
https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows

```sh
gh api repos/ceion6/crawler-pub/actions/workflows --jq '.workflows[] | {name,state}'
gh run list -R ceion6/crawler-pub --limit 10
```

To isolate workflow-to-server connectivity, run
`gh workflow run verify_monitor.yml -R ceion6/crawler-pub`. It reads at most
two signed subscription pages per tier, with refresh disabled. It does not
fetch any source or submit observations, and only logs sample counts.

When the state is `disabled_inactivity`, restore the existing schedules:

```sh
gh workflow enable crawl_runner.yml -R ceion6/crawler-pub
gh workflow enable daily_full_crawl.yml -R ceion6/crawler-pub
gh workflow run crawl_runner.yml -R ceion6/crawler-pub
```

Wait for the subscription run to finish before testing one daily shard:

```sh
gh workflow run daily_full_crawl.yml -R ceion6/crawler-pub -f shard=0
```

Review actual `ok`, `failed`, `skipped` and reported results, not just the green
run icon. The run summary lists failure reasons. A run fails after reporting its
results if every reported fetch failed, or at least 10 fetches failed and the
failure rate reached 20%. Explicit skips are excluded from the denominator.
The summary shows newly recorded server observations separately from fetch
results. A replay can record zero new observations. Empty task pools and
shards still get a summary. An empty catalog pool raises a warning. An
invalid task response or report acknowledgement fails the run.
Task pulls use URL cursors when the first response includes a cursor field.
The whole run uses offsets if the first response comes from an older server.
An established cursor must keep advancing until the final page.

4N and 70 use their public tobacco collection feeds. All pages are loaded once
and reused across threads and task pages. Requested variant IDs determine stock
and price. Missing catalog handles retain the existing UCP lookup. Incomplete
or blocked catalogs stay failed for the current run rather than causing one
request per product; the next scheduled process tries again. No catalog or
private task data is committed to this repository.

403/challenge responses require an available authorized source or normal
interactive access. Repeated requests do not make them valid product data.
Dreaming Pipes and its subdomains are skipped under section 12 of its
[Terms and Conditions](https://dreamingpipes.com/terms-and-conditions/).
The runner counts these skips as `source_permission_paused` and keeps the
previous observations on the server.
PipeUncle 403 or HTML API responses stop further probes for the current run;
every affected listing is still reported as a failed fetch, and a new process
probes again. Product-missing JSON responses do not stop other products.
404s require checking whether the listing was removed or renamed. Neither
condition should be converted into a successful stock result.

The generic parser requires explicit, consistent stock markers. Missing or
conflicting markers produce failed observations and preserve the last
confirmed inventory.

Local checks: `python -m pytest tests -q` and `git diff --check`.
