"""Check the subscription API without fetching sources or reporting results."""

from runner.task_client import pull_task_page


def verify_subscription_feed() -> None:
    for tier in ('low', 'high'):
        after_url = ''
        received = 0
        for _ in range(2):
            page = pull_task_page(tier, 1, refresh=False, source_mode='subscription', after_url=after_url)
            if page.get('source_mode') != 'subscription' or page.get('refreshed') != 0:
                raise RuntimeError(f'Unexpected task source or refresh status for tier={tier}')
            if 'next_after_url' not in page:
                raise RuntimeError(f'Monitor API does not support cursor pagination for tier={tier}')
            received += len(page['tasks'])
            after_url = page['next_after_url']
            if after_url is None:
                break
        print(f'Authenticated subscription pages OK for tier={tier}; sampled_tasks={received}')


if __name__ == '__main__':
    verify_subscription_feed()
