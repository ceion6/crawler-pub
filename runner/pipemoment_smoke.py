import json

from runner.main import _build_host_gates, crawl_one


TASKS = [
    {
        'url': (
            'https://pipemoment.com/en/products/'
            'samuel-gawith-squadron-leader-50g'
        )
    },
    {
        'url': (
            'https://pipemoment.com/en/products/'
            'gawith-hoggarth-rodeo-50g'
        )
    },
    {
        'url': (
            'https://pipemoment.com/en/products/'
            'gawith-hoggarth-american-c-v-50g'
        )
    },
]

gates = _build_host_gates(TASKS)
results = [crawl_one(task, host_gates=gates) for task in TASKS]
print(json.dumps(results, ensure_ascii=False))
if not all(row.get('fetch_ok') and not row.get('reason') for row in results):
    raise SystemExit(1)
