"""Exchange rates for showing costs in other currencies (no network: replies are faked)."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import rates  # noqa: E402
from isolation import isolate  # noqa: E402


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


GOOD = {"result": "success", "time_last_update_unix": 1790000000, "rates": {"USD": 1, "INR": 88.2, "EUR": 0.86}}


class RatesTests(unittest.TestCase):
    def setUp(self):
        isolate(self, Path(tempfile.mkdtemp()))

    def test_download_then_cache(self):
        with mock.patch("urllib.request.urlopen", return_value=Reply(json.dumps(GOOD).encode())) as get:
            self.assertEqual(rates.get()["rates"]["INR"], 88.2)
            self.assertEqual(rates.get()["rates"]["EUR"], 0.86)  # from the cache
        get.assert_called_once()

    def test_offline_uses_old_rates_or_reports(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIn("error", rates.get())
        with mock.patch("urllib.request.urlopen", return_value=Reply(json.dumps(GOOD).encode())):
            rates.get()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            r = rates.get(force=True)
        self.assertTrue(r["stale"])
        self.assertEqual(r["rates"]["INR"], 88.2)

    def test_bad_reply_is_rejected(self):
        bad = dict(GOOD, rates={"USD": 1, "INR": -5})
        with mock.patch("urllib.request.urlopen", return_value=Reply(json.dumps(bad).encode())):
            self.assertIn("error", rates.get())


if __name__ == "__main__":
    unittest.main()
