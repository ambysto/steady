import copy
import sqlite3
import time
import unittest
from types import SimpleNamespace

from app import config, i18n, winutil
from app.monitor import MERGE_WINDOW_S, MinuteAggregator, Monitor, OutageTracker
from app.storage import Storage


def en(message):
    """Outage events are stored as messages (ADR-0006); these tests read the English text."""
    return i18n.render(message, "en")

BASE = 600_000  # divisible by 60: start of a minute
QUIET = [(True, True)] * (MERGE_WINDOW_S + 1)   # long enough for a held Internet outage to be reported


class OutageTrackerTests(unittest.TestCase):
    def feed(self, tracker, start, pattern, router_ip="192.168.3.1"):
        """pattern: list of (router_ok, internet_ok), one per second."""
        events = []
        for i, (r, n) in enumerate(pattern):
            events += tracker.update(start + i, r, n, router_ip)
        return events

    def test_healthy_emits_nothing(self):
        t = OutageTracker()
        self.assertEqual(self.feed(t, BASE, [(True, True)] * 30), [])
        self.assertEqual(t.active(), {})

    def test_below_threshold_is_not_an_outage(self):
        t = OutageTracker()
        events = self.feed(t, BASE, [(True, True), (False, True), (False, True), (True, True)])
        self.assertEqual(events, [])

    def test_router_outage_event_on_recovery(self):
        t = OutageTracker()
        events = self.feed(t, BASE, [(True, True)] + [(False, True)] * 5 + [(True, True)])
        (ev,) = events
        self.assertEqual(ev.kind, "router_down")
        self.assertEqual(ev.ts, BASE + 6)
        self.assertEqual(ev.duration, 5)  # first failed tick (BASE+1) .. recovery (BASE+6)
        self.assertIn("192.168.3.1", en(ev.message))

    def test_outage_is_active_only_after_threshold(self):
        t = OutageTracker()
        self.feed(t, BASE, [(False, True)] * 2)
        self.assertEqual(t.active(), {})
        self.feed(t, BASE + 2, [(False, True)])
        self.assertEqual(t.active(), {"router": BASE})

    def test_internet_down_router_ok_means_isp_side(self):
        t = OutageTracker()
        events = self.feed(t, BASE, [(True, False)] * 4 + [(True, True)] + QUIET)
        (ev,) = events
        self.assertEqual(ev.kind, "internet_down")
        self.assertIn("ISP", en(ev.message))
        self.assertEqual(ev.duration, 4)

    def test_both_down_reports_router_first_and_overlap(self):
        t = OutageTracker()
        events = self.feed(t, BASE, [(False, False)] * 4 + [(True, True)] + QUIET)
        self.assertEqual([e.kind for e in events], ["router_down", "internet_down"])
        self.assertEqual(en(events[1].message), "router also unreachable")

    def test_router_failing_mid_internet_outage_counts_as_overlap(self):
        t = OutageTracker()
        pattern = [(True, False)] * 4 + [(False, False)] + [(True, True)] + QUIET
        events = self.feed(t, BASE, pattern)
        inet = next(e for e in events if e.kind == "internet_down")
        self.assertEqual(en(inet.message), "router also unreachable")

    def test_overlap_resets_for_the_next_outage(self):
        t = OutageTracker()
        self.feed(t, BASE, [(False, False)] * 4 + [(True, True)] + QUIET)
        events = self.feed(t, BASE + 100, [(True, False)] * 4 + [(True, True)] + QUIET)
        (ev,) = events
        self.assertIn("ISP", en(ev.message))

    def test_drops_close_together_are_one_unstable_episode(self):
        # 2026-10-05: 30 s down, 37 s back, 6 s down again - one episode, not two events.
        t = OutageTracker()
        pattern = ([(True, False)] * 30 + [(True, True)] * 37 + [(True, False)] * 6 + [(True, True)] + QUIET)
        events = self.feed(t, BASE, pattern)
        (ev,) = events
        self.assertEqual(ev.kind, "internet_down")
        self.assertEqual(ev.duration, 30 + 37 + 6)            # first drop .. last recovery
        self.assertEqual(ev.ts, BASE + 30 + 37 + 6)           # reported at the last recovery
        self.assertEqual(en(ev.message), "router reachable -> ISP/router WAN side; unstable: 2 drops")

    def test_active_still_reflects_the_moment_while_an_episode_is_merged(self):
        t = OutageTracker()
        self.feed(t, BASE, [(True, False)] * 4 + [(True, True)] * 10)
        self.assertEqual(t.active(), {})                       # toasts / watchdog see "recovered"
        self.feed(t, BASE + 14, [(True, False)] * 3)
        self.assertEqual(t.active(), {"internet": BASE + 14})

    def test_drops_further_apart_than_the_window_stay_separate(self):
        t = OutageTracker()
        pattern = ([(True, False)] * 4 + [(True, True)] * (MERGE_WINDOW_S + 5) + [(True, False)] * 4 + [(True, True)] + QUIET)
        events = self.feed(t, BASE, pattern)
        self.assertEqual([e.duration for e in events], [4, 4])
        self.assertTrue(all("unstable" not in en(e.message) for e in events))

    def test_a_drop_shorter_than_the_threshold_does_not_extend_the_episode(self):
        t = OutageTracker()
        pattern = [(True, False)] * 4 + [(True, True)] * 10 + [(True, False), (True, False), (True, True)] + QUIET
        (ev,) = self.feed(t, BASE, pattern)
        self.assertEqual(ev.duration, 4)

    def test_three_drops_are_counted_and_router_overlap_is_kept(self):
        t = OutageTracker()
        pattern = ([(True, False)] * 4 + [(True, True)] * 10 + [(False, False)] * 4 + [(True, True)] * 10
                   + [(True, False)] * 4 + [(True, True)] + QUIET)
        events = [e for e in self.feed(t, BASE, pattern) if e.kind == "internet_down"]
        (ev,) = events
        self.assertEqual(en(ev.message), "router also unreachable; unstable: 3 drops")

    def test_interrupt_reports_a_held_episode_and_an_open_drop_as_one(self):
        t = OutageTracker()
        self.feed(t, BASE, [(True, False)] * 4 + [(True, True)] * 10 + [(True, False)] * 5)
        (ev,) = t.interrupt(BASE + 19)
        self.assertEqual(ev.duration, 19)
        self.assertIn("cut short", en(ev.message))
        self.assertEqual(t.flush(BASE + 20), [])

    def test_interrupt_reports_a_held_episode_when_nothing_is_open(self):
        t = OutageTracker()
        self.feed(t, BASE, [(True, False)] * 4 + [(True, True)] * 3)
        (ev,) = t.interrupt(BASE + 7)
        self.assertEqual((ev.ts, ev.duration), (BASE + 4, 4))

    def test_flush_reports_a_held_episode_on_shutdown(self):
        t = OutageTracker()
        self.feed(t, BASE, [(True, False)] * 4 + [(True, True)] * 3)
        (ev,) = t.flush(BASE + 7)
        self.assertEqual(ev.duration, 4)
        self.assertEqual(t.flush(BASE + 8), [])

    def test_the_merge_window_can_be_turned_off(self):
        t = OutageTracker(merge_window_s=0)
        pattern = [(True, False)] * 4 + [(True, True)] * 3 + [(True, False)] * 4 + [(True, True)] * 3
        self.assertEqual(len(self.feed(t, BASE, pattern)), 2)

    def test_internet_ok_if_any_target_answers_is_callers_job(self):
        # The tracker only sees the combined flag; one flapping flag below threshold is no outage.
        t = OutageTracker()
        self.assertEqual(self.feed(t, BASE, [(True, False), (True, True)] * 10), [])


class MinuteAggregatorTests(unittest.TestCase):
    def test_rolls_only_when_minute_changes(self):
        a = MinuteAggregator()
        self.assertIsNone(a.roll(BASE))
        a.add_ping("router", "1.1.1.1", 5.0)
        self.assertIsNone(a.roll(BASE + 59))
        batch = a.roll(BASE + 60)
        self.assertEqual(batch.minute_ts, BASE)
        self.assertEqual(len(batch.rows), 1)
        self.assertIsNone(a.roll(BASE + 61))

    def test_stats(self):
        a = MinuteAggregator()
        a.roll(BASE)
        for rtt in (10.0, 20.0, 40.0, None):
            a.add_ping("router", "192.168.3.1", rtt)
        (row,) = a.roll(BASE + 60).rows
        self.assertEqual((row["sent"], row["lost"], row["avg"], row["max"], row["jitter"]),
                         (4, 1, 23.3, 40.0, 15.0))
        self.assertEqual(row["ip"], "192.168.3.1")

    def test_all_lost_and_single_sample(self):
        a = MinuteAggregator()
        a.roll(BASE)
        a.add_ping("dead", "9.9.9.9", None)
        a.add_ping("dead", "9.9.9.9", None)
        a.add_ping("one", "8.8.8.8", 7.0)
        rows = {r["target"]: r for r in a.roll(BASE + 60).rows}
        self.assertEqual((rows["dead"]["sent"], rows["dead"]["lost"], rows["dead"]["avg"]), (2, 2, None))
        self.assertIsNone(rows["one"]["jitter"])

    def test_signal_average_and_gap_in_minutes(self):
        a = MinuteAggregator()
        a.roll(BASE)
        a.add_signal(70)
        a.add_signal(75)
        batch = a.roll(BASE + 300)  # 5 minutes of silence
        self.assertEqual((batch.minute_ts, batch.signal_avg), (BASE, 72.5))

    def test_flush_partial_and_empty(self):
        a = MinuteAggregator()
        self.assertIsNone(a.flush())
        a.roll(BASE)
        self.assertIsNone(a.flush())  # nothing recorded
        a.roll(BASE)
        a.add_ping("router", None, 3.0)
        self.assertEqual(a.flush().rows[0]["sent"], 1)


# --- Monitor with fakes ----------------------------------------------------------

def wifi(state="connected", bssid="aa:aa", ssid="Home", channel=36, signal=70):
    return winutil.WifiState("Wi-Fi", state, ssid, bssid, "802.11ax", channel, signal, -60, 100, 100)


class Env:
    """Fake clock + fake network; `net[ip]` is the RTT the next ping returns (None = lost)."""

    def __init__(self):
        self.t = float(BASE)
        self.net = {"192.168.3.1": 2.0, "1.1.1.1": 40.0, "8.8.8.8": 50.0}
        self.wifi = wifi()
        self.gateway = "192.168.3.1"
        self.pings = []
        self.probes = []
        self.probe_net = {}   # probe target -> RTT, None = failed; unset targets succeed at 30 ms

    def ping(self, ip, timeout_ms):
        self.pings.append(ip)
        rtt = self.net.get(ip)
        return SimpleNamespace(ok=rtt is not None, rtt_ms=rtt)

    def probe(self, kind, target, timeout_s):
        self.probes.append((kind, target))
        rtt = self.probe_net.get(target, 30.0)
        return SimpleNamespace(ok=rtt is not None, rtt_ms=rtt)

    def make(self, storage=None, probes=False):
        self.storage = storage or Storage()
        settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        settings["probes"]["enabled"] = probes   # real probes only when a test asks for fake ones
        mon = Monitor(self.storage, settings, clock=lambda: self.t, ping_fn=self.ping, wifi_fn=lambda: self.wifi,
                      gateway_fn=lambda: self.gateway, probe_fn=self.probe)
        mon.refresh_gateway()
        mon.poll_wifi(baseline=True)
        return mon

    def run(self, mon, seconds, **net_changes):
        self.net.update(net_changes)
        for _ in range(seconds):
            mon.run_tick()
            self.t += 1


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.mon = self.env.make()
        self.addCleanup(self.mon._pool.shutdown)

    def events(self):
        return {e["kind"]: e for e in self.env.storage.query_events()}

    def test_minute_rollover_writes_stats_and_wifi(self):
        self.env.run(self.mon, 61)  # ticks at BASE..BASE+60: the last one closes the first minute
        rows = {r["target"]: r for r in self.env.storage.query_minute_stats(BASE, BASE + 60)}
        self.assertEqual(set(rows), {"router", "cloudflare", "google"})
        self.assertEqual((rows["router"]["sent"], rows["router"]["lost"], rows["router"]["ip"]),
                         (60, 0, "192.168.3.1"))
        self.assertEqual(rows["cloudflare"]["avg"], 40.0)
        (w,) = self.env.storage.query_wifi_stats(BASE, BASE + 60)
        self.assertEqual((w["state"], w["ssid"], w["rssi"]), ("connected", "Home", -60))

    def test_lost_pings_counted(self):
        self.env.run(self.mon, 30)
        self.env.run(self.mon, 31, **{"1.1.1.1": None})
        rows = {r["target"]: r for r in self.env.storage.query_minute_stats(BASE, BASE + 60)}
        self.assertEqual(rows["cloudflare"]["lost"], 30)
        self.assertEqual(rows["google"]["lost"], 0)

    def test_internet_outage_isp_side(self):
        self.env.run(self.mon, 5)
        self.env.run(self.mon, 4, **{"1.1.1.1": None, "8.8.8.8": None})
        self.env.run(self.mon, 2 + MERGE_WINDOW_S, **{"1.1.1.1": 40.0, "8.8.8.8": 50.0})
        ev = self.events()["internet_down"]
        self.assertEqual(ev["level"], "bad")
        self.assertIn("ISP", en(ev["message"]))
        self.assertEqual(ev["duration"], 4)
        self.assertNotIn("router_down", self.events())

    def test_one_internet_target_answering_is_not_an_outage(self):
        self.env.run(self.mon, 10, **{"1.1.1.1": None})
        self.assertNotIn("internet_down", self.events())

    def test_router_outage_with_internet_also_down(self):
        self.env.run(self.mon, 3)
        self.env.run(self.mon, 5, **{"192.168.3.1": None, "1.1.1.1": None, "8.8.8.8": None})
        self.env.run(self.mon, 2 + MERGE_WINDOW_S, **{"192.168.3.1": 2.0, "1.1.1.1": 40.0, "8.8.8.8": 50.0})
        ev = self.events()
        self.assertIn("192.168.3.1", en(ev["router_down"]["message"]))
        self.assertEqual(en(ev["internet_down"]["message"]), "router also unreachable")

    def test_sleep_gap_does_not_become_a_multi_hour_outage(self):
        # Windows drops Wi-Fi just before sleeping: the outage opens, then the PC sleeps 8 hours.
        self.env.run(self.mon, 5)
        self.env.run(self.mon, 4, **{"192.168.3.1": None, "1.1.1.1": None, "8.8.8.8": None})
        self.env.t += 8 * 3600
        self.env.run(self.mon, 3, **{"192.168.3.1": 2.0, "1.1.1.1": 40.0, "8.8.8.8": 50.0})
        ev = self.env.storage.query_events(limit=100)
        for e in ev:
            if e["kind"] in ("router_down", "internet_down"):
                self.assertLess(e["duration"], 60, e)  # only the real pre-sleep part
        gap = [e for e in ev if e["kind"] == "monitor_gap"]
        self.assertEqual(len(gap), 1)
        self.assertGreaterEqual(gap[0]["duration"], 8 * 3600)

    def test_failures_before_a_gap_do_not_combine_with_failures_after_it(self):
        self.env.run(self.mon, 3)
        self.env.run(self.mon, 2, **{"192.168.3.1": None})   # 2 fails: below threshold
        self.env.t += 3600
        self.env.run(self.mon, 1)                            # 1 more fail after resume
        self.assertNotIn("router", self.mon.snapshot()["outages"])

    def test_normal_tick_jitter_is_not_a_gap(self):
        self.env.run(self.mon, 3)
        self.env.t += 4  # a slow tick (e.g. pings timing out) is not a sleep
        self.env.run(self.mon, 3)
        self.assertNotIn("monitor_gap", self.events())

    def test_snapshot_shows_open_outage_and_recent_samples(self):
        self.env.run(self.mon, 5, **{"192.168.3.1": None})
        snap = self.mon.snapshot(window_s=3)
        self.assertIn("router", snap["outages"])
        self.assertEqual(snap["wifi"]["ssid"], "Home")
        self.assertEqual(snap["targets"]["router"], "192.168.3.1")
        times = {s[0] for s in snap["samples"]}
        self.assertTrue(all(t >= self.env.t - 3 for t in times))
        self.assertEqual(len(snap["samples"]), 3 * 3)  # 3 seconds x 3 targets

    def test_raw_samples_trimmed_to_15_minutes(self):
        self.env.run(self.mon, 1)
        self.env.t += 20 * 60
        self.env.run(self.mon, 1)
        self.assertEqual(len(self.mon.snapshot(window_s=10**6)["samples"]), 3)

    def test_wifi_state_change_event_and_gateway_refresh(self):
        self.env.wifi = wifi(state="disconnected", bssid="", ssid="")
        self.env.gateway = "10.0.0.1"
        self.mon.poll_wifi()
        ev = self.events()
        self.assertEqual(ev["wifi_state"]["message"], "connected -> disconnected ()")
        self.assertEqual(ev["wifi_state"]["level"], "warn")
        self.assertEqual(self.mon.snapshot()["targets"]["router"], "10.0.0.1")
        self.assertEqual(ev["gateway_change"]["message"], "192.168.3.1 -> 10.0.0.1")

    def test_roam_event(self):
        self.env.wifi = wifi(bssid="bb:bb", channel=149)
        self.mon.poll_wifi()
        self.assertEqual(self.events()["roam"]["message"], "aa:aa -> bb:bb ch149")

    def test_no_event_when_wifi_unchanged(self):
        self.mon.poll_wifi()
        self.assertEqual(self.env.storage.query_events(), [])

    def test_no_wifi_adapter_is_fine(self):
        self.env.wifi = None
        self.mon.poll_wifi()
        self.env.run(self.mon, 61)
        self.assertEqual(self.env.storage.query_wifi_stats(0, 10**9)[0]["state"], "connected")  # baseline kept

    def test_gateway_lookup_none_keeps_old_router(self):
        self.env.gateway = None
        self.mon.refresh_gateway()
        self.assertEqual(self.mon.snapshot()["targets"]["router"], "192.168.3.1")

    def test_missing_router_ip_counts_as_lost_without_pinging(self):
        env = Env()
        env.gateway = None
        mon = env.make()
        self.addCleanup(mon._pool.shutdown)
        env.run(mon, 4)
        self.assertNotIn(None, env.pings)
        self.assertEqual(len(env.pings), 4 * 2)  # only the two Internet targets
        self.assertIn("router", mon.snapshot()["outages"])

    def test_ping_function_exception_is_a_lost_ping(self):
        def boom(ip, timeout_ms):
            raise OSError("handle gone")
        self.mon._ping_fn = boom
        self.env.run(self.mon, 4)  # must not raise
        self.assertEqual(set(self.mon.snapshot()["outages"]), {"router", "internet"})

    def test_storage_failure_does_not_stop_the_monitor(self):
        def broken(*a, **k):
            raise sqlite3.OperationalError("database or disk is full")
        self.env.storage.add_minute_stat = broken
        self.env.storage.add_event = broken
        self.env.run(self.mon, 61, **{"1.1.1.1": None, "8.8.8.8": None})  # rollover + internet outage
        self.env.run(self.mon, 5)
        self.assertGreater(self.mon.write_errors, 0)

    def test_gateway_refresh_failure_is_swallowed(self):
        def boom():
            raise RuntimeError("powershell died")
        self.mon._gateway_fn = boom
        self.mon.refresh_gateway()
        self.assertEqual(self.mon.snapshot()["targets"]["router"], "192.168.3.1")


ALL_ICMP_INTERNET_DOWN = {"1.1.1.1": None, "8.8.8.8": None}


class ProbeTests(unittest.TestCase):
    """TCP/HTTP probes confirm that the Internet is up when ICMP says otherwise."""

    def setUp(self):
        self.env = Env()
        self.mon = self.env.make(probes=True)
        self.addCleanup(self.mon._pool.shutdown)
        self.addCleanup(self.mon._probe_pool.shutdown)

    def events(self):
        return {e["kind"]: e for e in self.env.storage.query_events()}

    def tick(self, seconds, probe_every=10, **icmp):
        """Advance time; run a probe round every `probe_every` s like the probe loop does."""
        self.env.net.update(icmp)
        for i in range(seconds):
            if probe_every and i % probe_every == 0:
                self.mon.run_probe_round()
            self.mon.run_tick()
            self.env.t += 1

    def test_default_probes_are_tcp_and_http(self):
        self.assertEqual(set(self.mon._probes), {"tcp_cloudflare", "tcp_google", "http_cloudflare"})
        self.assertEqual(self.mon._probes["http_cloudflare"][0], "http")

    def test_icmp_blocked_but_probes_ok_is_not_an_internet_outage(self):
        # The false alarm this feature exists to prevent: ISP drops ICMP, traffic is fine.
        self.tick(5)
        self.tick(120, **ALL_ICMP_INTERNET_DOWN)
        self.tick(3, **{"1.1.1.1": 40.0, "8.8.8.8": 50.0})
        self.assertNotIn("internet_down", self.events())
        self.assertEqual(self.mon.snapshot()["outages"], {})

    def test_icmp_and_probes_down_is_a_real_outage(self):
        self.tick(5)
        for t in ("1.1.1.1:443", "8.8.8.8:443", "http://cp.cloudflare.com/generate_204"):
            self.env.probe_net[t] = None
        self.tick(40, **ALL_ICMP_INTERNET_DOWN)
        self.assertIn("internet", self.mon.snapshot()["outages"])
        self.env.probe_net.clear()
        self.tick(12 + MERGE_WINDOW_S, **{"1.1.1.1": 40.0, "8.8.8.8": 50.0})
        ev = self.events()["internet_down"]
        self.assertIn("ISP", en(ev["message"]))

    def test_one_probe_succeeding_is_enough(self):
        self.tick(5)
        self.env.probe_net.update({"1.1.1.1:443": None, "8.8.8.8:443": None})  # HTTP probe still works
        self.tick(60, **ALL_ICMP_INTERNET_DOWN)
        self.assertNotIn("internet_down", self.events())

    def test_probes_down_but_icmp_up_is_not_an_outage(self):
        # e.g. a firewall blocking port 443 to those hosts: ping still proves the Internet works.
        for t in ("1.1.1.1:443", "8.8.8.8:443", "http://cp.cloudflare.com/generate_204"):
            self.env.probe_net[t] = None
        self.tick(60)
        self.assertEqual(self.mon.snapshot()["outages"], {})

    def test_a_stale_probe_round_never_confirms(self):
        self.tick(1)                             # one successful probe round...
        self.env.t += 300                        # ...then the probe loop stalls (and ICMP is down)
        self.env.net.update(ALL_ICMP_INTERNET_DOWN)
        self.mon._last_tick = self.env.t - 1     # not a sleep gap, only the probes are stale
        for _ in range(5):
            self.mon.run_tick()
            self.env.t += 1
        self.assertIn("internet", self.mon.snapshot()["outages"])

    def test_before_the_first_round_icmp_decides(self):
        self.env.net.update(ALL_ICMP_INTERNET_DOWN)
        for _ in range(5):
            self.mon.run_tick()
            self.env.t += 1
        self.assertIn("internet", self.mon.snapshot()["outages"])

    def test_probe_results_are_stored_per_target_next_to_icmp(self):
        self.env.probe_net["8.8.8.8:443"] = None
        self.tick(61)
        rows = {r["target"]: r for r in self.env.storage.query_minute_stats(BASE, BASE + 60)}
        # A round that starts in the same second as the minute boundary may land in the old minute
        # (the probe loop is independent of the ping tick that rolls the minute): 6 or 7 rounds.
        self.assertIn(rows["tcp_cloudflare"]["sent"], (6, 7))
        self.assertEqual(rows["tcp_cloudflare"]["lost"], 0)
        self.assertEqual(rows["tcp_google"]["sent"], rows["tcp_google"]["lost"])  # all failed
        self.assertEqual(rows["http_cloudflare"]["ip"], "http://cp.cloudflare.com/generate_204")
        self.assertEqual(rows["router"]["sent"], 60)

    def test_probe_exception_counts_as_failed_not_fatal(self):
        def boom(*a):
            raise OSError("socket layer died")
        self.mon._probe_fn = boom
        self.mon.run_probe_round()
        self.assertFalse(self.mon.snapshot()["probe"]["ok"])

    def test_snapshot_reports_the_latest_round(self):
        self.assertIsNone(self.mon.snapshot()["probe"])
        self.mon.run_probe_round()
        self.assertTrue(self.mon.snapshot()["probe"]["ok"])

    def test_disabled_probes_change_nothing(self):
        env = Env()
        mon = env.make(probes=False)
        self.addCleanup(mon._pool.shutdown)
        mon.run_probe_round()
        env.run(mon, 5)
        self.assertEqual((env.probes, mon.snapshot()["probe"], mon._probe_pool), ([], None, None))

    def test_probe_loop_runs_with_real_threads(self):
        storage = Storage()
        settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        settings.update(ping_interval_s=0.02, wifi_poll_s=0.05, gateway_refresh_s=0.05)
        settings["probes"].update(interval_s=0.05)
        net = Env()
        mon = Monitor(storage, settings, ping_fn=net.ping, wifi_fn=lambda: net.wifi, gateway_fn=lambda: net.gateway,
                      probe_fn=net.probe)
        mon.start()
        time.sleep(0.5)
        mon.stop()
        self.assertGreaterEqual(len(net.probes), 6)  # 3 probes x at least 2 rounds


class MonitorThreadTests(unittest.TestCase):
    def test_start_stop_with_real_threads(self):
        storage = Storage()
        settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        settings.update(ping_interval_s=0.02, wifi_poll_s=0.05, gateway_refresh_s=0.05)
        settings["probes"].update(interval_s=0.05)
        net = Env()
        mon = Monitor(storage, settings, ping_fn=net.ping, wifi_fn=lambda: net.wifi, gateway_fn=lambda: net.gateway,
                      probe_fn=net.probe)
        mon.start()
        time.sleep(0.4)
        snap = mon.snapshot()
        started = time.monotonic()
        mon.stop()
        self.assertLess(time.monotonic() - started, 2)
        self.assertGreater(len(snap["samples"]), 10)
        kinds = [e["kind"] for e in storage.query_events()]
        self.assertIn("monitor_start", kinds)
        self.assertIn("monitor_stop", kinds)
        # partial minute is flushed on stop
        self.assertTrue(storage.query_minute_stats(0, 2**40))

    def test_cannot_start_twice(self):
        net = Env()
        mon = Monitor(Storage(), copy.deepcopy(config.DEFAULT_SETTINGS), ping_fn=net.ping,
                      wifi_fn=lambda: net.wifi, gateway_fn=lambda: net.gateway, probe_fn=net.probe)
        mon.start()
        self.addCleanup(mon.stop)
        with self.assertRaises(RuntimeError):
            mon.start()


if __name__ == "__main__":
    unittest.main()
