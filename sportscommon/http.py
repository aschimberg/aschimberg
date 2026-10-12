"""Small HTTP helpers with retries (stdlib only)."""
import json
import time
import urllib.error
import urllib.request

UA = "cfb-model-site/1.0 (+github actions)"


def fetch(url, retries=4, timeout=90, headers=None, want_headers=False):
    h = {"User-Agent": UA}
    h.update(headers or {})
    delay = 2
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return (r.read(), dict(r.headers)) if want_headers else r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8"))
