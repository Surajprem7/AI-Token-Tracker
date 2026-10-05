"""Widget window, tray-less closing rules, single instance, start with the computer, widget summary."""

import json
import sys
import tempfile
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_token_tracker import autostart, core, gui, server  # noqa: E402
from isolation import isolate  # noqa: E402


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        results = [h() for h in self.handlers]
        return all(r is not False for r in results)


class FakeWindow:
    def __init__(self, owner, title, url, **kw):
        self.owner, self.title, self.url, self.kw = owner, title, url, kw
        self.visible = not kw.get("hidden")
        self.events = type("E", (), {})()
        self.events.closing, self.events.closed = Event(), Event()

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def restore(self):
        pass

    def destroy(self):
        if self.events.closing.fire() or self.owner.force:
            self.owner.windows.remove(self)
            self.events.closed.fire()

    def user_close(self):  # the window's X button
        if self.events.closing.fire():
            self.owner.windows.remove(self)
            self.events.closed.fire()


class FakeWebview:
    def __init__(self):
        self.windows, self.force = [], False

    def create_window(self, title, url, **kw):
        w = FakeWindow(self, title, url, **kw)
        self.windows.append(w)
        return w


class DesktopTests(unittest.TestCase):
    def make(self, widget=False):
        self.wv = FakeWebview()
        self.app = server.Dashboard()
        return gui.DesktopApp(self.wv, self.app, "http://127.0.0.1:1/?t=abc", widget)

    def test_widget_toggle_is_on_top(self):
        d = self.make()
        self.app.on_widget()
        self.assertEqual(len(self.wv.windows), 2)
        widget = self.wv.windows[1]
        self.assertTrue(widget.kw["on_top"])
        self.assertEqual(widget.url, "http://127.0.0.1:1/widget.html?t=abc")
        self.app.on_widget()
        self.assertEqual(len(self.wv.windows), 1)
        self.assertIsNone(d.widget)

    def test_start_with_widget_hides_dashboard(self):
        d = self.make(widget=True)
        self.assertFalse(d.main.visible)
        self.assertIsNotNone(d.widget)
        self.app.on_show()
        self.assertTrue(d.main.visible)

    def test_closing_dashboard_keeps_widget_running(self):
        d = self.make()
        d.toggle_widget()
        d.main.user_close()
        self.assertIn(d.main, self.wv.windows)  # hidden, not closed
        self.assertFalse(d.main.visible)
        d.widget.user_close()  # nothing left on screen and no tray: quit
        self.assertTrue(d.quitting)
        self.assertEqual(self.wv.windows, [])

    def test_closing_dashboard_alone_quits(self):
        d = self.make()
        d.main.user_close()
        self.assertEqual(self.wv.windows, [])


class SingleInstanceTests(unittest.TestCase):
    def test_second_launch_brings_first_forward(self):
        httpd, app, url = server.start(port=0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        shown = []
        app.on_show = lambda: shown.append("main")
        app.on_show_widget = lambda: shown.append("widget")
        port = httpd.server_address[1]
        with mock.patch.object(server, "DEFAULT_PORT", port):
            self.assertTrue(gui._focus_running_copy(widget=True))
            self.assertTrue(gui._focus_running_copy(widget=False))
        import time
        time.sleep(0.2)
        self.assertEqual(sorted(shown), ["main", "widget"])
        # without the app's own header (as from a web page) it's refused
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/focus", method="POST", data=b"{}")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=2)
        self.assertEqual(err.exception.code, 403)

    def test_nothing_running(self):
        with mock.patch.object(server, "DEFAULT_PORT", 1):
            self.assertFalse(gui._focus_running_copy(widget=False))


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        isolate(self, self.tmp)

    @unittest.skipIf(sys.platform in ("win32", "darwin"), "checks the Linux autostart file")
    def test_linux_desktop_file(self):
        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
                mock.patch.object(autostart.sys, "executable", "/opt/My Apps/AITokenTracker"):
            self.assertEqual(autostart.status(), {"available": True, "enabled": False})
            self.assertTrue(autostart.set_enabled(True)["enabled"])
            text = (self.tmp / "home/.config/autostart/ai-token-tracker.desktop").read_text()
            self.assertIn('Exec="/opt/My Apps/AITokenTracker" --widget', text)
            self.assertFalse(autostart.set_enabled(False)["enabled"])

    def test_not_installed(self):
        with mock.patch.object(autostart.shutil, "which", return_value=None):
            self.assertFalse(autostart.status()["available"])
            with self.assertRaises(RuntimeError):
                autostart.set_enabled(True)


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        now = datetime.now(timezone.utc)
        s = core.Session(session_id="s", project="p", path=Path("x"), tool="Codex CLI", title="Fix bug")
        s.turns.append(core.Turn(prompt="Fix bug", timestamp=now, calls=[core.ApiCall(now, "gpt-5", core.Usage(input=100, output=20))]))
        app = server.Dashboard()
        with mock.patch.object(server, "load_sessions", return_value=[s]), \
                mock.patch.object(server, "data_signature", return_value=1), \
                mock.patch.object(server.cursor_usage, "refresh"):
            out = app.summary()
        self.assertEqual(out["today"]["tokens"], 120)
        self.assertEqual(out["today"]["tools"][0]["tool"], "Codex CLI")
        self.assertEqual(out["latest"]["title"], "Fix bug")
        json.dumps(out)


if __name__ == "__main__":
    unittest.main()
