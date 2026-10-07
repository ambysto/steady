"""Backups live in HKLM where only the elevated side can write (ADR-0018): a tampered backup.json no longer
changes what the elevated helper restores. winreg is a fake; the real registry is never touched."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import backupstore, config, elevated, failover, i18n, tweaks, winutil
from tests.test_backup_trust import catalog
from tests.test_failover import FakeSystem as MetricSystem
from tests.test_tweaks import FakeSystem
from app.tweaks import TweakManager

DOH_FLAGS = {"auto_upgrade": False, "fallback_to_udp": False}


class FakeWinreg:
    """The winreg calls the store makes, on an in-memory HKLM. Without Admin every write is refused, as
    HKLM\\SOFTWARE's ACL does (BUILTIN\\Users: ReadKey)."""
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ, KEY_WRITE, KEY_WOW64_64KEY = 0x20019, 0x20006, 0x0100
    REG_SZ, REG_MULTI_SZ = 1, 7

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

    def DeleteKeyEx(self, root, path, access=KEY_WOW64_64KEY, reserved=0):
        self.views.append(access)
        self._need_admin()
        if path not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        if any(k.startswith(path + "\\") for k in self.keys):
            raise PermissionError(5, "Access is denied")     # what RegDeleteKeyEx says for a key with subkeys
        del self.keys[path]

    def backup(self):
        return json.loads(self.keys[backupstore.STORE_KEY][backupstore.BACKUP_VALUE][0])


def dns_original(servers, static=True):
    return {"interface_index": 6, "static": static, "servers": servers,
            "doh": {a: {"present": True, "template": t, **DOH_FLAGS} for a, (_, t) in tweaks.DOH_ADDRESSES.items()}}


def dns_on(system):
    """dns_fastest is on: static servers from two providers, DoH auto-upgrade on for them."""
    system.dns.update(servers=["1.1.1.1", "8.8.8.8"], static=True)
    for address in ("1.1.1.1", "8.8.8.8"):
        system.doh[address]["auto_upgrade"] = True
    return system


class StoreCase(unittest.TestCase):
    """The registry backend (STABLEINTERNET_BACKUP unset), a fake HKLM, the old backup.json in a temp folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {k: v for k, v in os.environ.items() if k != backupstore.ENV_BACKEND}
        env["STABLEINTERNET_DATA"] = tmp.name
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(backupstore, "REGISTRY", None),
                        mock.patch.object(winutil, "is_admin", lambda: self.reg.admin)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reg = FakeWinreg()
        backupstore.REGISTRY = backupstore.RegistryStore(self.reg)
        self.legacy = Path(tmp.name) / "backup.json"
        self.system = FakeSystem()
        self.check = backupstore.entry_check(self.system, catalog())
        self.events = []

    def plant(self, data):
        """What a process of the user, without Admin, can do: write backup.json."""
        self.legacy.write_text(json.dumps(data), encoding="utf-8")

    def store(self, data):
        self.reg.CreateKeyEx(self.reg.HKEY_LOCAL_MACHINE, backupstore.STORE_KEY)
        backupstore.REGISTRY.write(data)

    def load(self):
        return backupstore.load(check=self.check)

    def manager(self, system=None):
        return TweakManager(system or self.system, catalog(), load_backup=self.load, save_backup=config.save_backup,
                            record_event=lambda k, m, lvl: self.events.append((k, m, lvl)),
                            is_admin=lambda: True, clock=lambda: 1000.0)


class StoreTests(StoreCase):
    def test_backups_are_one_json_value_in_hklm_in_the_64_bit_view(self):
        config.put_backup_entry("tcp_ecn", {"original": {"setting": "ecncapability", "value": "disabled"}})
        self.assertEqual(self.reg.backup(), {"tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}}})
        self.assertEqual(self.reg.keys[backupstore.STORE_KEY][backupstore.BACKUP_VALUE][1], self.reg.REG_SZ)
        self.assertTrue(all(access & self.reg.KEY_WOW64_64KEY for access in self.reg.views))
        self.assertFalse(self.legacy.exists())

    def test_without_admin_nothing_can_be_written(self):
        self.store({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}}})
        self.reg.admin = False
        with self.assertRaises(PermissionError):
            config.put_backup_entry("tcp_ecn", {"original": {"setting": "ecncapability", "value": "enabled"}})
        with self.assertRaises(PermissionError):
            config.save_backup({})
        self.assertEqual(self.reg.backup()["tcp_ecn"]["original"]["value"], "disabled")

    def test_an_unelevated_reader_sees_the_store(self):
        self.store({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}}})
        self.reg.admin = False
        self.assertEqual(list(config.load_backup()), ["tcp_ecn"])
        mgr = self.manager()
        mgr._load = config.load_backup
        self.assertTrue(mgr.state("tcp_ecn").has_backup)
        self.assertFalse(mgr.state("rsc_off").has_backup)

    def test_no_key_means_no_backup(self):
        self.assertEqual(config.load_backup(), {})
        self.assertFalse(backupstore.exists())

    def test_a_corrupt_value_is_an_error_not_an_empty_backup(self):
        self.reg.CreateKeyEx(self.reg.HKEY_LOCAL_MACHINE, backupstore.STORE_KEY)
        self.reg.keys[backupstore.STORE_KEY][backupstore.BACKUP_VALUE] = ("[1, 2]", self.reg.REG_SZ)
        with self.assertRaises(ValueError):
            config.load_backup()
        self.assertIsNone(self.manager().state("tcp_ecn").has_backup)

    def test_the_lock_is_reentrant_so_an_import_can_run_inside_a_write(self):
        self.plant({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}}})
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


class ImportTests(StoreCase):
    def test_entries_that_would_be_restored_are_imported_once(self):
        dns_on(self.system)
        self.system.tcp_global["ecncapability"] = "enabled"
        good = {"dns_fastest": {"original": dns_original(["192.0.2.53"]), "captured_at": 1},
                "tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}},
                "failover:21": {"original": {"automatic": False, "metric": 25}, "home": 6}}
        self.plant(good)
        self.assertEqual(self.load(), good)
        self.assertEqual(self.reg.backup(), good)
        self.assertFalse(self.legacy.exists())
        self.assertTrue(self.legacy.with_name("backup.json.imported").is_file())
        self.assertEqual(backupstore.REGISTRY.imported(), [backupstore.legacy_id(self.legacy)])

    def test_refused_entries_stay_out_of_the_store(self):
        dns_on(self.system)
        planted = {
            "tcp_timedwait": {"original": {"path": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
                                           "name": "TcpTimedWaitDelay", "value": 0}},         # ADR-0016 check fails
            "tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}},     # the tweak is off: stale
            "dns_fastest": {"captured_at": 1},                                               # no original
            "failover:6": {"original": {"automatic": False, "metric": 0}},                  # metric out of range
            "failover:x": {"original": {"automatic": True}},
            "anything_else": {"original": {}},
        }
        self.plant(planted)
        self.assertEqual(self.load(), {})
        self.assertEqual(self.reg.backup(), {})
        self.assertEqual(json.loads(self.legacy.with_name("backup.json.imported").read_text(encoding="utf-8")),
                         planted)   # kept for the record

    def test_an_entry_the_store_has_is_never_overwritten(self):
        dns_on(self.system)
        self.store({"dns_fastest": {"original": dns_original(["192.0.2.53"])}})
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"])}})
        self.load()
        self.assertEqual(self.reg.backup()["dns_fastest"]["original"]["servers"], ["192.0.2.53"])

    def test_a_new_backup_json_at_the_same_path_is_never_imported(self):
        self.plant({})
        self.load()
        dns_on(self.system)
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"])}})
        self.assertEqual(self.load(), {})
        self.assertTrue(self.legacy.exists())     # left alone: it is not read any more

    def test_without_admin_nothing_is_imported_and_the_reader_sees_both(self):
        self.store({"tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}}})
        self.plant({"rsc_off": {"original": {"adapter": "Wi-Fi", "ipv4": True, "ipv6": True}},
                    "tcp_ecn": {"original": {"setting": "ecncapability", "value": "enabled"}}})
        self.reg.admin = False
        self.assertEqual(self.load(), {"rsc_off": {"original": {"adapter": "Wi-Fi", "ipv4": True, "ipv6": True}},
                                       "tcp_ecn": {"original": {"setting": "ecncapability", "value": "disabled"}}})
        self.assertEqual(backupstore.REGISTRY.imported(), [])
        self.assertTrue(self.legacy.is_file())

    def test_a_corrupt_backup_json_is_not_imported_as_empty(self):
        self.legacy.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(backupstore.REGISTRY.imported(), [])


class TamperedBackupJsonTests(StoreCase):
    """The task of ADR-0018: whatever a process without Admin writes to backup.json, the elevated helper
    restores the original it captured itself."""

    def test_a_forged_dns_server_list_no_longer_reaches_the_restore(self):
        dns_on(self.system)
        self.store({"dns_fastest": {"original": dns_original(["192.0.2.53"]), "captured_at": 1}})
        self.plant({})
        self.load()                                           # imported: nothing in it
        self.plant({"dns_fastest": {"original": dns_original(["203.0.113.66"]), "captured_at": 2}})
        out = self.manager().disable("dns_fastest")
        self.assertTrue(out.ok, i18n.render(out.message, "en"))
        self.assertIn(("write", "dns_servers_set", (6, ["192.0.2.53"])), self.system.writes())
        self.assertNotIn("203.0.113.66", json.dumps(self.system.writes()))
        self.assertNotIn("dns_fastest", self.reg.backup())   # restored, entry removed from the store

    def test_a_forged_entry_for_a_tweak_that_is_off_is_never_imported(self):
        """Before the first elevated run the file is still read once: a forged dns_fastest entry is
        imported only if dns_fastest is on (ADR-0018's narrowed window)."""
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
        self.plant({})
        self.load()
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

    def test_restore_all_removes_the_store_and_the_empty_company_key(self):
        dns_on(self.system)
        self.store({"dns_fastest": {"original": dns_original(["192.0.2.53"])}})
        res = self.restore_all(self.manager())
        self.assertTrue(res["ok"], res)
        self.assertIn(("write", "dns_servers_set", (6, ["192.0.2.53"])), self.system.writes())
        self.assertEqual(self.reg.keys, {"SOFTWARE": {}})
        self.assertFalse(backupstore.exists())

    def test_an_empty_store_is_removed_too(self):
        self.store({})
        self.assertTrue(backupstore.exists())
        self.assertTrue(self.restore_all(self.manager())["ok"])
        self.assertFalse(backupstore.exists())

    def test_another_product_of_the_company_keeps_its_key(self):
        self.store({})
        self.reg.CreateKeyEx(self.reg.HKEY_LOCAL_MACHINE, backupstore.COMPANY_KEY + r"\Other")
        self.assertTrue(self.restore_all(self.manager())["ok"])
        self.assertIn(backupstore.COMPANY_KEY + r"\Other", self.reg.keys)
        self.assertNotIn(backupstore.STORE_KEY, self.reg.keys)

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
        self.assertFalse(backupstore.exists())

    def test_the_uninstaller_asks_for_the_store_even_without_backups(self):
        from app import installer
        self.store({})
        self.reg.admin = False
        ops = installer.Ops()
        self.assertFalse(ops.backups_left())
        self.assertTrue(ops.store_left())


if __name__ == "__main__":
    unittest.main()
