import copy
import csv
import random
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app import config, i18n
from app.actions import ActionResult, Actions
from app.storage import Storage
from app.watchdog import (RECONNECT, RECONNECT_FORCE, RESTART, Limits, Observation, Watchdog, WatchdogPolicy,
                          uplink_kind)

T0 = 1_000_000.0
LIM = Limits()  # 15 s threshold, 120 s cooldown, 6/h, 60 s verify, trip after 3


def obs(t, wifi="connected", wifi_since=T0, router_down=None, **kw):
    return Observation(now=t, wifi_state=wifi, wifi_since=wifi_since, router_down_since=router_down, **kw)


def run(policy, start, end, make_obs):
    """Tick once per second; returns [(t, decision)] for ticks that did something."""
    out = []
    t = start
    while t <= end:
        d = policy.decide(make_obs(t))
        if d.action or d.events or d.trip:
            out.append((t, d))
        t += 1
    return out


def actions_of(log):
    return [(t, d.action) for t, d in log if d.action]


def kinds_of(log):
    return [k for _, d in log for k, _, _ in d.events]


class DetectionTests(unittest.TestCase):
    def test_healthy_does_nothing(self):
        self.assertEqual(run(WatchdogPolicy(), T0, T0 + 600, lambda t: obs(t)), [])

    def test_wifi_down_acts_only_after_threshold(self):
        log = run(WatchdogPolicy(), T0, T0 + 20, lambda t: obs(t, wifi="disconnected", wifi_since=T0))
        self.assertEqual(actions_of(log), [(T0 + 15, RECONNECT)])

    def test_short_blip_never_triggers(self):
        def world(t):
            return obs(t, wifi="disconnected", wifi_since=T0) if t < T0 + 14 else obs(t)
        self.assertEqual(actions_of(run(WatchdogPolicy(), T0, T0 + 300, world)), [])

    def test_silent_hang_uses_forced_reconnect(self):
        log = run(WatchdogPolicy(), T0, T0 + 20, lambda t: obs(t, router_down=T0))
        self.assertEqual(actions_of(log), [(T0 + 15, RECONNECT_FORCE)])

    def test_internet_only_outage_never_triggers(self):
        # Router answers (router_down None) - the ISP side is not ours to fix.
        self.assertEqual(run(WatchdogPolicy(), T0, T0 + 3600, lambda t: obs(t)), [])

    def test_no_wifi_adapter_never_triggers(self):
        self.assertEqual(run(WatchdogPolicy(), T0, T0 + 600, lambda t: obs(t, wifi=None, router_down=T0)), [])

    def test_wired_uplink_is_left_alone_and_logged_once(self):
        log = run(WatchdogPolicy(), T0, T0 + 300, lambda t: obs(t, router_down=T0, uplink="other"))
        self.assertEqual(actions_of(log), [])
        self.assertEqual(kinds_of(log), ["watchdog_skip"])

    def test_grace_period_delays_action(self):
        log = run(WatchdogPolicy(), T0, T0 + 120, lambda t: obs(t, router_down=T0, grace_until=T0 + 60))
        self.assertEqual(actions_of(log), [(T0 + 60, RECONNECT_FORCE)])

    def test_user_disconnect_is_respected(self):
        log = run(WatchdogPolicy(), T0, T0 + 600, lambda t: obs(t, wifi="disconnected", user_disconnected=True))
        self.assertEqual(actions_of(log), [])


class LadderTests(unittest.TestCase):
    def test_escalates_after_cooldown_and_never_repeats_within_an_incident(self):
        log = run(WatchdogPolicy(), T0, T0 + 8 * 3600, lambda t: obs(t, wifi="disconnected", is_admin=True))
        self.assertEqual(actions_of(log), [(T0 + 15, RECONNECT), (T0 + 135, RESTART)])  # 8 h dead: still only 2
        self.assertIn("watchdog_ineffective", kinds_of(log))

    def test_without_admin_restart_is_skipped_with_a_reason(self):
        log = run(WatchdogPolicy(), T0, T0 + 3600, lambda t: obs(t, wifi="disconnected", is_admin=False))
        self.assertEqual(actions_of(log), [(T0 + 15, RECONNECT)])
        msgs = [i18n.render(m, "vi") for _, d in log for k, m, _ in d.events if k == "watchdog_skip"]
        self.assertTrue(any("Administrator" in m for m in msgs))
        self.assertTrue(any("thử hết" in m for m in msgs))

    def test_recovery_after_action_is_logged_and_resets_the_counter(self):
        p = WatchdogPolicy()
        p.consecutive_ineffective = 2

        def world(t):
            return obs(t, router_down=T0) if t < T0 + 30 else obs(t)
        log = run(p, T0, T0 + 100, world)
        self.assertEqual(actions_of(log), [(T0 + 15, RECONNECT_FORCE)])
        self.assertIn("watchdog_recovered", kinds_of(log))
        self.assertEqual(p.consecutive_ineffective, 0)


class LimitTests(unittest.TestCase):
    def test_cooldown_between_separate_incidents(self):
        def world(t):  # two outages 40 s apart, each fixed 10 s after the action
            if T0 <= t < T0 + 25 or T0 + 65 <= t < T0 + 300:
                return obs(t, router_down=T0 if t < T0 + 25 else T0 + 65)
            return obs(t)
        acts = actions_of(run(WatchdogPolicy(), T0, T0 + 400, world))
        self.assertEqual(acts[0][0], T0 + 15)
        self.assertGreaterEqual(acts[1][0] - acts[0][0], LIM.cooldown_s)

    def test_hourly_cap_holds_under_24h_of_random_flapping(self):
        rnd = random.Random(7)
        outages, t = [], T0
        while t < T0 + 24 * 3600:   # random outages 0-10 min long, 0-5 min apart
            length, gap = rnd.randint(5, 600), rnd.randint(1, 300)
            outages.append((t, t + length, rnd.choice(["wifi", "router"])))
            t += length + gap

        def world(t):
            for a, b, kind in outages:
                if a <= t < b:
                    return obs(t, wifi="disconnected", wifi_since=a) if kind == "wifi" else obs(t, router_down=a)
            return obs(t)
        p = WatchdogPolicy(Limits(trip_after=10**9))  # keep acting all day to stress the cap
        times = [a for a, _ in actions_of(run(p, T0, T0 + 24 * 3600, world))]
        self.assertTrue(times)
        for i, a in enumerate(times):
            self.assertLessEqual(sum(1 for b in times if a <= b < a + 3600), LIM.max_per_hour)
            if i:
                self.assertGreaterEqual(a - times[i - 1], LIM.cooldown_s)

    def test_history_from_a_previous_process_counts_toward_the_cap(self):
        history = [T0 - 3000 + 130 * i for i in range(6)]   # 6 actions in the last hour, before a restart
        log = run(WatchdogPolicy(LIM, history), T0, T0 + 300, lambda t: obs(t, router_down=T0))
        self.assertEqual(actions_of(log), [])
        self.assertTrue(any("trần" in i18n.render(m, "vi") for _, d in log for _, m, _ in d.events))

    def test_trips_after_three_ineffective_actions_and_stays_off(self):
        def world(t):  # three separate outages, none ever helped by the action
            for a in (T0, T0 + 1000, T0 + 2000):
                if a <= t < a + 400:
                    return obs(t, router_down=a, is_admin=False)
            return obs(t)
        p = WatchdogPolicy()
        log = run(p, T0, T0 + 6000, world)
        self.assertEqual(len(actions_of(log)), 3)
        self.assertEqual(kinds_of(log).count("watchdog_ineffective"), 3)
        self.assertTrue(any(d.trip for _, d in log))
        self.assertTrue(p.tripped)
        self.assertEqual(actions_of(run(p, T0 + 6000, T0 + 9000, lambda t: obs(t, router_down=T0 + 6000))), [])

    def test_dry_run_decides_but_never_trips(self):
        p = WatchdogPolicy(dry_run=True)
        log = run(p, T0, T0 + 3 * 3600, lambda t: obs(t, router_down=T0, is_admin=True))
        self.assertTrue(actions_of(log))
        self.assertNotIn("watchdog_ineffective", kinds_of(log))
        self.assertFalse(p.tripped)


def _replay_events():
    """Outages and Wi-Fi state changes recorded by the PowerShell logger on 2026-10-03."""
    rows = []
    for path in sorted(Path(config.ROOT, "data", "monitor").glob("events-2026100*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows += list(csv.DictReader(fh))
    return rows


class ReplayTests(unittest.TestCase):
    """Feed the recorded router outages through the policy (no action ever fixes anything)."""

    def test_replay_2026_10_03(self):
        rows = _replay_events()
        if not rows:
            self.skipTest("no recorded logs")
        ts = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S").astimezone().timestamp()  # noqa: E731
        outages = sorted((ts(r["time"]) - float(r["duration_s"]), ts(r["time"]))
                         for r in rows if r["kind"] == "router_down" and r["duration_s"])
        start, end = outages[0][0] - 60, outages[-1][1] + 600

        def world(t):
            for a, b in outages:
                if a <= t < b:
                    return obs(t, router_down=a, is_admin=True)
            return obs(t)
        p = WatchdogPolicy(Limits(trip_after=10**9))
        log = run(p, start, end, world)
        acts = [a for a, _ in actions_of(log)]
        short = [o for o in outages if o[1] - o[0] < LIM.threshold_s]
        for a in acts:  # every action sits inside an outage that had lasted >= 15 s
            self.assertTrue(any(o[0] + LIM.threshold_s <= a < o[1] for o in outages), a)
        for i, a in enumerate(acts):
            self.assertLessEqual(sum(1 for b in acts if a <= b < a + 3600), LIM.max_per_hour)
        self.assertGreater(len(short), 0)
        print(f"\n  replay: {len(outages)} router outages ({len(short)} under 15 s), "
              f"{len(acts)} actions would have been taken")


class UplinkTests(unittest.TestCase):
    adapters = [{"InterfaceIndex": 6, "PhysicalMediaType": "Native 802.11", "Status": "Up", "Virtual": False},
                {"InterfaceIndex": 9, "PhysicalMediaType": "802.3", "Status": "Up", "Virtual": False},
                {"InterfaceIndex": 12, "PhysicalMediaType": "", "Status": "Up", "Virtual": True},
                {"InterfaceIndex": 44, "PhysicalMediaType": "Unspecified", "Status": "Up", "Virtual": False},
                {"InterfaceIndex": 13, "PhysicalMediaType": "802.3", "Status": "Disconnected", "Virtual": False}]

    def test_kinds(self):
        self.assertEqual(uplink_kind(6, self.adapters), "wifi")
        self.assertEqual(uplink_kind(9, self.adapters), "other")
        self.assertEqual(uplink_kind(12, self.adapters), "wifi")   # VPN rides on Wi-Fi
        self.assertEqual(uplink_kind(44, self.adapters), "other")  # iPhone over USB (seen in a real test)
        self.assertEqual(uplink_kind(13, self.adapters), "wifi")   # a cable that is not connected carries nothing
        self.assertEqual(uplink_kind(99, self.adapters), "wifi")
        self.assertEqual(uplink_kind(None, self.adapters), "none")


# --- runtime ------------------------------------------------------------------------------

class FakeMonitor:
    def __init__(self):
        self.snap = {"wifi": {"interface": "Wi-Fi", "state": "connected", "profile": "Home"},
                     "wifi_since": T0, "last_profile": "Home", "last_gap_end": None, "outages": {}}

    def snapshot(self, window_s=0):
        return copy.deepcopy(self.snap)


class FakeActions:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def reconnect(self, interface, profile, force=False):
        self.calls.append(("reconnect", interface, profile, force))
        return ActionResult(self.ok, "done" if self.ok else "netsh failed")

    def restart_adapter(self, interface):
        self.calls.append(("restart", interface))
        return ActionResult(self.ok, "done")


class Rig:
    def __init__(self, enabled=True, dry_run=False, storage=None):
        self.t = T0
        self.settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        self.settings["watchdog"].update(enabled=enabled, dry_run=dry_run)
        self.saved = []
        self.monitor, self.actions = FakeMonitor(), FakeActions()
        self.storage = storage or Storage()
        self.disconnect_calls = 0
        self.next_disconnect = None   # what the WLAN log says about the latest disconnect
        self.wd = Watchdog(self.monitor, self.storage, actions=self.actions, clock=lambda: self.t,
                           load_settings=lambda: copy.deepcopy(self.settings), save_settings=self.save,
                           is_admin=lambda: False, route_fn=lambda: {"interface_index": 6},
                           adapters_fn=lambda: UplinkTests.adapters, disconnect_fn=self.disconnect_fn,
                           run_async=False)

    def disconnect_fn(self):
        self.disconnect_calls += 1
        return self.next_disconnect

    def save(self, s):
        self.saved.append(copy.deepcopy(s))
        self.settings = copy.deepcopy(s)

    def run(self, seconds):
        for _ in range(seconds):
            self.wd.tick()
            self.t += 1

    def events(self, kind=None):
        ev = self.storage.query_events(limit=1000)
        return [e for e in ev if kind is None or e["kind"] == kind]


class RuntimeTests(unittest.TestCase):
    def test_disabled_by_default_does_nothing(self):
        rig = Rig(enabled=False)
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(600)
        self.assertEqual((rig.actions.calls, rig.events()), ([], []))
        self.assertFalse(config.DEFAULT_SETTINGS["watchdog"]["enabled"])

    def test_silent_hang_reconnects_with_force_and_logs(self):
        rig = Rig()
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(20)
        self.assertEqual(rig.actions.calls, [("reconnect", "Wi-Fi", "Home", True)])
        self.assertEqual(len(rig.events("watchdog_action")), 1)

    def test_no_action_while_a_tethered_phone_keeps_the_pc_online(self):
        # Seen 2026-10-04: the router rebooted, Wi-Fi dropped, the PC stayed online through an iPhone
        # plugged in after the adapter list was read - the dry run still "would reconnect Wi-Fi".
        rig = Rig()
        listed = [a for a in UplinkTests.adapters if a["InterfaceIndex"] != 44]
        reads = []
        rig.wd._adapters_fn = lambda: reads.append(1) or (listed if len(reads) == 1 else UplinkTests.adapters)
        rig.wd._route_fn = lambda: {"interface_index": 44}
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.run(300)
        self.assertEqual(rig.actions.calls, [])
        self.assertEqual(len(reads), 2)                                  # the list was re-read once, at once
        self.assertEqual(len(rig.events("watchdog_skip")), 1)

    def test_a_vpn_route_does_not_re_read_adapters_every_tick(self):
        rig = Rig()
        reads = []
        rig.wd._adapters_fn = lambda: reads.append(1) or UplinkTests.adapters
        rig.wd._route_fn = lambda: {"interface_index": 77}              # virtual: never in the physical list
        rig.run(120)
        self.assertEqual(len(reads), 2)                                  # first read + one re-check

    def test_wifi_down_uses_last_known_profile(self):
        rig = Rig()
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.run(20)
        self.assertEqual(rig.actions.calls, [("reconnect", "Wi-Fi", "Home", False)])

    def test_dry_run_records_but_does_not_act(self):
        rig = Rig(dry_run=True)
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(20)
        self.assertEqual(rig.actions.calls, [])
        self.assertEqual(len(rig.events("watchdog_dry_run")), 1)
        self.assertEqual(rig.events("watchdog_action"), [])

    def test_trip_disables_watchdog_in_settings_and_stays_off(self):
        rig = Rig()
        rig.settings["watchdog"].update(trip_after=1)
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(200)
        self.assertEqual(len(rig.actions.calls), 1)
        self.assertEqual(len(rig.events("watchdog_tripped")), 1)
        self.assertFalse(rig.settings["watchdog"]["enabled"])
        self.assertIsNotNone(rig.settings["watchdog"]["tripped_at"])
        rig.run(3600)
        self.assertEqual(len(rig.actions.calls), 1)

    def test_cap_survives_restart_via_the_event_log(self):
        storage = Storage()
        for i in range(6):
            storage.add_event(int(T0 - 3000 + 130 * i), "watchdog_action", "earlier", level="warn")
        rig = Rig(storage=storage)
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(600)
        self.assertEqual(rig.actions.calls, [])

    def test_recent_tweak_change_gives_grace(self):
        rig = Rig()
        rig.storage.add_event(int(T0), "tweak_enabled", "Bật tối ưu: x", level="info")
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(60)
        self.assertEqual(rig.actions.calls, [])
        rig.run(100)
        self.assertEqual(len(rig.actions.calls), 1)   # acts once the 120 s grace is over

    def test_wake_from_sleep_gives_grace(self):
        rig = Rig()
        rig.monitor.snap["last_gap_end"] = T0
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(30)
        self.assertEqual(rig.actions.calls, [])

    def test_user_disconnect_is_checked_once_and_respected(self):
        rig = Rig()
        rig.next_disconnect = {"ts": int(T0), "reason": "The network is disconnected by the user."}
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.run(300)
        self.assertEqual(rig.actions.calls, [])
        self.assertEqual(rig.disconnect_calls, 1)

    def test_driver_disconnect_is_acted_on(self):
        rig = Rig()
        rig.next_disconnect = {"ts": int(T0), "reason": "The network is disconnected by the driver."}
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.run(20)
        self.assertEqual(len(rig.actions.calls), 1)

    def test_old_user_disconnect_does_not_block_a_new_outage(self):
        rig = Rig()
        rig.next_disconnect = {"ts": int(T0 - 3600), "reason": "The network is disconnected by the user."}
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.run(20)
        self.assertEqual(len(rig.actions.calls), 1)

    def test_garbage_from_the_event_log_does_not_wedge_the_watchdog(self):
        rig = Rig()
        rig.next_disconnect = "not a dict"
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.run(20)
        self.assertEqual(len(rig.actions.calls), 1)

    def test_failed_action_is_reported(self):
        rig = Rig()
        rig.actions.ok = False
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(20)
        self.assertEqual(len(rig.events("watchdog_error")), 1)

    def test_unknown_profile_is_reported_not_crashing(self):
        rig = Rig()
        rig.monitor.snap["wifi"] = {"interface": "Wi-Fi", "state": "disconnected", "profile": ""}
        rig.monitor.snap["last_profile"] = ""
        rig.run(20)
        self.assertIn("profile", i18n.render(rig.events("watchdog_error")[0]["message"], "en"))

    def test_enabling_at_runtime_takes_effect_without_restart(self):
        rig = Rig(enabled=False)
        rig.monitor.snap["outages"] = {"router": T0}
        rig.run(30)
        rig.settings["watchdog"]["enabled"] = True
        rig.run(30)
        self.assertEqual(len(rig.actions.calls), 1)


class ActionsTests(unittest.TestCase):
    def rig(self, codes=None):
        calls = []
        codes = codes or {}

        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=codes.get(args[2], 0), stdout=b"ok")
        return Actions(run=run, ps=lambda s, **k: calls.append(s) or ""), calls

    def test_reconnect_plain_and_forced(self):
        a, calls = self.rig()
        self.assertTrue(a.reconnect("Wi-Fi", "HomeNet_5G").ok)
        self.assertEqual(calls, [["netsh", "wlan", "connect", "name=HomeNet_5G", "interface=Wi-Fi"]])
        calls.clear()
        self.assertTrue(a.reconnect("Wi-Fi", "HomeNet_5G", force=True).ok)
        self.assertEqual([c[2] for c in calls], ["disconnect", "connect"])

    def test_failed_disconnect_stops_before_connect(self):
        a, calls = self.rig({"disconnect": 1})
        self.assertFalse(a.reconnect("Wi-Fi", "X", force=True).ok)
        self.assertEqual(len(calls), 1)

    def test_unsafe_names_are_rejected(self):
        a, _ = self.rig()
        for bad in ('a"b', "a\nb", ""):
            with self.assertRaises(ValueError):
                a.reconnect("Wi-Fi", bad)

    def test_restart_passes_the_name_only_as_base64(self):
        a, calls = self.rig()
        a.restart_adapter("Wi-Fi'; Remove-Item x")
        self.assertNotIn("Remove-Item x", calls[0])


class MonitorSnapshotTests(unittest.TestCase):
    def test_snapshot_exposes_what_the_watchdog_needs(self):
        from tests.test_monitor import Env
        env = Env()
        mon = env.make()
        self.addCleanup(mon._pool.shutdown)
        snap = mon.snapshot()
        self.assertEqual(snap["wifi_since"], env.t)
        self.assertEqual(snap["last_profile"], "")  # the fake WifiState has no profile
        self.assertIsNone(snap["last_gap_end"])
        env.run(mon, 1)
        env.t += 3600          # sleep
        env.run(mon, 1)
        self.assertEqual(mon.snapshot()["last_gap_end"], env.t - 1)

    def test_last_profile_is_kept_while_disconnected(self):
        from tests.test_monitor import Env, wifi
        import dataclasses
        env = Env()
        env.wifi = dataclasses.replace(wifi(), profile="Home")
        mon = env.make()
        self.addCleanup(mon._pool.shutdown)
        env.wifi = dataclasses.replace(wifi(state="disconnected"), profile="")
        env.t += 7
        mon.poll_wifi()
        snap = mon.snapshot()
        self.assertEqual(snap["last_profile"], "Home")
        self.assertEqual(snap["wifi_since"], env.t)


if __name__ == "__main__":
    unittest.main()
