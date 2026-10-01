"""Tests for the update check and installer helpers (no network: GitHub replies are faked)."""

import hashlib
import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import __version__, server, updater  # noqa: E402
from isolation import isolate  # noqa: E402


def fake_release(version="99.0.0", assets=()):
    return {"tag_name": f"v{version}", "html_url": f"https://github.com/{updater.REPO}/releases/tag/v{version}",
            "body": "What changed", "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}",
                                                "size": 10, "digest": "sha256:" + "0" * 64} for n in assets]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp, AI_TOKEN_TRACKER_DIR=str(self.tmp / "att"))


class VersionTests(Base):
    def test_compare(self):
        self.assertTrue(updater.is_newer("2.10.0", "2.9.9"))
        self.assertTrue(updater.is_newer("v3", "2.99"))
        self.assertFalse(updater.is_newer("2.2", "2.2.0"))
        self.assertFalse(updater.is_newer("garbage", "0.1"))
        self.assertFalse(updater.is_newer(__version__))


class CheckTests(Base):
    def test_check_uses_cache_and_reports_update(self):
        with mock.patch.object(updater, "_fetch_json", return_value=fake_release(assets=[updater.WINDOWS_ASSET])) as fetch:
            first = updater.check()
            second = updater.check()  # within 6 hours: no second request
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(first["available"])
        self.assertEqual(second["latest"]["version"], "99.0.0")
        # Running from a source checkout, so this copy can't install itself.
        self.assertEqual(first["method"], "manual")
        self.assertFalse(first["can_install"])

    def test_switching_checks_off_means_no_request(self):
        updater.update_settings(check_updates=False)
        with mock.patch.object(updater, "_fetch_json") as fetch:
            status = updater.check()
        fetch.assert_not_called()
        self.assertFalse(status["available"])

    def test_offline_is_reported_not_raised(self):
        with mock.patch.object(updater, "_fetch_json", side_effect=OSError("no network")):
            status = updater.check(force=True)
        self.assertIn("no network", status["error"])

    def test_settings_default_to_automatic_updates_and_ignore_junk(self):
        self.assertEqual(updater.public_settings(updater.load_settings()), {"check_updates": True, "auto_install": True})
        updater.settings_file().parent.mkdir(parents=True, exist_ok=True)
        updater.settings_file().write_text(json.dumps({"auto_install": "yes", "check_updates": False}))
        self.assertEqual(updater.public_settings(updater.load_settings()), {"check_updates": False, "auto_install": True})

    def test_pip_copies_update_from_the_release_wheel(self):
        release = updater._release_info(fake_release(assets=["ai_token_tracker-99.0.0-py3-none-any.whl", "x.zip"]))
        self.assertTrue(updater._asset_for("pip", release)["url"].endswith(".whl"))
        self.assertIsNone(updater._asset_for("manual", release))


class DownloadTests(Base):
    def test_checksum_mismatch_is_refused(self):
        src = self.tmp / "file.bin"
        src.write_bytes(b"hello")
        good = {"url": src.as_uri(), "size": 5, "digest": "sha256:" + hashlib.sha256(b"hello").hexdigest()}
        with mock.patch.object(updater, "_ssl_context", return_value=None), \
                mock.patch.object(urllib.request, "urlopen", lambda req, **kw: open(src, "rb")):
            updater._download(good, self.tmp / "ok.bin")
            self.assertEqual((self.tmp / "ok.bin").read_bytes(), b"hello")
            with self.assertRaises(ValueError):
                updater._download(dict(good, digest="sha256:" + "0" * 64), self.tmp / "bad.bin")
        self.assertFalse((self.tmp / "bad.bin").exists())


class ServerTests(Base):
    def test_update_and_settings_endpoints(self):
        httpd, app, url = server.start(0, roots=[self.tmp / "none"])
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base, headers = url.split("/?")[0], {"X-Token": app.token}
        with mock.patch.object(updater, "_fetch_json", return_value=fake_release()):
            with urllib.request.urlopen(urllib.request.Request(base + "/api/update", headers=headers)) as r:
                self.assertTrue(json.load(r)["available"])
        req = urllib.request.Request(base + "/api/settings", data=b'{"auto_install": false}', method="POST",
                                     headers=dict(headers, **{"Content-Type": "application/json"}))
        with urllib.request.urlopen(req) as r:
            self.assertEqual(json.load(r), {"check_updates": True, "auto_install": False})
        # Installing isn't possible from a source checkout: a clear error, and the app keeps running.
        req = urllib.request.Request(base + "/api/update/install", data=b"", method="POST", headers=headers)
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        self.assertEqual(err.exception.code, 500)
        self.assertFalse(app.exit_event.is_set())


if __name__ == "__main__":
    unittest.main()
