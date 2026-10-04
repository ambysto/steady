import copy
import os
import sys
import unittest

from app import config, i18n, notify
from app.notify import Notifier, OutageNotifier, send_toast
from app.winutil import PowerShellError

T0 = 1_000_000.0


class Collector:
    def __init__(self):
        self.toasts = []

    def __call__(self, title, message):
        self.toasts.append((title, message))


class OutageNotifierTests(unittest.TestCase):
    def setUp(self):
        self.out = Collector()
        self.n = OutageNotifier(self.out, after_s=30, lang="en")

    def test_toast_only_after_the_threshold_and_only_once(self):
        for sec in range(0, 100):
            self.n.update(T0 + sec, {"router": T0})
        self.assertEqual(len(self.out.toasts), 1)
        title, text = self.out.toasts[0]
        self.assertIn("router", title)
        self.assertIn("30 seconds", text)   # first toast at exactly the threshold

    def test_blip_shorter_than_threshold_is_silent_including_recovery(self):
        for sec in range(0, 20):
            self.n.update(T0 + sec, {"router": T0})
        self.n.update(T0 + 20, {})
        self.assertEqual(self.out.toasts, [])

    def test_recovery_toast_says_how_long(self):
        self.n.update(T0 + 40, {"internet": T0})
        self.n.update(T0 + 125, {})
        self.assertEqual(len(self.out.toasts), 2)
        self.assertEqual(self.out.toasts[1][0], "Connection restored")
        self.assertIn("2 minutes 5 seconds", self.out.toasts[1][1])
        self.assertIn("Internet is down", self.out.toasts[1][1])   # names what was down

    def test_two_kinds_are_tracked_independently(self):
        self.n.update(T0 + 40, {"router": T0, "internet": T0})
        self.assertEqual(len(self.out.toasts), 2)
        self.n.update(T0 + 50, {"internet": T0})
        self.assertEqual(self.out.toasts[-1][0], "Connection restored")
        self.assertEqual(len(self.out.toasts), 3)

    def test_a_new_outage_of_the_same_kind_announces_again(self):
        self.n.update(T0 + 40, {"router": T0})
        self.n.update(T0 + 50, {})
        self.n.update(T0 + 200, {"router": T0 + 100})
        self.assertEqual(len(self.out.toasts), 3)

    def test_gap_reset_is_silent(self):
        self.n.update(T0 + 40, {"router": T0})
        self.n.reset()
        self.n.update(T0 + 5000, {})
        self.assertEqual(len(self.out.toasts), 1)   # only the first one; no "recovered" guess after a gap

    def test_unknown_kind_still_gets_a_readable_title(self):
        self.n.update(T0 + 40, {"dns": T0})
        self.assertEqual(self.out.toasts[0][0], "Network problem: dns")

    def test_language_is_pinned_per_notifier(self):
        out = Collector()
        OutageNotifier(out, after_s=30, lang="vi").update(T0 + 40, {"router": T0})
        self.assertEqual(out.toasts[0][0], "Mất kết nối tới router")
        self.assertIn("40 giây", out.toasts[0][1])


class NotifierTests(unittest.TestCase):
    def make(self, **kw):
        self.t = T0
        self.sent = []
        kw.setdefault("send", lambda title, message: self.sent.append((title, message)))
        return Notifier(clock=lambda: self.t, run_async=False, **kw)

    def test_delivers(self):
        n = self.make()
        self.assertTrue(n.notify("A", "x"))
        self.assertEqual(self.sent, [("A", "x")])

    def test_disabled_sends_nothing(self):
        n = self.make(enabled=lambda: False)
        self.assertFalse(n.notify("A", "x"))
        self.assertEqual(self.sent, [])

    def test_enabled_callback_error_means_off(self):
        def boom():
            raise RuntimeError("settings unreadable")
        self.assertFalse(self.make(enabled=boom).notify("A", "x"))

    def test_same_title_cooldown(self):
        n = self.make(cooldown_s=30)
        n.notify("A", "1")
        self.t += 10
        self.assertFalse(n.notify("A", "2"))
        self.assertTrue(n.notify("B", "3"))
        self.t += 25
        self.assertTrue(n.notify("A", "4"))
        self.assertEqual([m for _, m in self.sent], ["1", "3", "4"])

    def test_global_cap_in_a_window(self):
        n = self.make(cooldown_s=0, max_per_window=3, window_s=600)
        results = [n.notify(f"T{i}", "x") for i in range(6)]
        self.assertEqual(results, [True, True, True, False, False, False])
        self.assertEqual(n.dropped, 3)
        self.t += 601
        self.assertTrue(n.notify("again", "x"))

    def test_failure_never_raises_and_logs_once(self):
        def boom(title, message):
            raise PowerShellError("no toast host")
        n = self.make(send=boom, cooldown_s=0)
        with self.assertLogs("stableinternet.notify", "WARNING") as cm:
            n.notify("A", "x")
            n.notify("B", "x")
        self.assertEqual(len(cm.output), 1)

    def test_unexpected_error_in_send_is_contained(self):
        def boom(title, message):
            raise ZeroDivisionError
        with self.assertLogs("stableinternet.notify", "ERROR"):
            self.make(send=boom).notify("A", "x")

    def test_async_delivery_does_not_block_the_caller(self):
        import threading
        gate, done = threading.Event(), threading.Event()

        def slow(title, message):
            gate.wait(5)
            done.set()
        n = Notifier(send=slow, run_async=True)
        self.assertTrue(n.notify("A", "x"))     # returns while the send is still blocked
        self.assertFalse(done.is_set())
        gate.set()
        self.assertTrue(done.wait(5))


class ToastScriptTests(unittest.TestCase):
    NASTY = ["Tiêu đề", 'q"q', "a'b", "’‘", "$(Remove-Item x)", "<b>&amp;</b>", "`n; exit 1"]

    def test_strings_reach_powershell_only_as_base64(self):
        scripts = []
        for s in self.NASTY:
            send_toast(s, s, ps=lambda script, **k: scripts.append(script) or "Enabled")
        for script in scripts:
            for s in self.NASTY[1:]:
                self.assertNotIn(s, script)
            self.assertIn(notify.AUMID, script)

    def test_returns_notifier_setting(self):
        self.assertEqual(send_toast("a", "b", ps=lambda s, **k: "\r\nEnabled\r\n"), "Enabled")

    @unittest.skipUnless(sys.platform == "win32", "WinRT toast API")
    @unittest.skipIf(os.environ.get("CI"), "hosted CI runners have no interactive session for notifications")
    def test_real_powershell_builds_the_toast_without_showing_it(self):
        # show=False: loads the WinRT types, builds the XML from base64, creates the notifier. No pop-up.
        for s in self.NASTY:
            self.assertEqual(send_toast(s, s, show=False), "Enabled")


class IntegrationTests(unittest.TestCase):
    def test_monitor_toasts_a_long_outage_and_not_a_gap(self):
        from tests.test_monitor import Env
        env, out = Env(), Collector()
        env.make()
        settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        settings["probes"]["enabled"] = False
        from app.monitor import Monitor
        mon = Monitor(env.storage, settings, clock=lambda: env.t, ping_fn=env.ping, wifi_fn=lambda: env.wifi,
                      gateway_fn=lambda: env.gateway, notify=out)
        self.addCleanup(mon._pool.shutdown)
        env.run(mon, 5)
        env.run(mon, 40, **{"192.168.3.1": None, "1.1.1.1": None, "8.8.8.8": None})
        titles = [title for title, _ in out.toasts]
        self.assertIn(i18n.t("notify.outage.router.title"), titles)   # whatever language is configured
        n_before = len(out.toasts)
        env.t += 8 * 3600                       # sleep
        env.run(mon, 3, **{"192.168.3.1": 2.0, "1.1.1.1": 40.0, "8.8.8.8": 50.0})
        self.assertEqual(len(out.toasts), n_before)   # no "recovered" toast for a gap

    def test_watchdog_events_toast_except_dry_run(self):
        from tests.test_watchdog import Rig
        out = Collector()
        for dry, expected in ((False, 1), (True, 0)):
            rig = Rig(dry_run=dry)
            rig.wd._notify = out
            rig.monitor.snap["outages"] = {"router": 1_000_000.0}
            out.toasts.clear()
            rig.run(20)
            self.assertEqual(len(out.toasts), expected, dry)
            for title, body in out.toasts:   # stored as messages, shown as text
                self.assertIsInstance(title, str)
                self.assertIsInstance(body, str)
                self.assertNotIn("watchdog.", body)
        self.assertEqual(out.toasts, [])


if __name__ == "__main__":
    unittest.main()
