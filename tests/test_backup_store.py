"""Backups live in HKLM where only the elevated side can write (ADR-0018): a tampered backup.json no longer
changes what the elevated helper restores. winreg is a fake; the real registry is never touched."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import backupstore, config, elevated, failover, i18n, tweaks, winutil
from app.tweaks import Reading, TweakManager
from tests.test_backup_trust import catalog
from tests.test_failover import FakeSystem as MetricSystem
from tests.test_tweaks import WIFI_GUID, FakeSystem

DOH_FLAGS = {"auto_upgrade": False, "fallback_to_udp": False}
ECN_OFF = {"original": {"setting": "ecncapability", "value": "disabled"}}
DOCK_GUID = "{00000000-0000-4000-8000-0000000000BB}"


class FakeWinreg:
    """The winreg calls the store makes, on an in-memory HKLM. Without Admin every write is refused, as
    HKLM\\SOFTWARE's ACL does (BUILTIN\\Users: ReadKey)."""
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ, KEY_WRITE, KEY_WOW64_64KEY = 0x20019, 0x20006, 0x0100
    REG_SZ, REG_DWORD = 1, 4

    class Handle:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self, admin=True):
        self.admin = admin
        self.keys = {}       # path -> {name: (value, kind)}
        self.views = []      # access flags of every open, to check the 64-bit view

    def _need_admin(self):
        if not self.admin:
            raise PermissionError(5, "Access is denied")

    def OpenKey(self, root, path, reserved=0, access=KEY_READ):
        assert root == self.HKEY_LOCAL_MACHINE
        self.views.append(access)
        if path not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        if access & 0x6:          # KEY_SET_VALUE | KEY_CREATE_SUB_KEY
            self._need_admin()
        return self.Handle(path)

    def CreateKeyEx(self, root, path, reserved=0, access=KEY_WRITE):
        self.views.append(access)
        self._need_admin()
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})
        return self.Handle(path)

    def QueryValueEx(self, handle, name):
        try:
            return self.keys[handle.path][name]
        except KeyError:
            raise FileNotFoundError(2, "The system cannot find the file specified") from None

    def SetValueEx(self, handle, name, reserved, kind, value):
        self._need_admin()
        self.keys[handle.path][name] = (value, kind)

    def DeleteValue(self, handle, name):
        self._need_admin()
        try:
            del self.keys[handle.path][name]
        except KeyError:
            raise FileNotFoundError(2, "The system cannot find the file specified") from None

    def value(self, name):
        return self.keys.get(backupstore.STORE_KEY, {}).get(name, (None,))[0]

    def backup(self):
        return json.loads(self.value(backupstore.BACKUP_VALUE))

    def quarantine(self):
        text = self.value(backupstore.QUARANTINE_VALUE)
        return json.loads(text) if text else {}


def dns_original(servers, static=True):
    return {"interface_index": 6, "static": static, "servers": servers,
            "doh": {a: {"present": True, "template": t, **DOH_FLAGS} for a, (_, t) in tweaks.DOH_ADDRESSES.items()}}


def dns_on(system):
    """dns_fastest is on: static servers from two providers, DoH auto-upgrade on for them."""
    system.dns.update(servers=["1.1.1.1", "8.8.8.8"], static=True)
    for address in ("1.1.1.1", "8.8.8.8"):
        system.doh[address]["auto_upgrade"] = True
    return system


class ElsewhereTweak:
    """A tweak whose own reading is off while its backup belongs to a subject it does not read (dns_fastest
    of a docked or VPN interface, mtu_pmtu): only backup_reading() says it is on."""
    id = "elsewhere"

    def __init__(self, on_there=True):
        self.on_there = on_there

    def check_original(self, system, original):
        if original != {"subject": "there"}:
            raise ValueError("not this tweak's")

    def read(self, system):
        return Reading(True, False, None)

    def backup_reading(self, system, original):
        return Reading(True, True, None) if self.on_there else None

    backup_in_effect = tweaks.Tweak.backup_in_effect     # the default: read(), else backup_reading()


class StoreCase(unittest.TestCase):
    """The registry backend (STABLEINTERNET_BACKUP unset), a fake HKLM, the old backup.json in a temp folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if k != backupstore.ENV_BACKEND}
        env["STABLEINTERNET_DATA"] = tmp.name
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(backupstore, "REGISTRY", None),
                        mock.patch.object(winutil, "is_admin", lambda: self.reg.admin)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reg = FakeWinreg()
        self.new_process()
        self.legacy = self.tmp / "backup.json"
        self.system = FakeSystem()
        self.metrics = MetricSystem({21: {"automatic": False, "metric": 5}})   # failover has switched to path 21
        self.check = backupstore.entry_check(self.system, catalog(), self.metrics)
        self.events = []

    def new_process(self):
        """The same HKLM seen by a new process (the quarantine is retried once per process)."""
        backupstore.REGISTRY = backupstore.RegistryStore(self.reg)

    def plant(self, data, path=None):
        """What a process of the user, without Admin, can do: write backup.json."""
        (path or self.legacy).write_text(json.dumps(data), encoding="utf-8")

    def store(self, data):
        backupstore.REGISTRY.write(data)
        backupstore.REGISTRY.mark_imported()

    def load(self):
        return backupstore.load(check=self.check)

    def manager(self, system=None):
        return TweakManager(system or self.system, catalog(), load_backup=self.load, save_backup=config.save_backup,
                            record_event=lambda k, m, lvl: self.events.append((k, m, lvl)),
                            is_admin=lambda: True, clock=lambda: 1000.0)


class StoreTests(StoreCase):
    def test_backups_are_one_json_value_in_hklm_in_the_64_bit_view(self):
        config.put_backup_entry("tcp_ecn", ECN_OFF)
        self.assertEqual(self.reg.backup(), {"tcp_ecn": ECN_OFF})
        self.assertEqual(self.reg.keys[backupstore.STORE_KEY][backupstore.BACKUP_VALUE][1], self.reg.REG_SZ)
        self.assertTrue(all(access & self.reg.KEY_WOW64_64KEY for access in self.reg.views))
        self.assertFalse(self.legacy.exists())

    def test_without_admin_nothing_can_be_written(self):
        self.store({"tcp_ecn": ECN_OFF})
        self.reg.admin = False
        with self.assertRaises(PermissionError):
            config.put_backup_entry("tcp_ecn", {"original": {"setting": "ecncapability", "value": "enabled"}})
        with self.assertRaises(PermissionError):
            config.save_backup({})
        self.assertEqual(self.reg.backup(), {"tcp_ecn": ECN_OFF})

    def test_an_unelevated_reader_sees_the_store(self):
        self.store({"tcp_ecn": ECN_OFF})
        self.reg.admin = False
        self.new_process()
        self.assertEqual(list(config.load_backup()), ["tcp_ecn"])
        mgr = self.manager()
        mgr._load = config.load_backup
        self.assertTrue(mgr.state("tcp_ecn").has_backup)
        self.assertFalse(mgr.state("rsc_off").has_backup)

    def test_no_key_means_no_backup(self):
        self.reg.admin = False
        self.assertEqual(config.load_backup(), {})
        self.assertFalse(backupstore.has_data())

    def test_a_corrupt_value_is_an_error_not_an_empty_backup(self):
        self.store({})
        self.reg.keys[backupstore.STORE_KEY][backupstore.BACKUP_VALUE] = ("[1, 2]", self.reg.REG_SZ)
        with self.assertRaises(ValueError):
            config.load_backup()
        self.assertIsNone(self.manager().state("tcp_ecn").has_backup)

    def test_the_lock_is_reentrant_so_an_import_can_run_inside_a_write(self):
        self.plant({"tcp_ecn": ECN_OFF})
        self.system.tcp_global["ecncapability"] = "enabled"
        with config.backup_lock():
            config.put_backup_entry("failover:21", {"original": {"automatic": True}},
                                    load=self.load, save=config.save_backup)
        self.assertEqual(set(self.reg.backup()), {"tcp_ecn", "failover:21"})

    def test_the_file_backend_is_ignored_in_the_packaged_build(self):
        with mock.patch.dict(os.environ, {backupstore.ENV_BACKEND: "file"}):
            self.assertTrue(backupstore.file_backend())
            with mock.patch("sys.frozen", True, create=True):
                self.assertFalse(backupstore.file_backend())

    def test_an_elevated_packaged_process_ignores_the_folder_overrides(self):
        """HKCU\\Environment reaches the elevated helper: it must not read or write where the user points it."""
        other = self.tmp / "elsewhere"
        with mock.patch.dict(os.environ, {"STABLEINTERNET_DATA": str(other), "STABLEINTERNET_USERDIR": str(other),
                                          "LOCALAPPDATA": str(self.tmp / "local")}), \
                mock.patch("sys.frozen", True, create=True):
            self.reg.admin = False
            self.assertEqual(config.data_dir(), other)          # the build's smoke test runs unelevated
            self.reg.admin = True
            self.assertEqual(config.user_dir(), self.tmp / "local" / "StableInternet")
            self.assertEqual(config.data_dir(), self.tmp / "local" / "StableInternet" / "data")


class ImportTests(StoreCase):
    def test_entries_that_would_be_restored_are_imported_once(self):
        dns_on(self.system)
        self.system.tcp_global["ecncapability"] = "enabled"
        good = {"dns_fastest": {"original": dns_original(["192.0.2.53"]), "captured_at": 1},
                "tcp_ecn": ECN_OFF,
                "failover:21": {"original": {"automatic": False, "metric": 25}, "home": 6}}
        self.plant(good)
        self.assertEqual(self.load(), good)
        self.assertEqual(self.reg.backup(), good)
        self.assertEqual(self.reg.value(backupstore.IMPORTED_VALUE), 1)
        self.assertEqual(json.loads(self.legacy.read_text(encoding="utf-8")), good)   # never moved by the elevated side

    def test_unknown_entries_are_dropped_refused_ones_wait_in_quarantine(self):
        dns_on(self.system)
        stale = {"tcp_ecn": ECN_OFF,                                                   # the tweak is off now
                 "tcp_timedwait": {"original": {"path": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
                                                "name": "TcpTimedWaitDelay", "value": 0}},   # ADR-0017 check fails
                 "failover:6": {"original": {"automatic": False, "metric": 0}}}            # metric out of range
        self.plant({**stale, "dns_fastest": {"captured_at": 1}, "failover:x": {"original": {"automatic": True}},
                    "anything_else": {"original": {}}})
        self.assertEqual(self.load(), {})
        self.assertEqual(self.reg.quarantine(), stale)

    def test_a_quarantined_entry_is_imported_once_it_passes(self):
        """A transient refusal (the tweak read as off, the Wi-Fi card switched off at the first elevated run)
        is not a loss: the next elevated process tries again."""
        self.plant({"tcp_ecn": ECN_OFF})
        self.assertEqual(self.load(), {})
        self.system.tcp_global["ecncapability"] = "enabled"
        self.assertEqual(self.load(), {})                     # once per process: no machine reads on every load
        self.new_process()
        self.assertEqual(self.load(), {"tcp_ecn": ECN_OFF})
        self.assertEqual(self.reg.quarantine(), {})

    def test_a_quarantined_entry_never_replaces_a_backup_taken_since(self):
        self.plant({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "enabled"}}})
        self.load()
        self.assertTrue(self.manager().enable("tcp_ecn").ok)     # captures "disabled" itself
        self.new_process()
        self.assertEqual(self.load()["tcp_ecn"]["original"]["value"], "disabled")
        self.assertEqual(self.reg.quarantine(), {})

    def test_a_backup_of_a_subject_the_tweak_does_not_read_is_imported(self):
        """mtu_pmtu and dns_fastest of another interface read "off": backup_reading() is what says they are on."""
        self.plant({"elsewhere": {"original": {"subject": "there"}}})
        self.assertEqual(backupstore.load(check=backupstore.entry_check(self.system, [ElsewhereTweak()])),
                         {"elsewhere": {"original": {"subject": "there"}}})

    def test_a_backup_whose_subject_is_gone_waits(self):
        self.plant({"elsewhere": {"original": {"subject": "there"}}})
        self.assertEqual(backupstore.load(check=backupstore.entry_check(self.system, [ElsewhereTweak(False)])), {})
        self.assertEqual(set(self.reg.quarantine()), {"elsewhere"})

    def test_a_check_that_cannot_read_the_machine_waits_too(self):
        def broken(key, entry):
            raise OSError("PowerShell timed out")
        self.plant({"tcp_ecn": ECN_OFF})
        self.assertEqual(backupstore.load(check=broken), {})
        self.assertEqual(self.reg.quarantine(), {"tcp_ecn": ECN_OFF})

    def test_an_entry_the_store_has_is_never_overwritten(self):
        dns_on(self.system)
        backupstore.REGISTRY.write({"dns_fastest": {"original": dns_original(["192.0.2.53"])}})
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"])}})
        self.load()
        self.assertEqual(self.reg.backup()["dns_fastest"]["original"]["servers"], ["192.0.2.53"])
        self.assertEqual(self.reg.quarantine(), {})

    def test_the_first_elevated_run_closes_the_import_even_without_a_backup_json(self):
        self.assertEqual(self.load(), {})
        self.assertEqual(self.reg.value(backupstore.IMPORTED_VALUE), 1)
        self.system.tcp_global["ecncapability"] = "enabled"
        self.plant({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "enabled"}}})
        self.new_process()
        self.assertEqual(self.load(), {})

    def test_backup_json_at_another_path_is_never_imported(self):
        """The import is per machine, not per path: pointing the data folder elsewhere (STABLEINTERNET_DATA, a
        junction in place of the folder) gives no second import."""
        self.load()
        other = self.tmp / "other"
        other.mkdir()
        self.system.tcp_global["ecncapability"] = "enabled"
        self.plant({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "enabled"}}}, other / "backup.json")
        with mock.patch.dict(os.environ, {"STABLEINTERNET_DATA": str(other)}):
            self.new_process()
            self.assertEqual(self.load(), {})

    def test_without_admin_nothing_is_imported_and_the_reader_sees_both(self):
        backupstore.REGISTRY.write({"tcp_ecn": ECN_OFF})
        self.plant({"rsc_off": {"original": {"adapter": "Wi-Fi", "ipv4": True, "ipv6": True}},
                    "tcp_ecn": {"original": {"setting": "ecncapability", "value": "enabled"}}})
        self.reg.admin = False
        self.assertEqual(self.load(), {"rsc_off": {"original": {"adapter": "Wi-Fi", "ipv4": True, "ipv6": True}},
                                       "tcp_ecn": ECN_OFF})
        self.assertIsNone(self.reg.value(backupstore.IMPORTED_VALUE))

    def test_after_the_import_an_unelevated_reader_sees_only_the_store(self):
        self.load()
        self.plant({"tcp_ecn": ECN_OFF})
        self.reg.admin = False
        self.new_process()
        self.assertEqual(self.load(), {})

    def test_a_corrupt_backup_json_imports_nothing_and_closes_the_import(self):
        """Left open, it would fail every elevated operation and keep the window open for a later file."""
        for text in ("{not json", "[1, 2]"):
            with self.subTest(text=text):
                self.reg.keys.clear()
                self.new_process()
                self.legacy.write_text(text, encoding="utf-8")
                self.assertEqual(self.load(), {})
                self.assertEqual(self.reg.value(backupstore.IMPORTED_VALUE), 1)
                self.assertEqual(self.legacy.read_text(encoding="utf-8"), text)

    def test_every_elevated_operation_closes_the_import(self):
        """Even one that needs no backup (restart-adapter): the window ends at the first UAC prompt."""
        self.plant({})
        with mock.patch.object(backupstore, "default_check", self.check):
            elevated._close_backup_import()
        self.assertEqual(self.reg.value(backupstore.IMPORTED_VALUE), 1)


class RealBackupImportTests(StoreCase):
    """Backups an older version really wrote are imported, kind by kind (the ADR-0017 checks of #31 decide)."""

    VALUES = {"upload_shaping": 20_000_000, "mtu_pmtu": 1400}

    def turn_on(self, t, s):
        """The machine as the older version left it: original captured, then the tweak applied."""
        original = t.capture(s)
        if t.id in self.VALUES:
            t.apply_value(s, self.VALUES[t.id])
        else:
            t.apply(s)
        return original

    def imported(self, t, s, entry):
        self.plant({t.id: entry})
        self.new_process()
        return backupstore.load(check=backupstore.entry_check(s, [t]))

    def test_every_catalog_tweak_that_is_on_is_imported(self):
        for t in catalog():
            if t.id == "dns_fastest":
                continue   # needs a benchmark; covered by its own tests
            with self.subTest(t.id):
                self.reg.keys.clear()
                s = FakeSystem()
                try:
                    if not t.read(s).supported:
                        continue
                    original = self.turn_on(t, s)
                except (KeyError, tweaks.Unsupported):
                    continue     # FakeSystem lacks this setting
                entry = {"original": original, "captured_at": 1}
                self.assertEqual(self.imported(t, s, entry), {t.id: entry}, self.reg.quarantine())

    def test_an_upload_limit_with_its_lan_exemptions_is_imported(self):
        t = next(t for t in catalog() if t.id == "upload_shaping")
        s = FakeSystem()
        original = self.turn_on(t, s)
        for exempt in ([], sorted(t.exemptions)[:1], sorted(t.exemptions)):
            with self.subTest(exempt=exempt):
                self.reg.keys.clear()
                entry = {"original": {**original, "exempt": exempt}, "measurement": {"value": 20_000_000}}
                self.assertEqual(self.imported(t, s, entry), {"upload_shaping": entry})
        self.reg.keys.clear()
        legacy = {k: v for k, v in original.items() if k != "exempt"}       # before the LAN exemptions
        self.assertEqual(self.imported(t, s, {"original": legacy}), {"upload_shaping": {"original": legacy}})

    def test_an_mtu_backup_is_imported_although_read_says_off(self):
        t = next(t for t in catalog() if t.id == "mtu_pmtu")
        s = FakeSystem()
        original = self.turn_on(t, s)
        self.assertFalse(t.read(s).enabled)
        self.assertEqual(self.imported(t, s, {"original": original}), {"mtu_pmtu": {"original": original}})

    def test_an_mtu_backup_whose_mtu_is_back_waits(self):
        t = next(t for t in catalog() if t.id == "mtu_pmtu")
        s = FakeSystem()
        original = t.capture(s)                         # never applied: nothing to restore
        self.assertEqual(self.imported(t, s, {"original": original}), {})
        self.assertEqual(set(self.reg.quarantine()), {"mtu_pmtu"})

    def test_a_wifi_property_keyword_must_be_the_resolved_one_exactly(self):
        t = next(t for t in catalog() if t.id == "wifi_power_saving")
        s = FakeSystem()
        original = self.turn_on(t, s)
        self.assertEqual(self.imported(t, s, {"original": original}), {"wifi_power_saving": {"original": original}})
        for keyword in (original["keyword"].lower(), "*" + original["keyword"], "*WakeOnMagicPacket"):
            with self.subTest(keyword=keyword):
                self.reg.keys.clear()
                self.assertEqual(self.imported(t, s, {"original": {**original, "keyword": keyword}}), {})
                self.assertEqual(set(self.reg.quarantine()), {"wifi_power_saving"})


class ForgedImportTests(StoreCase):
    """A backup.json planted before the first elevated run: an entry whose "in effect" only the forged backup
    itself creates is never imported (it waits in the quarantine, where nothing restores it)."""

    def dock(self, **dns):
        self.system.extra_interfaces.append({"index": 9, "guid": DOCK_GUID, "alias": "Ethernet",
                                             "servers": ["192.168.1.1"], "static": False, "static_v6": False,
                                             "suffix": "", "domain_joined": False, "vpn_up": False, **dns})

    def test_dns_for_another_interface_that_does_not_have_the_tweaks_dns(self):
        self.dock()
        forged = {"original": {**dns_original(["203.0.113.66"]), "guid": DOCK_GUID, "interface_index": 9}}
        self.plant({"dns_fastest": forged})
        self.assertEqual(self.load(), {})
        self.assertEqual(self.reg.quarantine(), {"dns_fastest": forged})

    def test_dns_for_another_interface_that_has_the_tweaks_dns_is_imported(self):
        """The docked case the review asked to keep: the tweak was turned on there, the PC is on Wi-Fi now."""
        self.dock(servers=["1.1.1.1", "8.8.8.8"], static=True)
        for address in ("1.1.1.1", "8.8.8.8"):
            self.system.doh[address]["auto_upgrade"] = True
        real = {"original": {**dns_original(["192.0.2.53"]), "guid": DOCK_GUID, "interface_index": 9}}
        self.plant({"dns_fastest": real})
        self.assertEqual(self.load(), {"dns_fastest": real})

    def test_an_mtu_above_the_interfaces_is_never_imported(self):
        """apply only ever lowers the MTU: a saved MTU below the current one cannot be an original."""
        forged = {"original": {"guid": WIFI_GUID, "interface_index": 6, "alias": "Wi-Fi", "mtu": 1280}}
        self.plant({"mtu_pmtu": forged})
        self.assertEqual(self.load(), {})
        self.assertEqual(self.reg.quarantine(), {"mtu_pmtu": forged})
        self.system.mtu[WIFI_GUID] = 1400                  # lowered: the saved 1500 is a plausible original
        real = {"original": {**forged["original"], "mtu": 1500}}
        self.reg.keys.clear()
        self.plant({"mtu_pmtu": real})
        self.new_process()
        self.assertEqual(self.load(), {"mtu_pmtu": real})

    def test_a_failover_metric_without_a_switch_in_effect(self):
        for now in ({"automatic": True, "metric": 35}, {"automatic": False, "metric": 1}):
            with self.subTest(now=now):
                self.reg.keys.clear()
                self.new_process()
                self.metrics.metrics[21] = now
                forged = {"original": {"automatic": False, "metric": 1}, "home": 6}
                self.plant({"failover:21": forged})
                self.assertEqual(self.load(), {})
                self.assertEqual(self.reg.quarantine(), {"failover:21": forged})

    def test_a_failover_metric_of_an_interface_that_is_gone_waits(self):
        self.plant({"failover:33": {"original": {"automatic": True}, "home": 6}})
        self.assertEqual(self.load(), {})
        self.assertEqual(set(self.reg.quarantine()), {"failover:33"})


class TamperedBackupJsonTests(StoreCase):
    """The task of ADR-0018: whatever a process without Admin writes to backup.json, the elevated helper
    restores the original it captured itself."""

    def test_a_forged_dns_server_list_no_longer_reaches_the_restore(self):
        dns_on(self.system)
        self.store({"dns_fastest": {"original": dns_original(["192.0.2.53"]), "captured_at": 1}})
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"]), "captured_at": 2}})
        out = self.manager().disable("dns_fastest")
        self.assertTrue(out.ok, i18n.render(out.message, "en"))
        self.assertIn(("write", "dns_servers_set", (6, ["192.0.2.53"])), self.system.writes())
        self.assertNotIn("203.0.113.66", json.dumps(self.system.writes()))
        self.assertNotIn("dns_fastest", self.reg.backup())   # restored, entry removed from the store

    def test_a_forged_dns_entry_planted_after_the_first_elevated_run_is_never_imported(self):
        """Even with dns_fastest reading "on" (DoH set up by hand) and no backup in the store."""
        self.load()
        dns_on(self.system)
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"])}})
        self.new_process()
        mgr = self.manager()
        self.assertFalse(mgr.state("dns_fastest").has_backup)
        mgr.disable("dns_fastest")
        self.assertNotIn("203.0.113.66", json.dumps(self.system.writes()))

    def test_a_forged_entry_for_a_tweak_that_is_off_is_never_restored(self):
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"])}})
        mgr = self.manager()
        self.assertFalse(mgr.state("dns_fastest").has_backup)
        out = mgr.disable("dns_fastest")
        self.assertNotIn("dns_servers_set", [name for _, name, _ in self.system.writes()])
        self.assertFalse(out.changed)

    def test_editing_backup_json_after_enable_changes_nothing(self):
        """Enable writes the capture to HKLM, not to backup.json: the file is no longer part of the flow."""
        self.system.tcp_global["ecncapability"] = "disabled"
        mgr = self.manager()
        self.assertTrue(mgr.enable("tcp_ecn").ok)
        self.assertFalse(self.legacy.exists())
        self.plant({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "enabled"}}})
        self.assertTrue(mgr.disable("tcp_ecn").ok)
        self.assertEqual(self.system.tcp_global["ecncapability"], "disabled")

    def test_a_forged_failover_metric_no_longer_reaches_the_restore(self):
        s = MetricSystem()
        self.store({"failover:21": {"original": {"automatic": True}, "home": 6}})
        self.plant({"failover:21": {"original": {"automatic": False, "metric": 1}, "home": 6}})
        switch = failover.MetricSwitch(s, load_backup=self.load, save_backup=config.save_backup)
        self.assertTrue(switch.restore(21).ok)
        self.assertEqual(s.calls, [(21, None)])          # automatic metric, as captured; never metric 1
        self.assertEqual(self.reg.backup(), {})


class UninstallTests(StoreCase):
    def restore_all(self, mgr):
        with mock.patch.object(tweaks, "default_manager", lambda storage=None: mgr), \
                mock.patch("app.storage.Storage"), \
                mock.patch("app.winsys.WindowsSystem", MetricSystem), \
                mock.patch.object(backupstore, "default_check", self.check):
            return elevated.restore_everything()

    def test_restore_all_removes_the_backups_and_keeps_the_import_mark(self):
        dns_on(self.system)
        self.store({"dns_fastest": {"original": dns_original(["192.0.2.53"])}})
        res = self.restore_all(self.manager())
        self.assertTrue(res["ok"], res)
        self.assertIn(("write", "dns_servers_set", (6, ["192.0.2.53"])), self.system.writes())
        self.assertEqual(self.reg.keys[backupstore.STORE_KEY], {backupstore.IMPORTED_VALUE: (1, self.reg.REG_DWORD)})
        self.assertFalse(backupstore.has_data())

    def test_a_reinstall_never_imports_a_backup_json_planted_meanwhile(self):
        self.store({})
        self.assertTrue(self.restore_all(self.manager())["ok"])
        dns_on(self.system)
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"])}})
        self.new_process()
        self.assertEqual(self.load(), {})

    def test_quarantined_entries_go_with_the_store(self):
        self.store({})
        backupstore.REGISTRY.write_quarantine({"tcp_ecn": ECN_OFF})
        self.assertTrue(backupstore.has_data())
        self.assertTrue(self.restore_all(self.manager())["ok"])
        self.assertFalse(backupstore.has_data())

    def test_a_backup_that_could_not_be_restored_keeps_the_store(self):
        self.store({"power_pcie_aspm_off": {"original": {"subgroup": tweaks.SUB_PCIE, "setting": tweaks.SET_ASPM,
                                                         "ac": 9, "dc": 9}}})   # outside the setting's range
        res = self.restore_all(self.manager())
        self.assertFalse(res["ok"])
        self.assertIn("power_pcie_aspm_off", self.reg.backup())

    def test_restore_all_imports_the_old_backup_json_first(self):
        dns_on(self.system)
        self.plant({"dns_fastest": {"original": dns_original(["192.0.2.53"])}})
        self.assertTrue(self.restore_all(self.manager())["ok"])
        self.assertIn(("write", "dns_servers_set", (6, ["192.0.2.53"])), self.system.writes())
        self.assertFalse(backupstore.has_data())

    def test_the_uninstaller_asks_for_the_store_even_without_backups(self):
        from app import installer
        self.store({})
        backupstore.REGISTRY.write_quarantine({"tcp_ecn": ECN_OFF})
        self.reg.admin = False
        ops = installer.Ops()
        self.assertFalse(ops.backups_left())
        self.assertTrue(ops.store_left())


if __name__ == "__main__":
    unittest.main()
