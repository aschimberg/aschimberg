import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from cfbmodel import cfbd, config


def _resp(data):
    m = mock.MagicMock()
    m.__enter__.return_value.read.return_value = json.dumps(data).encode()
    return m


class CfbdCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(config, "DATA_DIR", Path(self.tmp.name))
        self.p.start()
        self.env = mock.patch.dict(os.environ, {"CFBD_API_KEY": "k", "CFBD_CACHE": "on"})
        self.env.start()

    def tearDown(self):
        self.p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_slow_endpoints_are_cached_fast_ones_are_not(self):
        c = cfbd.CFBDClient(retries=0)
        with mock.patch("urllib.request.urlopen", return_value=_resp([{"team": "A"}])) as u:
            c.sp(2026)
            c.sp(2026)          # cached: no second call
            c.lines(2026)
            c.lines(2026)       # lines always fresh
        self.assertEqual(u.call_count, 3)

    def test_quota_exceeded_uses_stale_cache_and_does_not_retry(self):
        c = cfbd.CFBDClient(retries=4)
        with mock.patch("urllib.request.urlopen", return_value=_resp([{"team": "A"}])):
            c.elo(2026)
        path = next((Path(self.tmp.name) / "cache" / "cfbd").glob("ratings_elo_*.json"))
        doc = json.loads(path.read_text())
        doc["fetched"] = time.time() - 3 * 86400  # expired
        path.write_text(json.dumps(doc))
        def quota(*_a, **_k):
            raise urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b'{"message":"Monthly call quota exceeded."}'))
        with mock.patch("urllib.request.urlopen", side_effect=quota) as u:
            self.assertEqual(c.elo(2026), [{"team": "A"}])   # stale copy
            self.assertEqual(u.call_count, 1)                 # no retries
            with self.assertRaises(cfbd.QuotaExceeded):
                c.lines(2026)                                 # nothing cached to fall back on


if __name__ == "__main__":
    unittest.main()
