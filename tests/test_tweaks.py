import contextlib
import copy
import io
import os
import sys
import tempfile
import unittest

from app import config, i18n, tweaks
from app.tweaks import (AdapterPropertyTweak, BindingTweak, NoDefaultRestore, PacketCoalescingOffTweak, PowerCfgTweak,
                        RegistryDwordTweak, RscOffTweak, TcpEcnTweak, TweakManager)
from app.winsys import SystemWriteError

CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0011"


def vi(message):
    """These tests check the Vietnamese wording, which app/locales/vi.json keeps."""
    return i18n.render(message, "vi")


TCPIP = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
SUB_PCIE, SET_ASPM = "501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5"


def prop(display, keyword, current, values, default):
    disp = [d for d, _ in values]
    reg = [r for _, r in values]
    return {"DisplayName": display, "RegistryKeyword": keyword, "RegistryValue": [current],
            "DisplayValue": disp[reg.index(current)], "ValidDisplayValues": disp, "ValidRegistryValues": reg,
            "DefaultRegistryValue": default}


class FakeSystem:
    """In-memory machine shaped like the dev PC before EXP-001. Records every call."""

    def __init__(self):
        self.adapter = "Wi-Fi"
        self.props = {"Wi-Fi": [
            prop("Power Saving", "PowerSaveMode", "2", [("Disabled", "0"), ("Auto", "2"), ("Enabled", "1")], "2"),
            prop("Wake on Magic Packet", "*WakeOnMagicPacket", "1", [("Disabled", "0"), ("Enabled", "1")], "1"),
        ]}
        self.registry = {(CLASS_KEY, "PnPCapabilities"): 16}
        self.power = {(SUB_PCIE, SET_ASPM): (1, 2)}
        self.bindings = {("Wi-Fi", "ms_tcpip6"): True}
        self.network = {"key": "6|192.0.2.1|HomeNet", "interface_index": 6, "gateway": "192.0.2.1"}
        self.qos = {}                 # policy name -> bits per second
        self.mtu = {6: 1500}
        self.ecn = "disabled"
        self.rsc = {"Wi-Fi": {"ipv4": True, "ipv6": True, "ipv4_supported": True, "ipv6_supported": True}}
        self.coalescing = "Enabled"
        self.dns = {6: {"static": [], "effective": ["192.0.2.1"]}}   # DHCP: the router answers
        self.dns_suffix = {6: ""}
        self.doh = {ip: {"auto_upgrade": False, "fallback": False}
                    for ip in ("1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112")}
        self.log = []                 # ("read"|"write", method, args)
        self.fail = set()             # write methods that raise
        self.noop = set()             # write methods that silently do nothing
        self.fail_after_writes = None

    # reads
    def _r(self, name, *args):
        self.log.append(("read", name, args))

    def wifi_adapter_name(self):
        self._r("wifi_adapter_name")
        return self.adapter

    def adapter_properties(self, adapter):
        self._r("adapter_properties", adapter)
        return copy.deepcopy(self.props.get(adapter, []))

    def adapter_class_key(self, adapter):
        self._r("adapter_class_key", adapter)
        return CLASS_KEY if adapter == "Wi-Fi" else None

    def registry_get(self, path, name):
        self._r("registry_get", path, name)
        return self.registry.get((path, name))

    def power_get(self, sub, setting):
        self._r("power_get", sub, setting)
        return self.power[(sub, setting)]

    def binding_get(self, adapter, component):
        self._r("binding_get", adapter, component)
        return self.bindings.get((adapter, component))

    def current_network(self):
        self._r("current_network")
        return copy.deepcopy(self.network)

    def qos_throttle_get(self, name):
        self._r("qos_throttle_get", name)
        return self.qos.get(name)

    def interface_mtu_get(self, index):
        self._r("interface_mtu_get", index)
        return self.mtu[index]

    def tcp_ecn_get(self):
        self._r("tcp_ecn_get")
        return self.ecn

    def rsc_get(self, adapter):
        self._r("rsc_get", adapter)
        return copy.deepcopy(self.rsc.get(adapter))

    def packet_coalescing_get(self):
        self._r("packet_coalescing_get")
        return self.coalescing

    def dns_get(self, index):
        self._r("dns_get", index)
        return copy.deepcopy(self.dns[index])

    def dns_suffix_get(self, index):
        self._r("dns_suffix_get", index)
        return self.dns_suffix[index]

    def doh_get(self, server):
        self._r("doh_get", server)
        return copy.deepcopy(self.doh.get(server))

    # writes
    def _w(self, name, *args):
        self.log.append(("write", name, args))
        writes = sum(1 for kind, *_ in self.log if kind == "write")
        if name in self.fail or (self.fail_after_writes is not None and writes > self.fail_after_writes):
            raise SystemWriteError(f"{name} failed (injected)")
        return name not in self.noop

    def _prop(self, adapter, keyword):
        return next(p for p in self.props[adapter] if p["RegistryKeyword"] == keyword)

    def adapter_property_set(self, adapter, keyword, value):
        if self._w("adapter_property_set", adapter, keyword, value):
            p = self._prop(adapter, keyword)
            p["RegistryValue"] = [value]
            p["DisplayValue"] = p["ValidDisplayValues"][p["ValidRegistryValues"].index(value)]

    def adapter_property_reset(self, adapter, keyword):
        if self._w("adapter_property_reset", adapter, keyword):
            p = self._prop(adapter, keyword)
            self.adapter_property_set(adapter, keyword, p["DefaultRegistryValue"])

    def registry_set_dword(self, path, name, value):
        if self._w("registry_set_dword", path, name, value):
            self.registry[(path, name)] = value

    def registry_delete(self, path, name):
        if self._w("registry_delete", path, name):
            self.registry.pop((path, name), None)

    def power_set(self, sub, setting, ac, dc):
        if self._w("power_set", sub, setting, ac, dc):
            self.power[(sub, setting)] = (ac, dc)

    def binding_set(self, adapter, component, enabled):
        if self._w("binding_set", adapter, component, enabled):
            self.bindings[(adapter, component)] = enabled

    def qos_throttle_set(self, name, rate):
        if self._w("qos_throttle_set", name, rate):
            self.qos[name] = rate

    def qos_policy_remove(self, name):
        if self._w("qos_policy_remove", name):
            self.qos.pop(name, None)

    def interface_mtu_set(self, index, mtu):
        if self._w("interface_mtu_set", index, mtu):
            self.mtu[index] = mtu

    def tcp_ecn_set(self, value):
        if self._w("tcp_ecn_set", value):
            self.ecn = value

    def rsc_set(self, adapter, ipv4, ipv6):
        if self._w("rsc_set", adapter, ipv4, ipv6):
            for key, value in (("ipv4", ipv4), ("ipv6", ipv6)):
                if value is not None:
                    self.rsc[adapter][key] = value

    def packet_coalescing_set(self, value):
        if self._w("packet_coalescing_set", value):
            self.coalescing = value

    def dns_servers_set(self, index, servers):
        if self._w("dns_servers_set", index, list(servers)):
            self.dns[index] = {"static": list(servers), "effective": list(servers)}

    def dns_servers_reset(self, index):
        if self._w("dns_servers_reset", index):
            self.dns[index] = {"static": [], "effective": ["192.0.2.1"]}

    def doh_set(self, server, auto_upgrade, fallback):
        if self._w("doh_set", server, auto_upgrade, fallback):
            self.doh[server] = {"auto_upgrade": auto_upgrade, "fallback": fallback}

    # helpers
    def writes(self):
        return [entry for entry in self.log if entry[0] == "write"]

    def snapshot(self):
        return copy.deepcopy((self.props, self.registry, self.power, self.bindings, self.qos, self.mtu, self.ecn, self.rsc,
                               self.coalescing, self.dns, self.doh))


def power_saving():
    return AdapterPropertyTweak("wifi_power_saving", "Tắt tiết kiệm điện của card", "low",
                                [(r"Power Saving", r"Disabled"), (r"MIMO Power Save Mode", r"No SMPS")])


def wake_magic():
    return AdapterPropertyTweak("wifi_wake_magic", "Tắt Wake on Magic Packet", "low",
                                [(r"Wake on Magic Packet", r"Disabled")])


def device_power_off():
    return RegistryDwordTweak("device_power_off", "Không cho Windows tắt card", "low",
                              path=lambda s: s.adapter_class_key(s.wifi_adapter_name()),
                              value_name="PnPCapabilities", target=lambda cur: (cur or 0) | 0x18)


def tcp_timedwait():
    return RegistryDwordTweak("tcp_timedwait", "Rút ngắn TIME_WAIT", "medium", path=TCPIP,
                              value_name="TcpTimedWaitDelay", target=lambda cur: 30, needs_reboot=True)


def aspm_off():
    return PowerCfgTweak("power_pcie_aspm_off", "Tắt PCIe ASPM", "low", subgroup=SUB_PCIE, setting=SET_ASPM,
                         ac=0, dc=0)


def ipv6_off():
    return BindingTweak("ipv6_off", "Tắt IPv6 trên Wi‑Fi", "experimental", component="ms_tcpip6", enabled=False)


def all_tweaks():
    return [power_saving(), wake_magic(), device_power_off(), tcp_timedwait(), aspm_off(), ipv6_off()]


class MemoryBackup:
    def __init__(self, initial=None):
        self.data = copy.deepcopy(initial or {})
        self.saves = 0
        self.fail_load = self.fail_save = False

    def load(self):
        if self.fail_load:
            raise ValueError("backup.json is corrupt")
        return copy.deepcopy(self.data)

    def save(self, data):
        if self.fail_save:
            raise OSError("disk full")
        self.saves += 1
        self.data = copy.deepcopy(data)


def manager(system=None, backup=None, admin=True, tweak_list=None):
    system = system or FakeSystem()
    backup = backup or MemoryBackup()
    events = []
    mgr = TweakManager(system, tweak_list or all_tweaks(), load_backup=backup.load, save_backup=backup.save,
                       record_event=lambda k, m, lvl: events.append((k, m, lvl)), is_admin=lambda: admin,
                       clock=lambda: 1000.0)
    return mgr, system, backup, events


class TweakDefinitionTests(unittest.TestCase):
    def test_bad_id_or_risk(self):
        with self.assertRaises(ValueError):
            aspm = PowerCfgTweak("Bad-Id", "x", "low", subgroup=SUB_PCIE, setting=SET_ASPM, ac=0, dc=0)
        with self.assertRaises(ValueError):
            PowerCfgTweak("ok_id", "x", "dangerous", subgroup=SUB_PCIE, setting=SET_ASPM, ac=0, dc=0)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            TweakManager(FakeSystem(), [aspm_off(), aspm_off()], load_backup=dict, save_backup=lambda d: None)

    def test_adapter_candidates_fall_back_to_other_vendor_name(self):
        s = FakeSystem()
        s.props["Wi-Fi"][0] = prop("MIMO Power Save Mode", "MIMOPowerSaveMode", "3",
                                   [("Auto SMPS", "3"), ("No SMPS", "0")], "3")
        r = power_saving().read(s)
        self.assertTrue(r.supported)
        self.assertFalse(r.enabled)
        self.assertEqual(r.current, "Auto SMPS")

    def test_adapter_property_missing_is_unsupported_not_an_error(self):
        s = FakeSystem()
        s.props["Wi-Fi"] = []
        r = power_saving().read(s)
        self.assertEqual((r.supported, r.enabled), (False, False))
        self.assertIn("không có thuộc tính", vi(r.reason))

    def test_adapter_target_value_missing_lists_what_exists(self):
        s = FakeSystem()
        s.props["Wi-Fi"][0] = prop("Power Saving", "PowerSaveMode", "2", [("Auto", "2"), ("Max", "1")], "2")
        r = power_saving().read(s)
        self.assertFalse(r.supported)
        self.assertIn("Auto, Max", vi(r.reason))

    def test_no_wifi_adapter_is_unsupported(self):
        s = FakeSystem()
        s.adapter = None
        self.assertFalse(power_saving().read(s).supported)
        self.assertFalse(device_power_off().read(s).supported)
        self.assertFalse(ipv6_off().read(s).supported)

    def test_original_from_display(self):
        s = FakeSystem()
        orig = power_saving().original_from_display(s, "Auto")
        self.assertEqual((orig["keyword"], orig["value"]), ("PowerSaveMode", "2"))
        with self.assertRaises(ValueError):
            power_saving().original_from_display(s, "Banana")

    def test_bitmask_registry_target(self):
        s = FakeSystem()
        t = device_power_off()
        self.assertFalse(t.read(s).enabled)            # 16
        s.registry[(CLASS_KEY, "PnPCapabilities")] = 24
        self.assertTrue(t.read(s).enabled)
        s.registry[(CLASS_KEY, "PnPCapabilities")] = 24 | 0x100
        self.assertTrue(t.read(s).enabled)             # extra OEM bits are kept and still count
        del s.registry[(CLASS_KEY, "PnPCapabilities")]
        self.assertFalse(t.read(s).enabled)            # absent

    def test_power_restore_without_backup_refuses(self):
        with self.assertRaises(NoDefaultRestore):
            aspm_off().restore(FakeSystem(), None)


class EnableTests(unittest.TestCase):
    def test_backup_is_saved_before_anything_is_written(self):
        order = []
        system = FakeSystem()
        backup = MemoryBackup()
        real_save, real_write = backup.save, system._w

        def save(data):
            order.append("save")
            real_save(data)

        def write(name, *args):
            order.append(name)
            return real_write(name, *args)
        backup.save, system._w = save, write
        mgr, *_ = manager(system, backup)
        self.assertTrue(mgr.enable("power_pcie_aspm_off").ok)
        self.assertEqual(order, ["save", "power_set"])
        self.assertEqual(backup.data["power_pcie_aspm_off"]["original"]["ac"], 1)

    def test_enable_applies_and_records_event(self):
        mgr, s, backup, events = manager()
        out = mgr.enable("wifi_power_saving")
        self.assertTrue(out.ok and out.changed)
        self.assertTrue(out.state.enabled)
        self.assertEqual(backup.data["wifi_power_saving"]["original"]["value"], "2")
        self.assertEqual(backup.data["wifi_power_saving"]["source"], "capture")
        self.assertEqual(events[-1][0], "tweak_enabled")
        self.assertTrue(mgr.recently_changed(60))

    def test_already_enabled_is_a_noop_without_backup(self):
        mgr, s, backup, events = manager()
        s.power[(SUB_PCIE, SET_ASPM)] = (0, 0)
        out = mgr.enable("power_pcie_aspm_off")
        self.assertTrue(out.ok)
        self.assertFalse(out.changed)
        self.assertEqual((s.writes(), backup.data, events), ([], {}, []))

    def test_unsupported_writes_nothing(self):
        s = FakeSystem()
        s.bindings.clear()
        mgr, _, backup, _ = manager(s)
        out = mgr.enable("ipv6_off")
        self.assertFalse(out.ok)
        self.assertEqual((s.writes(), backup.data), ([], {}))

    def test_needs_admin(self):
        mgr, s, backup, _ = manager(admin=False)
        out = mgr.enable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertIn("Administrator", vi(out.message))
        self.assertEqual((s.writes(), backup.data), ([], {}))

    def test_corrupt_backup_blocks_enable(self):
        backup = MemoryBackup()
        backup.fail_load = True
        mgr, s, _, events = manager(backup=backup)
        out = mgr.enable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.writes(), [])
        self.assertEqual(events[-1][0], "tweak_failed")

    def test_backup_save_failure_blocks_enable(self):
        backup = MemoryBackup()
        backup.fail_save = True
        mgr, s, _, _ = manager(backup=backup)
        out = mgr.enable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.writes(), [])

    def test_apply_error_rolls_back_and_drops_the_fresh_backup(self):
        s = FakeSystem()
        before = s.snapshot()
        s.fail_after_writes = 0  # the apply write fails...
        mgr, _, backup, events = manager(s)
        # ...but let the rollback write through
        real_w = s._w

        def w(name, *args):
            s.fail_after_writes = None if s.writes() else 0
            return real_w(name, *args)
        s._w = w
        out = mgr.enable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertIn("đã hoàn tác", vi(out.message))
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(backup.data, {})
        self.assertEqual(events[-1][0], "tweak_failed")

    def test_apply_without_effect_is_rolled_back(self):
        s = FakeSystem()
        s.noop.add("adapter_property_set")   # driver ignores the write
        mgr, _, backup, _ = manager(s)
        out = mgr.enable("wifi_power_saving")
        self.assertFalse(out.ok)
        self.assertIn("chưa bật", vi(out.message))
        self.assertEqual(backup.data, {})

    def test_failed_rollback_keeps_backup_and_is_bad(self):
        s = FakeSystem()
        # apply writes AC/DC then fails on setactive-equivalent: simulate a half-applied change
        real_set = s.power_set

        def half_apply(sub, setting, ac, dc):
            s.power[(sub, setting)] = (ac, s.power[(sub, setting)][1])  # AC changed, DC not
            raise SystemWriteError("powercfg died mid-way")
        s.power_set = half_apply
        mgr, _, backup, events = manager(s)
        out = mgr.enable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertTrue(out.changed)
        self.assertIn("HOÀN TÁC THẤT BẠI", vi(out.message))
        self.assertIn("power_pcie_aspm_off", backup.data)     # so the user can still disable
        self.assertEqual(events[-1][2], "bad")
        s.power_set = real_set
        self.assertTrue(mgr.disable("power_pcie_aspm_off").ok)  # and disabling now recovers
        self.assertEqual(s.power[(SUB_PCIE, SET_ASPM)], (1, 2))

    def test_existing_backup_is_never_overwritten(self):
        mgr, s, backup, _ = manager()
        mgr.enable("wifi_power_saving")                 # original "2" (Auto)
        s.adapter_property_set("Wi-Fi", "PowerSaveMode", "1")  # someone sets "Enabled" by hand
        mgr.enable("wifi_power_saving")
        self.assertEqual(backup.data["wifi_power_saving"]["original"]["value"], "2")


class DisableTests(unittest.TestCase):
    def test_round_trip_restores_every_kind_exactly(self):
        mgr, s, backup, _ = manager()
        before = s.snapshot()
        for t in all_tweaks():
            self.assertTrue(mgr.enable(t.id).ok, t.id)
        self.assertNotEqual(s.snapshot(), before)
        for t in all_tweaks():
            out = mgr.disable(t.id)
            self.assertTrue(out.ok, (t.id, out.message))
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(backup.data, {})

    def test_absent_registry_value_is_deleted_again(self):
        mgr, s, backup, _ = manager()
        mgr.enable("tcp_timedwait")
        self.assertEqual(s.registry[(TCPIP, "TcpTimedWaitDelay")], 30)
        self.assertIsNone(backup.data["tcp_timedwait"]["original"]["value"])
        mgr.disable("tcp_timedwait")
        self.assertNotIn((TCPIP, "TcpTimedWaitDelay"), s.registry)

    def test_restore_mismatch_keeps_backup(self):
        mgr, s, backup, events = manager()
        mgr.enable("power_pcie_aspm_off")
        s.noop.add("power_set")
        out = mgr.disable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertIn("không khớp", vi(out.message))
        self.assertIn("power_pcie_aspm_off", backup.data)
        self.assertEqual(events[-1][2], "bad")

    def test_restore_error_keeps_backup(self):
        mgr, s, backup, _ = manager()
        mgr.enable("wifi_wake_magic")
        s.fail.add("adapter_property_set")
        self.assertFalse(mgr.disable("wifi_wake_magic").ok)
        self.assertIn("wifi_wake_magic", backup.data)

    def test_without_backup_uses_driver_default(self):
        mgr, s, backup, events = manager()
        s.adapter_property_set("Wi-Fi", "PowerSaveMode", "0")  # enabled before the tool existed
        s.log.clear()
        out = mgr.disable("wifi_power_saving")
        self.assertTrue(out.ok)
        self.assertEqual(s.writes()[0][1], "adapter_property_reset")
        self.assertEqual(s._prop("Wi-Fi", "PowerSaveMode")["RegistryValue"], ["2"])
        self.assertIn("mặc định", vi(events[-1][1]))

    def test_without_backup_power_is_refused_and_untouched(self):
        mgr, s, _, _ = manager()
        s.power[(SUB_PCIE, SET_ASPM)] = (0, 0)
        s.log.clear()
        out = mgr.disable("power_pcie_aspm_off")
        self.assertFalse(out.ok)
        self.assertIn("giữ nguyên", vi(out.message))
        self.assertEqual(s.writes(), [])

    def test_already_off_is_noop(self):
        mgr, s, _, events = manager()
        out = mgr.disable("power_pcie_aspm_off")
        self.assertTrue(out.ok)
        self.assertFalse(out.changed)
        self.assertEqual((s.writes(), events), ([], []))

    def test_restores_original_even_if_someone_already_turned_it_off_by_hand(self):
        mgr, s, backup, _ = manager()
        mgr.enable("power_pcie_aspm_off")                     # original (1, 2)
        s.power[(SUB_PCIE, SET_ASPM)] = (2, 2)                # changed by hand afterwards
        self.assertTrue(mgr.disable("power_pcie_aspm_off").ok)
        self.assertEqual(s.power[(SUB_PCIE, SET_ASPM)], (1, 2))


class AdoptTests(unittest.TestCase):
    def test_exp001_manual_backup_scenario(self):
        # The dev PC: tweaks applied by hand, originals only in data/manual/backup-*.json.
        s = FakeSystem()
        s.adapter_property_set("Wi-Fi", "PowerSaveMode", "0")
        s.power[(SUB_PCIE, SET_ASPM)] = (0, 0)
        s.registry[(CLASS_KEY, "PnPCapabilities")] = 24
        mgr, _, backup, _ = manager(s)
        ps = mgr.get("wifi_power_saving")
        self.assertTrue(mgr.adopt_backup("wifi_power_saving", ps.original_from_display(s, "Auto"), "EXP-001").ok)
        self.assertTrue(mgr.adopt_backup("power_pcie_aspm_off",
                                         {"subgroup": SUB_PCIE, "setting": SET_ASPM, "ac": 1, "dc": 2}, "EXP-001").ok)
        self.assertTrue(mgr.adopt_backup("device_power_off",
                                         {"path": CLASS_KEY, "name": "PnPCapabilities", "value": 16}, "EXP-001").ok)
        s.log.clear()
        for tid in ("wifi_power_saving", "power_pcie_aspm_off", "device_power_off"):
            self.assertTrue(mgr.disable(tid).ok, tid)
        self.assertEqual(s._prop("Wi-Fi", "PowerSaveMode")["DisplayValue"], "Auto")
        self.assertEqual(s.power[(SUB_PCIE, SET_ASPM)], (1, 2))
        self.assertEqual(s.registry[(CLASS_KEY, "PnPCapabilities")], 16)

    def test_adopt_refuses_when_tweak_is_off(self):
        # Real case on the dev PC: most EXP-001 values had silently gone back to their originals.
        mgr, s, backup, _ = manager()
        out = mgr.adopt_backup("power_pcie_aspm_off", {"subgroup": SUB_PCIE, "setting": SET_ASPM, "ac": 3, "dc": 3},
                               "EXP-001")
        self.assertFalse(out.ok)
        self.assertIn("đang tắt", vi(out.message))
        self.assertEqual(backup.data, {})

    def test_adopt_changes_nothing_and_never_overwrites(self):
        mgr, s, backup, _ = manager()
        s.power[(SUB_PCIE, SET_ASPM)] = (0, 0)
        orig = {"subgroup": SUB_PCIE, "setting": SET_ASPM, "ac": 1, "dc": 2}
        self.assertTrue(mgr.adopt_backup("power_pcie_aspm_off", orig, "manual").ok)
        self.assertFalse(mgr.adopt_backup("power_pcie_aspm_off", dict(orig, ac=3), "manual").ok)
        self.assertEqual(backup.data["power_pcie_aspm_off"]["original"]["ac"], 1)
        self.assertEqual([w for w in s.writes() if w[1] != "power_set"], [])
        self.assertEqual(len(s.writes()), 0)

    def test_adopt_rejects_wrong_shape(self):
        mgr, *_ = manager()
        self.assertFalse(mgr.adopt_backup("power_pcie_aspm_off", {"ac": 1}, "manual").ok)


class StatesTests(unittest.TestCase):
    def test_states_never_write_and_share_reads(self):
        mgr, s, _, _ = manager()
        states = mgr.states()
        self.assertEqual(len(states), 6)
        self.assertEqual(s.writes(), [])
        reads = [name for kind, name, _ in s.log if kind == "read"]
        self.assertEqual(reads.count("adapter_properties"), 1)  # two adapter tweaks, one read
        self.assertEqual(reads.count("wifi_adapter_name"), 1)

    def test_read_error_is_reported_as_error_not_unsupported(self):
        s = FakeSystem()

        def boom(*a):
            raise RuntimeError("powercfg missing")
        s.power_get = boom
        mgr, *_ = manager(s)
        st = {x.id: x for x in mgr.states()}["power_pcie_aspm_off"]
        self.assertFalse(st.supported)
        self.assertIn("powercfg missing", st.error)

    def test_cached_reads_refuse_writes(self):
        cached = tweaks._CachedReads(FakeSystem())
        with self.assertRaises(AttributeError):
            cached.power_set
        self.assertEqual(cached.power_get(SUB_PCIE, SET_ASPM), (1, 2))

    def test_has_backup_unknown_when_backup_corrupt(self):
        backup = MemoryBackup()
        backup.fail_load = True
        mgr, *_ = manager(backup=backup)
        self.assertIsNone(mgr.states()[0].has_backup)

    def test_plan_is_read_only(self):
        mgr, s, _, _ = manager()
        text = mgr.plan("wifi_power_saving", True, "vi")
        self.assertIn("khởi động lại", text)
        self.assertIn("sẽ:", text)
        self.assertIn("không có mặc định an toàn", self._plan_disable_power_without_backup(mgr, s))
        self.assertEqual(s.writes(), [])

    @staticmethod
    def _plan_disable_power_without_backup(mgr, s):
        s.power[(SUB_PCIE, SET_ASPM)] = (0, 0)
        return mgr.plan("power_pcie_aspm_off", False, "vi")

    def test_unknown_id(self):
        mgr, *_ = manager()
        with self.assertRaises(KeyError):
            mgr.enable("nope")


class PersistenceTests(unittest.TestCase):
    """backup.json is the real file here: a new manager (= tool restarted) must restore it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("STABLEINTERNET_DATA")
        os.environ["STABLEINTERNET_DATA"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STABLEINTERNET_DATA", None)
        else:
            os.environ["STABLEINTERNET_DATA"] = self.old
        self.tmp.cleanup()

    def test_backup_survives_restart(self):
        s = FakeSystem()
        first = TweakManager(s, all_tweaks(), is_admin=lambda: True)
        self.assertTrue(first.enable("device_power_off").ok)
        self.assertEqual(config.load_backup()["device_power_off"]["original"]["value"], 16)
        second = TweakManager(s, all_tweaks(), is_admin=lambda: True)   # fresh process
        self.assertTrue(second.disable("device_power_off").ok)
        self.assertEqual(s.registry[(CLASS_KEY, "PnPCapabilities")], 16)
        self.assertEqual(config.load_backup(), {})

    def test_corrupt_file_blocks_writes(self):
        config.backup_path().write_text("{oops", encoding="utf-8")
        s = FakeSystem()
        out = TweakManager(s, all_tweaks(), is_admin=lambda: True).enable("device_power_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.writes(), [])


class StackSwitchTests(unittest.TestCase):
    """SIC-88: experimental network-stack switches (TCP ECN, RSC, packet coalescing)."""

    @staticmethod
    def stack():
        return [TcpEcnTweak("tcp_ecn", "ECN", "experimental"), RscOffTweak("rsc_off", "RSC", "experimental"),
                PacketCoalescingOffTweak("packet_coalescing_off", "Coalescing", "experimental")]

    def mgr(self, system=None, backup=None):
        return manager(system, backup, tweak_list=self.stack())

    def test_ecn_round_trip_restores_the_captured_value(self):
        mgr, s, backup, _ = self.mgr()
        before = s.snapshot()
        self.assertTrue(mgr.enable("tcp_ecn").ok)
        self.assertEqual(s.ecn, "enabled")
        self.assertEqual(backup.data["tcp_ecn"]["original"], {"value": "disabled"})
        self.assertTrue(mgr.disable("tcp_ecn").ok)
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(backup.data, {})

    def test_ecn_without_backup_goes_back_to_default(self):
        mgr, s, _, _ = self.mgr()
        s.ecn = "enabled"
        self.assertTrue(mgr.state("tcp_ecn").can_restore_without_backup)
        self.assertTrue(mgr.disable("tcp_ecn").ok)
        self.assertEqual(s.ecn, "default")
        self.assertEqual([w[1:] for w in s.writes()], [("tcp_ecn_set", ("default",))])

    def test_rsc_disables_only_the_enabled_families_and_restores_the_flags(self):
        mgr, s, backup, _ = self.mgr()
        s.rsc["Wi-Fi"]["ipv6"] = False                      # a card with IPv6 RSC already off
        before = s.snapshot()
        self.assertTrue(mgr.enable("rsc_off").ok)
        self.assertEqual(s.rsc["Wi-Fi"]["ipv4"], False)
        self.assertEqual([w[1:] for w in s.writes()], [("rsc_set", ("Wi-Fi", False, None))])
        self.assertEqual(backup.data["rsc_off"]["original"], {"adapter": "Wi-Fi", "ipv4": True, "ipv6": False})
        self.assertTrue(mgr.disable("rsc_off").ok)
        self.assertEqual(s.snapshot(), before)             # IPv6 was off before and stays off
        self.assertTrue(mgr.state("rsc_off").disrupts_network)

    def test_rsc_without_backup_enables_what_the_card_supports(self):
        mgr, s, _, _ = self.mgr()
        s.rsc["Wi-Fi"].update(ipv4=False, ipv6=False, ipv6_supported=False)
        self.assertTrue(mgr.state("rsc_off").enabled)
        out = mgr.disable("rsc_off")
        self.assertTrue(out.ok, out.message)
        self.assertEqual(s.rsc["Wi-Fi"]["ipv4"], True)
        self.assertEqual(s.rsc["Wi-Fi"]["ipv6"], False)    # never asked the card for IPv6 RSC it lacks
        self.assertEqual([w[1:] for w in s.writes()], [("rsc_set", ("Wi-Fi", True, None))])

    def test_rsc_unsupported_is_not_an_error(self):
        mgr, s, _, _ = self.mgr()
        s.rsc.clear()                                      # no RSC object for the card
        st = mgr.state("rsc_off")
        self.assertEqual((st.supported, st.enabled, st.error), (False, False, None))
        self.assertIn("Receive Segment Coalescing", vi(st.reason))
        s.rsc["Wi-Fi"] = {"ipv4": False, "ipv6": False, "ipv4_supported": False, "ipv6_supported": False}
        st = mgr.state("rsc_off")                          # the dev PC's Wi-Fi card: RSC exists but is unsupported
        self.assertEqual((st.supported, st.error), (False, None))
        out = mgr.enable("rsc_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.writes(), [])
        s.adapter = None
        self.assertFalse(mgr.state("rsc_off").supported)

    def test_packet_coalescing_round_trip_with_backup(self):
        mgr, s, backup, _ = self.mgr()
        s.coalescing = "Default"
        self.assertTrue(mgr.enable("packet_coalescing_off").ok)
        self.assertEqual(s.coalescing, "Disabled")
        self.assertTrue(mgr.disable("packet_coalescing_off").ok)
        self.assertEqual(s.coalescing, "Default")

    def test_packet_coalescing_has_no_default_restore(self):
        mgr, s, _, _ = self.mgr()
        s.coalescing = "Disabled"                          # set by hand, no backup
        self.assertFalse(mgr.state("packet_coalescing_off").can_restore_without_backup)
        out = mgr.disable("packet_coalescing_off")
        self.assertFalse(out.ok)
        self.assertEqual(s.writes(), [])
        self.assertEqual(s.coalescing, "Disabled")
        with self.assertRaises(NoDefaultRestore):
            PacketCoalescingOffTweak("x_off", "x", "experimental").restore(s, None)

    def test_a_write_that_has_no_effect_rolls_back(self):
        mgr, s, backup, _ = self.mgr()
        s.noop.add("tcp_ecn_set")
        out = mgr.enable("tcp_ecn")
        self.assertFalse(out.ok)
        self.assertEqual(s.ecn, "disabled")
        self.assertEqual(backup.data, {})


class CatalogTests(unittest.TestCase):
    def test_list_states_is_none_while_catalog_is_empty(self):
        if tweaks.CATALOG:
            self.skipTest("catalog has entries")
        self.assertIsNone(tweaks.list_states())

    def test_cli_list_and_dry_run_never_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["STABLEINTERNET_DATA"] = tmp
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = tweaks.main(["list"])
                self.assertEqual(code, 0)
            finally:
                os.environ.pop("STABLEINTERNET_DATA", None)


if __name__ == "__main__":
    unittest.main()
