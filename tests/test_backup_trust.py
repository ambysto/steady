"""backup.json is writable without Admin; the elevated helper that restores it must not write outside each
tweak's own domain (ADR-0016)."""
import copy
import unittest

from app import i18n, tweaks
from app.failover import MetricSwitch, check_metric_original
from tests.test_failover import FakeSystem as MetricSystem
from tests.test_tweaks import CLASS_KEY, SUB_PCIE, SET_ASPM, TCPIP, FakeSystem, MemoryBackup, manager

LUA = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"


def catalog():
    return tweaks.build_catalog(dns_benchmark=lambda servers: [], captive=lambda: False)


def text(message):
    return i18n.render(message, "en")


class RealCaptureStillRestoresTests(unittest.TestCase):
    """Every backup a real capture writes passes the check: the round trip is unchanged."""

    def test_every_supported_catalog_tweak_round_trips(self):
        for t in catalog():
            if t.id == "dns_fastest":
                continue   # needs a benchmark; covered below
            with self.subTest(t.id):
                mgr, s, backup, _ = manager(tweak_list=[t])
                try:
                    if not t.read(s).supported:
                        continue
                except KeyError:
                    continue          # FakeSystem lacks this setting
                before = s.snapshot()
                self.assertTrue(mgr.enable(t.id).ok)
                t.check_original(s, backup.data[t.id]["original"])
                out = mgr.disable(t.id)
                self.assertTrue(out.ok, text(out.message))
                self.assertEqual(s.snapshot(), before)

    def test_the_wireless_power_setting_round_trips(self):
        s = FakeSystem()
        s.power[(tweaks.SUB_WIRELESS, tweaks.SET_WIRELESS)] = (3, 3)
        mgr, s, backup, _ = manager(s, tweak_list=[t for t in catalog() if t.id == "power_wireless_max"])
        self.assertTrue(mgr.enable("power_wireless_max").ok)
        self.assertTrue(mgr.disable("power_wireless_max").ok)
        self.assertEqual(s.power[(tweaks.SUB_WIRELESS, tweaks.SET_WIRELESS)], (3, 3))

    def test_a_dns_capture_passes_the_check(self):
        s = FakeSystem()
        s.dns.update(servers=["10.0.0.53", "192.168.1.1"], static=True)
        s.doh.pop("9.9.9.9")
        t = next(t for t in catalog() if t.id == "dns_fastest")
        t.check_original(s, t.capture(s))


class TamperedBackupTests(unittest.TestCase):
    def refused(self, tweak_id, original, system=None):
        """Plant `original` in backup.json, turn the tweak off: nothing written, backup kept, reported as bad."""
        backup = MemoryBackup({tweak_id: {"original": original, "captured_at": 1, "source": "capture"}})
        planted = copy.deepcopy(backup.data)
        mgr, s, backup, events = manager(system, backup, tweak_list=catalog())
        out = mgr.disable(tweak_id)
        self.assertFalse(out.ok)
        self.assertFalse(out.changed)
        self.assertEqual(s.writes(), [])
        self.assertEqual(backup.data, planted)
        self.assertEqual(events[-1][2], "bad")
        self.assertIn("nothing was restored", text(out.message))
        return text(out.message)

    # -- registry -------------------------------------------------------------------------------

    def test_registry_tweaks_never_write_another_hklm_path(self):
        for tid, name in (("tcp_timedwait", "TcpTimedWaitDelay"), ("device_power_off", "PnPCapabilities")):
            with self.subTest(tid):
                why = self.refused(tid, {"path": LUA, "name": name, "value": 0})
                self.assertIn("registry path", why)

    def test_registry_tweaks_never_write_another_value_name(self):
        self.refused("tcp_timedwait", {"path": TCPIP, "name": "EnableLUA", "value": 0})
        self.refused("device_power_off", {"path": CLASS_KEY, "name": "NetworkAddress", "value": None})

    def test_registry_values_must_be_dwords(self):
        for value in (-1, 2 ** 32, "30", 1.5, True, [30]):
            with self.subTest(value=value):
                self.refused("tcp_timedwait", {"path": TCPIP, "name": "TcpTimedWaitDelay", "value": value})

    def test_extra_or_missing_fields_are_refused(self):
        self.refused("tcp_timedwait", {"path": TCPIP, "name": "TcpTimedWaitDelay"})
        self.refused("tcp_timedwait", {"path": TCPIP, "name": "TcpTimedWaitDelay", "value": 30, "type": "REG_SZ"})

    def test_an_entry_that_is_not_an_object_is_refused(self):
        for entry in ("x", 5, None, [1]):
            with self.subTest(entry=entry):
                backup = MemoryBackup({"tcp_timedwait": entry if entry is not None else {"captured_at": 1}})
                mgr, s, backup, events = manager(backup=backup, tweak_list=catalog())
                self.assertFalse(mgr.disable("tcp_timedwait").ok)
                self.assertEqual(s.writes(), [])

    # -- Wi-Fi card ---------------------------------------------------------------------------

    def test_adapter_properties_stay_on_this_card_this_property_and_its_values(self):
        good = {"adapter": "Wi-Fi", "keyword": "PowerSaveMode", "value": "2", "display": "Auto"}
        self.refused("wifi_power_saving", dict(good, adapter="Ethernet"))
        self.refused("wifi_power_saving", dict(good, keyword="NetworkAddress", value="025E009A4024"))
        self.refused("wifi_power_saving", dict(good, keyword="*WakeOnMagicPacket", value="1"))   # another tweak's
        self.refused("wifi_power_saving", dict(good, value="7"))
        self.refused("wifi_power_saving", dict(good, value=2))

    def test_a_card_that_was_replaced_is_refused_not_guessed(self):
        s = FakeSystem()
        s.adapter = None
        self.refused("wifi_power_saving", {"adapter": "Wi-Fi", "keyword": "PowerSaveMode", "value": "2"}, s)

    def test_bindings_and_rsc_stay_on_this_card_and_component(self):
        self.refused("ipv6_off", {"adapter": "Wi-Fi", "component": "ms_server", "enabled": False})
        self.refused("ipv6_off", {"adapter": "Ethernet", "component": "ms_tcpip6", "enabled": True})
        self.refused("ipv6_off", {"adapter": "Wi-Fi", "component": "ms_tcpip6", "enabled": "no"})
        self.refused("rsc_off", {"adapter": "Ethernet", "ipv4": True, "ipv6": True})
        self.refused("rsc_off", {"adapter": "Wi-Fi", "ipv4": 1, "ipv6": None})

    # -- power, netsh, offload -----------------------------------------------------------------

    def test_power_tweaks_stay_on_their_setting_and_range(self):
        self.refused("power_pcie_aspm_off", {"subgroup": SUB_PCIE, "setting": "245d8541-3943-4422-b025-13a784f679b7",
                                             "ac": 0, "dc": 0})
        self.refused("power_pcie_aspm_off", {"subgroup": tweaks.SUB_WIRELESS, "setting": SET_ASPM, "ac": 0, "dc": 0})
        self.refused("power_pcie_aspm_off", {"subgroup": SUB_PCIE, "setting": SET_ASPM, "ac": 3, "dc": 0})
        s = FakeSystem()
        s.power[(tweaks.SUB_WIRELESS, tweaks.SET_WIRELESS)] = (0, 0)
        self.refused("power_wireless_max", {"subgroup": tweaks.SUB_WIRELESS, "setting": tweaks.SET_WIRELESS,
                                            "ac": 0, "dc": 4}, s)

    def test_global_settings_stay_on_their_setting_and_values(self):
        self.refused("tcp_ecn", {"setting": "autotuninglevel", "value": "disabled"})
        self.refused("tcp_ecn", {"setting": "ecncapability", "value": "enabled; shutdown /s"})
        self.refused("packet_coalescing_off", {"setting": "ReceiveSideScaling", "value": "Disabled"})
        self.refused("packet_coalescing_off", {"setting": "PacketCoalescingFilter", "value": "Off"})

    # -- DNS ----------------------------------------------------------------------------------

    def dns(self, **changes):
        doh = {address: {"present": True, "template": template, "auto_upgrade": False, "fallback_to_udp": False}
               for address, (_, template) in tweaks.DOH_ADDRESSES.items()}
        return {"interface_index": 6, "static": False, "servers": ["192.168.1.1"], "doh": doh, **changes}

    def test_a_doh_template_can_only_be_the_providers_own(self):
        original = self.dns()
        original["doh"]["1.1.1.1"]["template"] = "https://dns.example.net/dns-query"
        self.assertIn("template", self.refused("dns_fastest", original))

    def test_doh_entries_only_for_the_candidate_addresses(self):
        original = self.dns()
        original["doh"]["203.0.113.10"] = {"present": True, "template": "", "auto_upgrade": True,
                                           "fallback_to_udp": False}
        self.refused("dns_fastest", original)

    def test_static_servers_must_be_a_short_list_of_unicast_ipv4_addresses(self):
        for servers in ([], ["0.0.0.0"], ["224.0.0.251"], ["255.255.255.255"], ["240.0.0.1"], ["fe80::1"],
                        ["1.1.1.1; Set-ExecutionPolicy"], ["10.0.0.1"] * 9, "1.1.1.1", [1]):
            with self.subTest(servers=servers):
                self.refused("dns_fastest", self.dns(static=True, servers=servers))

    def test_the_interface_must_be_an_index(self):
        for index in (0, -1, "6", True, 6.0):
            with self.subTest(index=index):
                self.refused("dns_fastest", self.dns(interface_index=index))

    def test_restore_writes_the_built_in_template_even_when_the_stored_one_is_empty(self):
        original = self.dns(static=True, servers=["10.0.0.53"])
        original["doh"]["1.1.1.1"]["template"] = ""
        mgr, s, backup, _ = manager(backup=MemoryBackup({"dns_fastest": {"original": original}}), tweak_list=catalog())
        mgr.disable("dns_fastest")
        templates = {args[0]: args[1] for _, name, args in s.writes() if name == "doh_set"}
        self.assertEqual(templates, {a: t for a, (_, t) in tweaks.DOH_ADDRESSES.items()})
        self.assertIn(("write", "dns_servers_set", (6, ["10.0.0.53"])), s.writes())

    # -- the rest -------------------------------------------------------------------------------

    def test_every_catalog_tweak_checks_its_backup(self):
        for t in catalog():
            with self.subTest(t.id):
                self.assertIsNot(type(t).check_original, tweaks.Tweak.check_original)

    def test_a_kind_without_a_check_fails_closed(self):
        class Bare(tweaks.Tweak):
            def restore(self, sys_, original):
                raise AssertionError("must not be reached")

        mgr, s, *_ = manager(backup=MemoryBackup({"bare": {"original": {}}}), tweak_list=[Bare("bare", "x", "low")])
        Bare.read = lambda self, sys_: tweaks.Reading(True, True)
        self.assertFalse(mgr.disable("bare").ok)

    def test_adopting_an_outside_backup_uses_the_same_check(self):
        s = FakeSystem()
        s.registry[(TCPIP, "TcpTimedWaitDelay")] = 30
        mgr, s, backup, _ = manager(s, tweak_list=catalog())
        out = mgr.adopt_backup("tcp_timedwait", {"path": LUA, "name": "TcpTimedWaitDelay", "value": 0}, "manual")
        self.assertFalse(out.ok)
        self.assertEqual(backup.data, {})


class FailoverBackupTests(unittest.TestCase):
    def switch(self, store):
        self.store = store
        return MetricSwitch(self.system, load_backup=lambda: copy.deepcopy(self.store),
                            save_backup=lambda d: setattr(self, "store", d))

    def setUp(self):
        self.system = MetricSystem({21: {"automatic": False, "metric": 5}})

    def test_a_metric_outside_1_to_9999_is_refused(self):
        for original in ({"automatic": False, "metric": 0}, {"automatic": False, "metric": 10000},
                         {"automatic": False, "metric": "5"}, {"automatic": "yes"}, ["x"]):
            with self.subTest(original=original):
                res = self.switch({"failover:21": {"original": original, "name": "Ethernet"}}).restore(21)
                self.assertFalse(res.ok)
                self.assertEqual(self.system.calls, [])
                self.assertIn("failover:21", self.store)

    def test_a_real_backup_still_restores(self):
        res = self.switch({"failover:21": {"original": {"automatic": True, "metric": 35}, "name": "Ethernet"}}).restore(21)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.system.calls, [(21, None)])

    def test_the_index_must_be_positive(self):
        with self.assertRaises(ValueError):
            check_metric_original(0, {"original": {"automatic": True}})
        with self.assertRaises(ValueError):
            check_metric_original(True, {"original": {"automatic": True}})


if __name__ == "__main__":
    unittest.main()
