import copy
import unittest

from app import config, failover, i18n
from app.failover import Failover, FailoverPolicy, Limits, MetricSwitch, Observation, Path
from app.storage import Storage

T0 = 1_000_000.0
WIFI = Path(6, "Wi-Fi", "192.168.3.1", "192.168.3.4", 0, 30, True, "wifi")
LTE = Path(21, "USB 4G", "192.168.8.1", "192.168.8.100", 0, 35, True, "other")
LAN = Path(13, "Ethernet", "10.0.0.1", "10.0.0.5", 0, 25, True, "ethernet")


def run(policy, start, end, world, step=5):
    """world(t) -> Observation; returns [(t, decision)] with an action or events."""
    out = []
    t = start
    while t <= end:
        d = policy.decide(world(t))
        if d.action or d.events:
            out.append((t, d))
        t += step
    return out


def obs(t, healthy, primary=6, paths=(WIFI, LTE), preferred=None, home=None):
    return Observation(t, list(paths), healthy, primary, preferred, home)


class PathTests(unittest.TestCase):
    def test_only_physical_up_paths_with_an_address(self):
        rows = [{"interface_index": 6, "name": "Wi-Fi", "gateway": "g", "ipv4": "1.2.3.4", "route_metric": 0,
                 "interface_metric": 30, "automatic_metric": True, "virtual": False, "media": "Native 802.11", "status": "Up"},
                {"interface_index": 32, "name": "wt0", "gateway": "g", "ipv4": "100.1.1.1", "virtual": True, "status": "Up"},
                {"interface_index": 13, "name": "Ethernet", "gateway": "g", "ipv4": "", "virtual": False, "status": "Up"},
                {"interface_index": 14, "name": "BT", "gateway": "g", "ipv4": "1.1.1.1", "virtual": False, "status": "Disconnected"},
                {"interface_index": 21, "name": "USB", "gateway": "g", "ipv4": "192.168.8.100", "route_metric": 0,
                 "interface_metric": 25, "virtual": False, "media": "802.3", "status": "Up"}]
        paths = failover.usable_paths(rows)
        self.assertEqual([(p.index, p.kind) for p in paths], [(21, "ethernet"), (6, "wifi")])   # by metric

    def test_probe_binds_to_the_path_address(self):
        calls = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def connect(addr, timeout, source_address):
            calls.append((addr, source_address))
            if addr[0] == "1.1.1.1":
                raise OSError("blocked")
            return Conn()
        self.assertTrue(failover.probe_path("192.168.8.100", connect=connect))
        self.assertEqual(calls, [(("1.1.1.1", 443), ("192.168.8.100", 0)), (("8.8.8.8", 443), ("192.168.8.100", 0))])
        self.assertFalse(failover.probe_path("10.0.0.5", connect=lambda *a, **k: (_ for _ in ()).throw(OSError())))

    def test_an_address_that_is_gone_is_unknown_not_down(self):
        gone = OSError(10049, "address not available")
        gone.winerror = 10049
        self.assertIsNone(failover.probe_path("192.168.3.4", connect=lambda *a, **k: (_ for _ in ()).throw(gone)))


class PolicyTests(unittest.TestCase):
    def test_switches_after_the_threshold_to_a_healthy_backup(self):
        p = FailoverPolicy()
        log = run(p, T0, T0 + 60, lambda t: obs(t, {6: t < T0 + 10, 21: True}))
        actions = [(t, d.action, d.target, d.home) for t, d in log if d.action]
        self.assertEqual(actions[0], (T0 + 30, "prefer", 21, 6))   # down since T0+10, 20 s later

    def test_a_short_blip_does_nothing(self):
        p = FailoverPolicy()
        log = run(p, T0, T0 + 120, lambda t: obs(t, {6: not (T0 + 10 <= t < T0 + 25), 21: True}))
        self.assertEqual([d.action for _, d in log if d.action], [])

    def test_backup_must_have_been_working_for_a_while(self):
        p = FailoverPolicy()
        world = lambda t: obs(t, {6: False, 21: t >= T0 + 25})       # backup comes up late
        first = next(t for t, d in run(p, T0, T0 + 120, world) if d.action)
        self.assertEqual(first, T0 + 35)

    def test_no_backup_is_reported_once_per_incident(self):
        p = FailoverPolicy()
        log = run(p, T0, T0 + 300, lambda t: obs(t, {6: False}, paths=(WIFI,)))
        events = [e for _, d in log for e in d.events]
        self.assertEqual([k for k, _, _ in events], ["failover_no_backup"])
        self.assertEqual(i18n.render(events[0][1], "en"), "Wi-Fi lost the Internet and there is no working backup connection")

    def test_picks_the_backup_with_the_lowest_metric(self):
        p = FailoverPolicy()
        log = run(p, T0, T0 + 60, lambda t: obs(t, {6: False, 21: True, 13: True}, paths=(WIFI, LTE, LAN)))
        self.assertEqual(next(d.target for _, d in log if d.action), 13)

    def test_failback_after_the_main_path_is_stable(self):
        p = FailoverPolicy()
        world = lambda t: obs(t, {6: t >= T0 + 100, 21: True}, primary=21, preferred=21, home=6)
        restore = next(t for t, d in run(p, T0, T0 + 400, world) if d.action == "restore")
        self.assertEqual(restore, T0 + 220)                                 # 120 s of good main path

    def test_back_home_early_if_the_backup_dies(self):
        p = FailoverPolicy()
        world = lambda t: obs(t, {6: True, 21: t < T0 + 30}, primary=21, preferred=21, home=6)
        restore = next(t for t, d in run(p, T0, T0 + 400, world) if d.action == "restore")
        self.assertEqual(restore, T0 + 50)

    def test_backup_unplugged_while_preferred(self):
        p = FailoverPolicy()
        world = lambda t: obs(t, {6: True}, primary=6, paths=(WIFI,), preferred=21, home=6)
        d = next(d for t, d in run(p, T0, T0 + 60, world) if d.action)
        self.assertEqual((d.action, d.target), ("restore", 21))

    def test_unknown_health_is_no_news(self):
        p = FailoverPolicy()
        # Wi-Fi's address changed at T0+10: probes say "unknown" until the paths are re-read
        log = run(p, T0, T0 + 60, lambda t: obs(t, {6: True if t < T0 + 10 else None, 21: True}))
        self.assertEqual([d.action for _, d in log if d.action], [])

    def test_sleep_forgets_how_long_things_were_down(self):
        p = FailoverPolicy()
        p.decide(obs(T0, {6: False, 21: True}))
        p.forget_health()
        self.assertIsNone(p.decide(obs(T0 + 3600, {6: False, 21: True})).action)   # not "down for an hour"

    def test_a_manual_choice_is_not_switched_back(self):
        p = FailoverPolicy()
        world = lambda t: Observation(t, [WIFI, LTE], {6: True, 21: True}, 21, 21, 6, manual=True)
        self.assertEqual([d.action for _, d in run(p, T0, T0 + 600, world) if d.action], [])
        dying = lambda t: Observation(t, [WIFI, LTE], {6: True, 21: t < T0 + 30}, 21, 21, 6, manual=True)
        self.assertIn("restore", [d.action for _, d in run(FailoverPolicy(), T0, T0 + 100, dying)])

    def test_cooldown_cap_and_flap_trip(self):
        p = FailoverPolicy(Limits(cooldown_s=60, max_per_hour=6, flap_limit=3, flap_window_s=1800))
        for i in range(3):
            p.performed(T0 + i * 100)
        log = run(p, T0 + 400, T0 + 500, lambda t: obs(t, {6: False, 21: True}))
        tripped = [d for _, d in log if d.trip]
        self.assertTrue(tripped and tripped[0].events[0][0] == "failover_tripped")
        self.assertTrue(p.tripped)
        self.assertEqual([d.action for _, d in log if d.action], [])

    def test_cooldown_between_switches(self):
        p = FailoverPolicy(Limits(cooldown_s=60))
        p.performed(T0)
        log = run(p, T0, T0 + 100, lambda t: obs(t, {6: False, 21: True}))
        self.assertEqual(next(t for t, d in log if d.action), T0 + 60)

    def test_hourly_cap(self):
        p = FailoverPolicy(Limits(cooldown_s=0, max_per_hour=2, flap_limit=99))
        p.performed(T0 - 100)
        p.performed(T0 - 50)
        log = run(p, T0, T0 + 60, lambda t: obs(t, {6: False, 21: True}))
        self.assertEqual([d.action for _, d in log if d.action], [])
        self.assertIn("failover_skip", [k for _, d in log for k, _, _ in d.events])


class UserSwitchTests(unittest.TestCase):
    def test_the_path_in_use_is_refused(self):
        refusal = failover.in_use_refusal(6, {"interface_index": 6}, "Wi-Fi")
        self.assertEqual(i18n.render(refusal, "en"), "Wi-Fi is already the connection in use; nothing to switch")
        self.assertIsNone(failover.in_use_refusal(21, {"interface_index": 6}))
        self.assertIsNone(failover.in_use_refusal(21, None))      # no route at all: a switch is what is needed

    def test_user_switches_are_logged_but_not_no_ops(self):
        storage = Storage()
        self.addCleanup(storage.close)
        failover.record_user_switch(storage, "prefer", failover.SwitchResult(True, i18n.msg("failover.result.switched", path="USB 4G")))
        failover.record_user_switch(storage, "restore", failover.SwitchResult(True, i18n.msg("failover.result.nothing")))
        failover.record_user_switch(storage, "restore", failover.SwitchResult(False, i18n.msg("failover.result.restore_failed", error="x")))
        events = storage.query_events(limit=10)
        self.assertEqual(sorted((e["kind"], e["level"]) for e in events),
                         [("failover_error", "bad"), ("failover_switched", "warn")])


class FakeSystem:
    def __init__(self, metrics=None, fail_set=False, lie=False):
        self.metrics = metrics or {21: {"automatic": True, "metric": 35}}
        self.fail_set, self.lie, self.calls = fail_set, lie, []

    def interface_metric_get(self, index):
        return dict(self.metrics[index])

    def interface_metric_set(self, index, metric):
        self.calls.append((index, metric))
        if index not in self.metrics:
            raise OSError(f"no interface {index}")          # adapter unplugged
        if self.fail_set and metric is not None:
            raise OSError("access denied")
        if self.lie:
            return
        self.metrics[index] = {"automatic": True, "metric": 35} if metric is None else {"automatic": False, "metric": metric}


class SwitchTests(unittest.TestCase):
    def make(self, system):
        self.store = {"wifi_power_saving": {"original": {}, "source": "capture"}}
        saves = []

        def save(s):
            saves.append(copy.deepcopy(s))
            self.store = copy.deepcopy(s)
        self.saves = saves
        return MetricSwitch(system, load_backup=lambda: copy.deepcopy(self.store), save_backup=save, clock=lambda: T0)

    def test_prefer_backs_up_first_then_sets_a_beating_metric(self):
        sys_ = FakeSystem()
        sw = self.make(sys_)
        res = sw.prefer(LTE, WIFI)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(sys_.calls, [(21, 29)])                      # 0 + 29 < 0 + 30
        self.assertEqual(self.saves[0]["failover:21"]["original"], {"automatic": True, "metric": 35})
        self.assertEqual(self.saves[0]["failover:21"]["home"], 6)
        self.assertIn("wifi_power_saving", self.store)                 # tweak backups untouched
        self.assertEqual(failover.preferred_from_backup(self.store), (21, 6))
        self.assertEqual(i18n.render(res.message, "en"), "Now using USB 4G")

    def test_only_one_preferred_path_at_a_time(self):
        sw = self.make(FakeSystem({21: {"automatic": True, "metric": 35}, 13: {"automatic": True, "metric": 25}}))
        sw.prefer(LTE, WIFI)
        self.assertFalse(sw.prefer(LAN, WIFI).ok)

    def test_restore_puts_automatic_back_and_drops_the_backup(self):
        sys_ = FakeSystem()
        sw = self.make(sys_)
        sw.prefer(LTE, WIFI)
        res = sw.restore(21)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(sys_.calls[-1], (21, None))
        self.assertNotIn("failover:21", self.store)
        self.assertEqual(sw.restore(21).ok, True)                       # nothing left: fine

    def test_restore_a_manual_metric(self):
        sys_ = FakeSystem({21: {"automatic": False, "metric": 50}})
        sw = self.make(sys_)
        sw.prefer(LTE, WIFI)
        sw.restore(21)
        self.assertEqual(sys_.calls[-1], (21, 50))

    def test_failed_apply_rolls_back(self):
        sys_ = FakeSystem(fail_set=True)
        sw = self.make(sys_)
        res = sw.prefer(LTE, WIFI)
        self.assertFalse(res.ok)
        self.assertIn("rolled back", i18n.render(res.message, "en"))
        self.assertNotIn("failover:21", self.store)

    def test_read_back_mismatch_keeps_the_backup(self):
        sw = self.make(FakeSystem(lie=True))
        res = sw.prefer(LTE, WIFI)
        self.assertFalse(res.ok)                                         # set did nothing: not verified

    def test_target_metric_never_below_one(self):
        home = Path(6, "w", "g", "ip", 0, 1, True, "wifi")
        self.assertEqual(MetricSwitch.target_metric(LTE, home), 1)
        self.assertEqual(MetricSwitch.target_metric(LTE, None), 1)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.t = T0
        self.storage = Storage()
        self.addCleanup(self.storage.close)
        self.settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        self.settings["failover"]["enabled"] = True
        self.store = {}
        self.health = {"192.168.3.4": False, "192.168.8.100": True}
        self.toasts = []
        self.admin = True
        rows = [{"interface_index": p.index, "name": p.name, "gateway": p.gateway, "ipv4": p.ipv4, "route_metric": 0,
                 "interface_metric": p.interface_metric, "automatic_metric": True, "virtual": False,
                 "media": "Native 802.11" if p is WIFI else "", "status": "Up"} for p in (WIFI, LTE)]
        self.system = FakeSystem()

        def save_backup(s):
            self.store = copy.deepcopy(s)
        switch = MetricSwitch(self.system, load_backup=lambda: copy.deepcopy(self.store), save_backup=save_backup,
                              clock=lambda: self.t)
        self.fo = Failover(self.storage, clock=lambda: self.t, load_settings=lambda: copy.deepcopy(self.settings),
                           save_settings=lambda s: self.settings.update(copy.deepcopy(s)),
                           load_backup=lambda: copy.deepcopy(self.store), paths_fn=lambda: rows,
                           route_fn=lambda: {"interface_index": 21 if "failover:21" in self.store else 6},
                           probe_fn=lambda ip: self.health[ip], is_admin=lambda: self.admin, switch=switch,
                           notify=lambda title, body: self.toasts.append((title, body)))
        self.addCleanup(self.fo._pool.shutdown)

    def run_for(self, seconds, step=5):
        end = self.t + seconds
        while self.t <= end:
            self.fo.tick()
            self.t += step

    def kinds(self):
        return [e["kind"] for e in reversed(self.storage.query_events(limit=100))]

    def test_switch_and_switch_back_with_admin(self):
        self.run_for(40)
        self.assertIn("failover:21", self.store)
        self.assertEqual(self.kinds(), ["failover_switched"])
        self.assertEqual(self.toasts[0][0], i18n.t("notify.failover.switched.title"))
        self.health["192.168.3.4"] = True
        self.run_for(200)
        self.assertNotIn("failover:21", self.store)
        self.assertEqual(self.kinds(), ["failover_switched", "failover_restored"])
        self.assertEqual(self.system.metrics[21], {"automatic": True, "metric": 35})

    def test_without_admin_it_only_offers_once(self):
        self.admin = False
        self.run_for(300)
        self.assertEqual(self.kinds(), ["failover_available"])
        self.assertEqual(self.store, {})
        self.assertEqual(self.system.calls, [])

    def test_dry_run_writes_nothing(self):
        self.settings["failover"]["dry_run"] = True
        self.run_for(300)
        self.assertEqual(self.kinds(), ["failover_dry_run"])
        self.assertEqual(self.system.calls, [])

    def test_turning_it_off_restores(self):
        self.run_for(40)
        self.settings["failover"]["enabled"] = False
        self.fo._settings = None
        self.fo.tick()
        self.assertEqual(self.store, {})
        self.assertEqual(self.kinds()[-1], "failover_restored")

    def test_disabled_does_nothing(self):
        self.settings["failover"]["enabled"] = False
        self.run_for(300)
        self.assertEqual((self.kinds(), self.system.calls), ([], []))

    def test_a_failing_switch_is_reported_once_and_retried_slowly(self):
        self.system.fail_set = True
        self.run_for(1400)
        self.assertEqual(self.kinds(), ["failover_error"])
        attempts = [c for c in self.system.calls if c[1] is not None]
        self.assertEqual(len(attempts), 4)                  # at ~20 s, then 60 s, 300 s and 900 s later
        self.assertEqual(self.store, {})

    def test_a_failing_restore_does_not_spin(self):
        self.store = {"failover:99": {"original": {"automatic": True}, "home": 6, "source": "failover"}}
        self.settings["failover"]["enabled"] = False         # turned off: restore a path that is gone
        self.run_for(600)
        self.assertEqual(self.kinds(), ["failover_error"])
        self.assertLessEqual(len(self.system.calls), 3)
        self.assertIn("failover:99", self.store)             # the only copy of the original: kept

    def test_a_manual_switch_survives_while_failover_is_off(self):
        self.settings["failover"]["enabled"] = False
        self.assertEqual(failover.user_source(lambda: self.settings), "manual")
        self.assertTrue(self.fo.switch.prefer(LTE, WIFI, source="manual").ok)
        self.run_for(300)
        self.assertIn("failover:21", self.store)
        self.assertEqual(self.kinds(), [])

    def test_paths_are_known_even_while_off(self):
        self.settings["failover"]["enabled"] = False
        self.fo.tick()
        snap = self.fo.snapshot()
        self.assertEqual([p["index"] for p in snap["paths"]], [6, 21])
        self.assertIsNone(snap["paths"][0]["healthy"])                  # not probed while off
        self.assertEqual(snap["primary"], 6)

    def test_snapshot_for_the_ui(self):
        self.run_for(40)
        snap = self.fo.snapshot()
        self.assertEqual((snap["preferred"], snap["home"]), (21, 6))
        lte = next(p for p in snap["paths"] if p["index"] == 21)
        self.assertTrue(lte["preferred"] and lte["healthy"])


if __name__ == "__main__":
    unittest.main()
