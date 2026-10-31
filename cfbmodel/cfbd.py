"""Minimal CollegeFootballData.com API client (stdlib only).

The CFBD v2 API returns camelCase keys; v1 used snake_case. `g()` reads either
so the rest of the code doesn't care which one comes back.
"""
import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def g(obj, key, default=None):
    """Get `key` (camelCase) from a CFBD record, falling back to snake_case."""
    if obj is None:
        return default
    if key in obj and obj[key] is not None:
        return obj[key]
    alt = _snake(key)
    if alt in obj and obj[alt] is not None:
        return obj[alt]
    return default


class CFBDError(RuntimeError):
    pass


class QuotaExceeded(CFBDError):
    """The account's monthly call quota is used up (HTTP 429 'Monthly call quota exceeded')."""


# Endpoints that change at most daily are cached on disk (data/cache/cfbd,
# committed with the data) so a refresh spends ~3 calls instead of ~11: the
# free CFBD tier allows 1,000 calls a month. Seconds to keep each response.
CACHE_TTL = {
    "/calendar": 7 * 86400,
    "/teams/fbs": 7 * 86400,
    "/rankings": 86400,
    "/ratings/sp": 86400,
    "/ratings/fpi": 86400,
    "/ratings/elo": 86400,
}


class CFBDClient:
    def __init__(self, api_key=None, base_url=None, retries=4):
        self.api_key = api_key or os.environ.get("CFBD_API_KEY", "")
        if not self.api_key:
            raise CFBDError("CFBD_API_KEY is not set")
        self.base_url = (base_url or config.CFBD_BASE_URL).rstrip("/")
        self.retries = retries
        self.calls = 0

    def _cache_path(self, path, params):
        key = hashlib.sha1(json.dumps([path, sorted(params.items())], default=str).encode()).hexdigest()[:16]
        return config.DATA_DIR / "cache" / "cfbd" / f"{path.strip('/').replace('/', '_')}_{key}.json"

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        ttl = CACHE_TTL.get(path) if os.environ.get("CFBD_CACHE", "on") != "off" else None
        cache = self._cache_path(path, params) if ttl else None
        cached = None
        if cache and cache.exists():
            try:
                cached = json.loads(cache.read_text())
            except ValueError:
                cached = None
            if cached and time.time() - cached.get("fetched", 0) < ttl:
                return cached["data"]
        try:
            data = self._fetch(path, params)
        except QuotaExceeded:
            if cached:  # out of calls: an old copy beats nothing for slow-changing data
                print(f"CFBD quota used up: using cached {path} from "
                      f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(cached.get('fetched', 0)))} UTC")
                return cached["data"]
            raise
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"fetched": int(time.time()), "data": data}, separators=(",", ":")))
        return data

    def _fetch(self, path, params):
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "cfb-model-site/1.0 (+github actions)",
            },
        )
        delay = 2
        for attempt in range(self.retries + 1):
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code == 429 and "quota" in body.lower():
                    # retrying can't help (and each retry counts against the quota)
                    raise QuotaExceeded(f"GET {path} {params} -> HTTP 429: {body}") from e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise CFBDError(f"GET {path} {params} -> HTTP {e.code}: {body}") from e
            except (urllib.error.URLError, http.client.IncompleteRead, ConnectionError, TimeoutError) as e:
                # IncompleteRead: the connection dropped mid-body (seen on large /lines responses)
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise CFBDError(f"GET {path} failed: {e}") from e
        raise CFBDError(f"GET {path} exhausted retries")

    # Thin endpoint wrappers -------------------------------------------------
    def calendar(self, year):
        return self.get("/calendar", year=year)

    def games(self, year, season_type="both", week=None):
        return self.get("/games", year=year, seasonType=season_type, week=week)

    def lines(self, year, season_type="regular", week=None):
        return self.get("/lines", year=year, seasonType=season_type, week=week)

    def sp(self, year):
        return self.get("/ratings/sp", year=year)

    def fpi(self, year):
        return self.get("/ratings/fpi", year=year)

    def elo(self, year, week=None, season_type=None):
        return self.get("/ratings/elo", year=year, week=week, seasonType=season_type)

    def fbs_teams(self, year):
        return self.get("/teams/fbs", year=year)


    def rankings(self, year, week=None, season_type=None):
        return self.get("/rankings", year=year, week=week, seasonType=season_type)
