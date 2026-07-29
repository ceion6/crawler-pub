import json
import time

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


URLS = {
    "robots": "https://www.smokingpipes.com/robots.txt",
    "catalog": "https://www.smokingpipes.com/tobacco/",
    "product": (
        "https://www.smokingpipes.com/pipe-tobacco/"
        "samuel-gawith/1792-flake-50g/product_id/1990"
    ),
    "search": "https://www.smokingpipes.com/search/main.cfm?string=1792+Flake",
    "asset": (
        "https://assets.smokingpipes.com/images/blog/posts/2020-July/"
        "bestSellers/Best%20Selling%20Tinned%20Pipe%20Tobaccos.pdf"
    ),
    "pipemoment": (
        "https://pipemoment.com/en/collections/all-pipetobacco/"
        "products.json?limit=250&page=1"
    ),
}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
}


def summarize(label, response):
    text = response.text
    print(
        json.dumps(
            {
                "method": label,
                "url": response.url,
                "status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "server": response.headers.get("server"),
                "cf_ray": response.headers.get("cf-ray"),
                "length": len(response.content),
                "blocked": "you have been blocked" in text.lower(),
                "challenge": "challenge-platform" in text.lower(),
                "title": (
                    BeautifulSoup(text, "html.parser").title.get_text(strip=True)
                    if "<html" in text.lower()
                    and BeautifulSoup(text, "html.parser").title
                    else ""
                ),
                "json_ld": 'application/ld+json' in text,
            },
            ensure_ascii=False,
        )
    )


for name, url in URLS.items():
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        summarize(f"requests:{name}", response)
    except Exception as exc:
        print(json.dumps({"method": f"requests:{name}", "error": repr(exc)}))

for name in ("catalog", "product", "search"):
    try:
        response = curl_requests.get(
            URLS[name],
            headers=HEADERS,
            impersonate="chrome136",
            timeout=30,
        )
        summarize(f"curl_cffi:{name}", response)
    except Exception as exc:
        print(json.dumps({"method": f"curl_cffi:{name}", "error": repr(exc)}))

options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("--window-size=1440,1000")
options.add_argument(f"--user-agent={HEADERS['User-Agent']}")

driver = webdriver.Chrome(options=options)
try:
    for name in ("catalog", "product"):
        driver.get(URLS[name])
        time.sleep(12)
        source = driver.page_source
        print(
            json.dumps(
                {
                    "method": f"selenium:{name}",
                    "url": driver.current_url,
                    "title": driver.title,
                    "length": len(source),
                    "blocked": "you have been blocked" in source.lower(),
                    "challenge": "challenge-platform" in source.lower(),
                    "json_ld": 'application/ld+json' in source,
                },
                ensure_ascii=False,
            )
        )
finally:
    driver.quit()
