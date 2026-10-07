import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import i18n, installer, runtime


class FakeOps:
    def __init__(self, backups=False, restore_ok=True, fail=None, location=None, store=None, legacy=None,
                 machine_ok=True, machine_cancelled=False, files=()):
        self.calls, self.backups, self.restore_ok, self.fail, self.location = [], backups, restore_ok, fail, location
        self.store = backups if store is None else store
        self.legacy, self.machine_ok, self.machine_cancelled, self.files = legacy, machine_ok, machine_cancelled, set(files)

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if name == self.fail:
            raise OSError(f"{name} failed")

    def copy_tree(self, src, dst): self._do("copy", src, dst)
    def replace_tree(self, src, dst): self._do("replace", src, dst)
    def shortcut(self, link, target, args=""): self._do("shortcut", link.name, args)
    def remove_file(self, path): self._do("remove_file", path.name)
    def is_file(self, path): return Path(path) in self.files
    def register(self, values): self._do("register", values)
    def unregister(self): self._do("unregister")
    def registered_location(self): return self.location
    def legacy_location(self): return self.legacy
    def unregister_legacy(self): self._do("unregister_legacy")
    def task_install(self, exe): self._do("task_install", exe)
    def task_start(self): self._do("task_start")
    def task_stop(self): self._do("task_end")
    def task_stop_and_delete(self): self._do("task_stop")
    def launch(self, exe, args): self._do("launch", args)
    def stop_other_instances(self, folder): self._do("stop_instances", folder)
    def backups_left(self): return self.backups
    def store_left(self): return self.store
    def delete_tree(self, path): self._do("delete_tree", path)
    def delete_program_folder(self, folder): self._do("delete_program_folder", folder)
    def delete_after_exit(self, folder, also_wait=None): self._do("delete_after_exit", folder, also_wait)

    def restore_everything(self):
        self._do("restore_all")
        if self.restore_ok:
            self.backups = self.store = False
        return self.restore_ok, i18n.msg("installer.restored_all")

    def install_machine(self):
        self._do("install_machine")
        if self.machine_cancelled:
            return SimpleNamespace(ok=False, cancelled=True, message=i18n.msg("elevation.cancelled"))
        return SimpleNamespace(ok=self.machine_ok, cancelled=False, message=i18n.msg("elevation.no_result", exit_code=0))

    def uninstall_machine(self):
        self._do("uninstall_machine")
        if self.machine_cancelled:
            return SimpleNamespace(ok=False, cancelled=True, message=i18n.msg("elevation.cancelled"))
        if self.machine_ok:
            self.location, self.backups, self.store = None, False, False
        return SimpleNamespace(ok=self.machine_ok, cancelled=False, message=i18n.msg("installer.keep_backup"))

    def names(self):
        return [c[0] for c in self.calls]


SRC = Path("C:/Users/x/Downloads/Ambysto Steady")
DST = Path("C:/Program Files/Ambysto Steady")
OLD = Path("C:/Users/x/AppData/Local/Programs/Ambysto Steady")    # a per-user install of 0.5.0 or earlier


class MachineInstallTests(unittest.TestCase):
    """The elevated half (ADR-0019): for all users, never per-user things."""

    def test_machine_plan(self):
        ops = FakeOps()
        results = installer.run_steps(installer.machine_steps(SRC, DST, ops))
        self.assertTrue(all(ok for _, ok, _ in results))
        self.assertEqual(ops.names(), ["stop_instances", "replace", "register", "shortcut"])
        self.assertEqual(ops.calls[1][1:], (SRC, DST))
        reg = next(c[1] for c in ops.calls if c[0] == "register")
        self.assertEqual(reg["UninstallString"], f'"{DST / runtime.EXE_NAME}" uninstall')
        self.assertEqual((reg["DisplayName"], reg["InstallLocation"]), ("Ambysto Steady", str(DST)))
        self.assertNotIn("task_install", ops.names())          # the task belongs to the user, not to the admin
        self.assertEqual(ops.calls[-1][1:], (f"{runtime.APP_NAME}.lnk", ""))

    def test_already_in_place_skips_the_copy(self):
        ops = FakeOps()
        installer.run_steps(installer.machine_steps(DST, DST, ops))
        self.assertNotIn("replace", ops.names())

    def test_install_machine_copies_its_own_folder_under_program_files(self):
        ops = FakeOps()
        with mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=SRC), \
                mock.patch.object(installer, "default_target", return_value=DST), \
                mock.patch.object(installer, "check_target"):
            out = installer.install_machine(ops)
        self.assertTrue(out["ok"], out)
        self.assertEqual(ops.calls[1][1:], (SRC, DST))

    def test_install_machine_refuses_from_source(self):
        ops = FakeOps()
        self.assertFalse(installer.install_machine(ops)["ok"])
        self.assertEqual(ops.calls, [])

    def test_a_failed_copy_is_reported(self):
        ops = FakeOps(fail="replace")
        with mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=SRC), \
                mock.patch.object(installer, "default_target", return_value=DST), \
                mock.patch.object(installer, "check_target"):
            out = installer.install_machine(ops)
        self.assertFalse(out["ok"])
        self.assertNotIn("register", ops.names())


class InstallTests(unittest.TestCase):
    """The whole install as the user runs it: the elevated half first, then this user's half."""

    def test_install_plan(self):
        ops = FakeOps()
        results = installer.install(DST, ops)
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertEqual(ops.names(), ["install_machine", "task_install", "shortcut", "task_start", "launch"])
        self.assertEqual(ops.calls[1][1], DST / runtime.EXE_NAME)       # the task runs the Program Files copy
        self.assertEqual(ops.calls[2][1:], (f"{runtime.APP_NAME}.lnk", "desktop --minimized"))

    def test_declined_uac_changes_nothing_for_the_user(self):
        ops = FakeOps(machine_cancelled=True)
        results = installer.install(DST, ops)
        self.assertEqual(ops.names(), ["install_machine"])
        self.assertFalse(results[-1][1])

    def test_no_result_from_another_account_is_checked_by_its_effect(self):
        # Over-the-shoulder UAC: the helper ran as the admin account, its result went to that profile.
        ops = FakeOps(machine_ok=False, location=str(DST), files=[DST / runtime.EXE_NAME])
        results = installer.install(DST, ops)
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertIn("task_install", ops.names())

    def test_a_failed_elevated_half_stops_the_install(self):
        ops = FakeOps(machine_ok=False)                                  # no entry, no exe: it did not happen
        results = installer.install(DST, ops)
        self.assertEqual(ops.names(), ["install_machine"])
        self.assertFalse(results[-1][1])

    def test_an_earlier_per_user_install_is_removed_and_its_data_kept(self):
        ops = FakeOps(legacy=str(OLD))
        installer.install(DST, ops)
        names = ops.names()
        self.assertEqual(names[1:5], ["stop_instances", "remove_file", "unregister_legacy", "delete_program_folder"])
        self.assertEqual(ops.calls[4][1], OLD)
        self.assertLess(names.index("delete_program_folder"), names.index("task_install"))   # its task is replaced after
        self.assertNotIn("delete_tree", names)                                               # the data folder stays

    def test_a_failed_legacy_cleanup_does_not_stop_the_install(self):
        ops = FakeOps(legacy=str(OLD), fail="delete_program_folder")
        results = installer.install(DST, ops)
        self.assertIn("launch", ops.names())
        self.assertEqual([ok for _, ok, _ in results].count(False), 1)

    def test_main_installs_under_program_files_and_dry_run_changes_nothing(self):
        ops = FakeOps()
        with mock.patch.object(installer, "_message_box", return_value=6), mock.patch.object(runtime, "FROZEN", True), \
                mock.patch.object(installer, "default_target", return_value=DST), \
                mock.patch.object(runtime, "install_dir", return_value=SRC), mock.patch.object(installer, "check_target"):
            self.assertEqual(installer.main(["install", "--dry-run"], ops=ops), 0)
            self.assertEqual(ops.calls, [])
            self.assertEqual(installer.main(["install", "--yes"], ops=ops), 0)
        self.assertEqual(ops.names()[0], "install_machine")

    def test_there_is_no_target_option_any_more(self):
        with self.assertRaises(SystemExit):
            installer.main(["install", "--target", "C:/Users/x/Downloads", "--dry-run"], ops=FakeOps())


class UninstallTests(unittest.TestCase):
    def test_uninstall_plan(self):
        ops = FakeOps(location=str(DST))
        results = installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=False))
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertEqual(ops.names(), ["stop_instances", "task_end", "uninstall_machine", "task_stop", "remove_file",
                                       "remove_file"])

    def test_restore_then_delete_data_when_asked(self):
        ops = FakeOps(backups=True, location=str(DST))
        results = installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=True))
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertLess(ops.names().index("uninstall_machine"), ops.names().index("delete_tree"))

    def test_a_failed_restore_stops_the_uninstall(self):
        # UAC declined: tweaks are still on, so the app stays (task, shortcuts, entry, data) to undo them later
        ops = FakeOps(backups=True, location=str(DST), machine_cancelled=True)
        results = installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=True))
        self.assertEqual(ops.names(), ["stop_instances", "task_end", "uninstall_machine"])
        self.assertFalse(results[-1][1])

    def test_no_result_but_the_entry_is_gone_counts_as_done(self):
        ops = FakeOps(location=None, machine_ok=False)
        ops.uninstall_machine = lambda: SimpleNamespace(ok=False, cancelled=False, message="no result")
        results = installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=False))
        self.assertTrue(all(ok for _, ok, _ in results), results)

    def test_a_stopped_uninstall_starts_the_monitor_again(self):
        ops = FakeOps(backups=True, location=str(DST), machine_cancelled=True)
        with mock.patch.object(installer, "_message_box", return_value=6), \
                mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=DST):
            self.assertEqual(installer.main(["uninstall", "--yes"], ops=ops), 1)
        self.assertEqual(ops.names()[-1], "task_start")

    def test_refuses_from_source_or_an_unregistered_copy(self):
        ops = FakeOps(location=str(DST))
        with mock.patch.object(installer, "_message_box", return_value=6):
            self.assertEqual(installer.main(["uninstall", "--yes"], ops=ops), 2)          # not frozen
            with mock.patch.object(runtime, "FROZEN", True), \
                    mock.patch.object(runtime, "install_dir", return_value=SRC):
                self.assertEqual(installer.main(["uninstall", "--yes"], ops=ops), 2)      # not the installed one
        self.assertEqual(ops.calls, [])

    def test_uninstall_from_the_registered_folder(self):
        ops = FakeOps(location=str(DST))
        with mock.patch.object(installer, "_message_box", return_value=6) as box, \
                mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=DST):
            self.assertEqual(installer.main(["uninstall", "--yes"], ops=ops), 0)
        self.assertIn("uninstall_machine", ops.names())
        box.assert_not_called()                                             # --yes: quiet, the result goes to the log


class MachineUninstallTests(unittest.TestCase):
    """The elevated half of uninstall: restore first; only then remove the all-users parts and the folder."""

    def run_it(self, restored, location=str(DST)):
        ops = FakeOps(location=location)
        with mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=DST), \
                mock.patch("app.elevated.restore_everything", return_value=restored):
            return installer.uninstall_machine(4242, ops), ops

    def test_restores_then_removes_and_waits_for_the_uninstaller(self):
        out, ops = self.run_it({"ok": True, "message": i18n.msg("installer.restored_all")})
        self.assertTrue(out["ok"])
        self.assertEqual(ops.names(), ["unregister", "remove_file", "delete_after_exit"])
        self.assertEqual(ops.calls[-1][1:], (DST, 4242))

    def test_a_failed_restore_keeps_the_app_installed(self):
        out, ops = self.run_it({"ok": False, "message": "WMI is busy"})
        self.assertFalse(out["ok"])
        self.assertEqual(ops.calls, [])

    def test_refuses_for_a_copy_that_is_not_the_registered_one(self):
        out, ops = self.run_it({"ok": True, "message": ""}, location=str(OLD))
        self.assertFalse(out["ok"])
        self.assertEqual(ops.calls, [])


class GuardTests(unittest.TestCase):
    def test_install_only_into_an_empty_folder_or_over_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Downloads"
            installer.check_target(SRC, folder)                                 # does not exist yet: fine
            folder.mkdir()
            installer.check_target(SRC, folder)                                 # empty: fine
            (folder / "holiday.jpg").write_bytes(b"")
            with self.assertRaises(ValueError):
                installer.check_target(SRC, folder)                             # uninstall would delete the photo
            (folder / runtime.EXE_NAME).write_bytes(b"")
            installer.check_target(SRC, folder)                                 # an earlier copy: upgrade

    def test_install_refuses_a_folder_with_other_files(self):
        ops = FakeOps()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_bytes(b"")
            with mock.patch.object(installer, "_message_box", return_value=6), mock.patch.object(runtime, "FROZEN", True), \
                    mock.patch.object(installer, "default_target", return_value=Path(tmp)):
                self.assertEqual(installer.main(["install", "--yes"], ops=ops), 2)
        self.assertEqual(ops.calls, [])

    def test_never_a_drive_or_a_folder_without_the_exe(self):
        for bad in ("C:/", "C:/Users"):
            with self.assertRaises(ValueError):
                installer.check_deletable(Path(bad), marker=None)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "app"
            folder.mkdir()
            with self.assertRaises(ValueError):
                installer.check_deletable(folder, marker=runtime.EXE_NAME)
            (folder / runtime.EXE_NAME).write_bytes(b"")
            installer.check_deletable(folder, marker=runtime.EXE_NAME)      # fine now

    def test_paths(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/L", "APPDATA": "C:/A", "ProgramFiles": "C:/Users/x"}):
            # Program Files comes from the shell, not from an environment variable a user can set (ADR-0019)
            self.assertEqual(installer.default_target(), runtime.program_files() / "Ambysto Steady")
            self.assertNotEqual(runtime.program_files(), Path("C:/Users/x"))
            self.assertEqual(installer.startup_link().parent.name, "Startup")
        self.assertTrue(runtime.is_protected(installer.default_target()))
        self.assertTrue(installer.common_start_menu_link().is_absolute())

    def test_only_program_files_is_protected(self):
        self.assertTrue(runtime.is_protected(runtime.program_files() / "Ambysto Steady"))
        for folder in (OLD, SRC, Path("C:/"), runtime.program_files().parent / "Program Files Evil"):
            self.assertFalse(runtime.is_protected(folder), folder)


class ElevationGuardTests(unittest.TestCase):
    """A packaged copy outside Program Files never runs elevated (ADR-0019)."""

    def test_run_elevated_refuses_from_an_unprotected_packaged_copy(self):
        from app import elevation
        launches = []
        with mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=SRC):
            res = elevation.run_elevated("tweak-enable", "wifi_power_saving", launcher=lambda *a: launches.append(a))
        self.assertFalse(res.ok)
        self.assertEqual(i18n.render(res.message, "en"), i18n.t("elevation.not_installed", "en"))
        self.assertEqual(launches, [])

    def test_install_machine_is_the_one_operation_allowed_from_the_download(self):
        from app import elevation
        self.assertTrue(runtime.elevation_allowed())          # from source: development
        with mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=SRC):
            self.assertFalse(runtime.elevation_allowed())
            res = elevation.run_elevated("install-machine", "-",
                                         launcher=lambda *a: elevation.Launch(False, error=1223))
        self.assertTrue(res.cancelled)                        # it got as far as the UAC prompt

    def test_the_helper_refuses_too(self):
        from app import elevated
        with mock.patch.object(elevated.winutil, "is_admin", return_value=True), \
                mock.patch.object(runtime, "FROZEN", True), mock.patch.object(runtime, "install_dir", return_value=SRC):
            out = elevated.run_op("restore-all", "-")
        self.assertEqual(out["message"], i18n.msg("elevation.not_installed"))

    def test_install_machine_takes_no_source_from_its_caller(self):
        from app import elevated
        with mock.patch.object(elevated.winutil, "is_admin", return_value=True):
            self.assertFalse(elevated.run_op("install-machine", "C:/Users/x/evil")["ok"])
            self.assertFalse(elevated.run_op("uninstall-machine", "1; calc")["ok"])

    def test_highest_privileges_only_for_an_exe_under_program_files(self):
        from app import autostart
        runs = []
        res = autostart.install(highest=True, runner=lambda *a, **k: runs.append(a), exe=OLD / runtime.EXE_NAME)
        self.assertFalse(res.ok)
        self.assertIn("Program Files", res.message)
        self.assertEqual(runs, [])


class RuntimeCommandTests(unittest.TestCase):
    def test_source_and_packaged_commands(self):
        program, args, _ = runtime.command("monitor")
        self.assertTrue(args[0].endswith("run_monitor.pyw"))
        program, args, cwd = runtime.command("monitor", exe=DST / runtime.EXE_NAME)
        self.assertEqual((program, args, cwd), (DST / runtime.EXE_NAME, ["monitor"], DST))
        self.assertEqual(runtime.command("elevated", "restore-all", "-")[1][:3], ["-m", "app.elevated", "restore-all"])
        with self.assertRaises(ValueError):
            runtime.command("format")

    def test_task_xml_runs_the_installed_exe(self):
        from app import autostart
        xml = autostart.build_task_xml(user="PC\\me", exe=DST / runtime.EXE_NAME)
        self.assertIn(f"<Command>{DST / runtime.EXE_NAME}</Command>", xml)
        self.assertIn("<Arguments>monitor</Arguments>", xml)


if __name__ == "__main__":
    unittest.main()
