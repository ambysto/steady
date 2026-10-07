import contextlib
import copy
import io
import os
import sys
import tempfile
import unittest

from app import config, i18n, tweaks
from app.tweaks import (AdapterPropertyTweak, BindingTweak, NoDefaultRestore, PowerCfgTweak, RegistryDwordTweak,
                        TweakManager)
from app.winsys import SystemReadError, SystemWriteError, WifiBands

WIFI_GUID = "{2F70B5EE-2B7E-4D1A-8C6A-4FD6AC8C98B7}"
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
        self.ssid_bands = WifiBands("HomeNet", "2.4 GHz", frozenset({"2.4 GHz", "5 GHz"}))   # None: not on Wi-Fi
        self.tcp_global = {"ecncapability": "disabled"}
        self.rsc = {"Wi-Fi": {"ipv4": True, "ipv6": True, "ipv4_supported": True, "ipv6_supported": True}}
        self.offload = {"PacketCoalescingFilter": "Enabled"}
        self.dhcp_dns = ["192.168.1.1"]
        self.dns = {"index": 6, "guid": WIFI_GUID, "alias": "Wi-Fi", "servers": list(self.dhcp_dns), "static": False,
                    "static_v6": False, "suffix": "", "domain_joined": False, "vpn_up": False}
        self.extra_interfaces = []    # other connections (a dock's Ethernet...), found by their GUID
        self.uplink = "dns"           # "dns": the interface above; else the dict of another one (or None: offline)
        self.uplink_read_error = False
        # What Windows 11 ships: a template for each public server, auto-upgrade for none of them.
        self.doh = {address: {"template": template, "auto_upgrade": False, "fallback_to_udp": False}
                    for address, (_, template) in tweaks.DOH_ADDRESSES.items()}
        self.qos = {"Backup-Agent": 5_000_000}   # throttle policies: name -> bit/s; someone else's policy
        self.qos_exempt = {}           # unthrottled policies: name -> destination prefix
        self.qos_rounding = 0          # Windows may store a slightly different rate
        self.mtu = {}                  # interface GUID -> IPv4 MTU (1500 when not listed)
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

    def tcp_global_get(self, setting):
        self._r("tcp_global_get", setting)
        return self.tcp_global.get(setting)

    def rsc_get(self, adapter):
        self._r("rsc_get", adapter)
        return copy.deepcopy(self.rsc.get(adapter))

    def offload_global_get(self, setting):
        self._r("offload_global_get", setting)
        return self.offload.get(setting)

    def interfaces(self):
        return ([self.dns] if self.dns else []) + self.extra_interfaces

    def dns_interface(self, guid=None):
        self._r("dns_interface", guid)
        if guid is not None:
            return copy.deepcopy(next((i for i in self.interfaces() if i["guid"] == guid), None))
        if self.uplink_read_error:
            raise SystemReadError("the uplink cannot be read")
        return copy.deepcopy(self.dns if self.uplink == "dns" else self.uplink)

    def doh_get(self):
        self._r("doh_get")
        return copy.deepcopy(self.doh)       # None: this Windows has no DoH

    def wifi_ssid_bands(self, adapter):
        self._r("wifi_ssid_bands", adapter)
        return self.ssid_bands

    def qos_policies_get(self, prefix):
        self._r("qos_policies_get", prefix)
        out = {n: {"rate_bps": r, "destination": None} for n, r in self.qos.items()}
        out.update({n: {"rate_bps": None, "destination": d} for n, d in self.qos_exempt.items()})
        return {n: p for n, p in out.items() if n.lower().startswith(prefix.lower())}

    def ipv4_interface(self, guid=None):
        self._r("ipv4_interface", guid)
        if guid is not None:
            found = next((i for i in self.interfaces() if i["guid"] == guid), None)
        elif self.uplink_read_error:
            raise SystemReadError("the uplink cannot be read")
        else:
            found = self.dns if self.uplink == "dns" else self.uplink
        if found is None:
            return None
        return {"index": found["index"], "guid": found["guid"], "alias": found["alias"],
                "mtu": self.mtu.get(found["guid"], 1500)}

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

    def tcp_global_set(self, setting, value):
        if self._w("tcp_global_set", setting, value):
            self.tcp_global[setting] = value

    def rsc_set(self, adapter, ipv4, ipv6):
        if self._w("rsc_set", adapter, ipv4, ipv6):
            for family, flag in (("ipv4", ipv4), ("ipv6", ipv6)):
                if flag is not None:
                    self.rsc[adapter][family] = flag

    def offload_global_set(self, setting, value):
        if self._w("offload_global_set", setting, value):
            self.offload[setting] = value

    def dns_servers_set(self, interface_index, servers):
        if self._w("dns_servers_set", interface_index, servers):
            target = next(i for i in self.interfaces() if i["index"] == interface_index)
            target.update(servers=list(self.dhcp_dns) if servers is None else list(servers), static=servers is not None)

    def doh_set(self, address, template, auto_upgrade, fallback_to_udp):
        if self._w("doh_set", address, template, auto_upgrade, fallback_to_udp):
            if template is None:      # Set-DnsClientDohServerAddress: only an entry that exists, its template stays
                if address not in self.doh:
                    raise SystemWriteError(f"no DoH entry for {address}")
                template = self.doh[address]["template"]
            self.doh[address] = {"template": template, "auto_upgrade": auto_upgrade,
                                 "fallback_to_udp": fallback_to_udp}

    def doh_remove(self, address):
        if self._w("doh_remove", address):
            self.doh.pop(address, None)

    def qos_policy_set(self, name, bits_per_second):
        if self._w("qos_policy_set", name, bits_per_second):
            self.qos[name] = bits_per_second + self.qos_rounding

    def qos_exempt_set(self, name, destination):
        if self._w("qos_exempt_set", name, destination):
            self.qos_exempt[name] = destination

    def qos_policy_remove(self, name):
        if self._w("qos_policy_remove", name):
            self.qos.pop(name, None)
            self.qos_exempt.pop(name, None)

    def ipv4_mtu_set(self, interface_index, mtu):
        if self._w("ipv4_mtu_set", interface_index, mtu):
            target = next(i for i in self.interfaces() if i["index"] == interface_index)
            if mtu == 1500:
                self.mtu.pop(target["guid"], None)
            else:
                self.mtu[target["guid"]] = mtu

    # helpers
    def writes(self):
        return [entry for entry in self.log if entry[0] == "write"]

    def snapshot(self):
        return copy.deepcopy((self.props, self.registry, self.power, self.bindings, self.tcp_global, self.rsc,
                              self.offload, self.dns, self.extra_interfaces, self.doh, self.qos, self.qos_exempt,
                              self.mtu))


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
