import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import i18n, installer, runtime


class FakeOps:
    def __init__(self, backups=False, restore_ok=True, fail=None, location=None, store=None):
        self.calls, self.backups, self.restore_ok, self.fail, self.location = [], backups, restore_ok, fail, location
        self.store = backups if store is None else store

    def _do(self, name, *args):
        self.calls.append((name, *args))
        if name == self.fail:
            raise OSError(f"{name} failed")

    def copy_tree(self, src, dst): self._do("copy", src, dst)
    def shortcut(self, link, target, args=""): self._do("shortcut", link.name, args)
    def remove_file(self, path): self._do("remove_file", path.name)
    def register(self, values): self._do("register", values)
    def unregister(self): self._do("unregister")
    def registered_location(self): return self.location
    def task_install(self, exe): self._do("task_install", exe)
    def task_start(self): self._do("task_start")
    def task_stop(self): self._do("task_end")
    def task_stop_and_delete(self): self._do("task_stop")
    def launch(self, exe, args): self._do("launch", args)
    def stop_other_instances(self, folder): self._do("stop_instances", folder)
    def backups_left(self): return self.backups
    def store_left(self): return self.store

    def restore_everything(self):
        self._do("restore_all")
        if self.restore_ok:
            self.backups = self.store = False
        return self.restore_ok, i18n.msg("installer.restored_all")

    def delete_tree(self, path): self._do("delete_tree", path)
    def delete_after_exit(self, folder): self._do("delete_after_exit", folder)

    def names(self):
        return [c[0] for c in self.calls]


SRC, DST = Path("C:/Users/x/Downloads/Ambysto Steady"), Path("C:/Users/x/AppData/Local/Programs/Ambysto Steady")


class InstallTests(unittest.TestCase):
    def test_install_plan(self):
        ops = FakeOps()
        results = installer.run_steps(installer.install_steps(SRC, DST, ops))
        self.assertTrue(all(ok for _, ok, _ in results))
        self.assertEqual(ops.names(), ["stop_instances", "task_end", "copy", "task_install", "shortcut", "shortcut",
                                       "register", "task_start", "launch"])
        self.assertEqual(ops.calls[0][1], DST)                               # the copy being replaced
        self.assertEqual(ops.calls[3][1], DST / runtime.EXE_NAME)           # the task runs the installed copy
        self.assertEqual([c[2] for c in ops.calls if c[0] == "shortcut"], ["", "desktop --minimized"])
        reg = next(c[1] for c in ops.calls if c[0] == "register")
        self.assertEqual(reg["UninstallString"], f'"{DST / runtime.EXE_NAME}" uninstall')
        self.assertEqual((reg["DisplayName"], reg["InstallLocation"]), ("Ambysto Steady", str(DST)))

    def test_already_in_place_skips_the_copy(self):
        ops = FakeOps()
        installer.run_steps(installer.install_steps(DST, DST, ops))
        self.assertNotIn("copy", ops.names())

    def test_a_failed_required_step_stops_the_plan(self):
        ops = FakeOps(fail="task_install")
        results = installer.run_steps(installer.install_steps(SRC, DST, ops))
        self.assertEqual(ops.names(), ["stop_instances", "task_end", "copy", "task_install"])
        self.assertFalse(results[-1][1])

    def test_dry_run_changes_nothing(self):
        ops = FakeOps()
        results = installer.run_steps(installer.install_steps(SRC, DST, ops), dry_run=True)
        self.assertEqual(ops.calls, [])
        self.assertEqual(len(results), 6)

    def test_nothing_running_yet_is_fine(self):
        ops = FakeOps(fail="task_end")                                      # schtasks /End: no such task
        results = installer.run_steps(installer.install_steps(SRC, DST, ops))
        self.assertIn("launch", ops.names())
        self.assertEqual([ok for _, ok, _ in results].count(False), 1)


class UninstallTests(unittest.TestCase):
    def test_restores_only_when_something_is_backed_up(self):
        ops = FakeOps(backups=False)
        installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=False))
        self.assertNotIn("restore_all", ops.names())
        self.assertEqual(ops.names(), ["stop_instances", "task_end", "task_stop", "remove_file", "remove_file",
                                       "unregister", "delete_after_exit"])

    def test_restore_then_delete_data_when_asked(self):
        ops = FakeOps(backups=True)
        results = installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=True))
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertLess(ops.names().index("restore_all"), ops.names().index("delete_tree"))

    def test_a_failed_restore_stops_the_uninstall(self):
        # UAC declined: tweaks are still on, so the app stays (task, shortcuts, entry, data) to undo them later
        ops = FakeOps(backups=True, restore_ok=False)
        results = installer.run_steps(installer.uninstall_steps(DST, ops, delete_data=True))
        self.assertEqual(ops.names(), ["stop_instances", "task_end", "restore_all"])
        self.assertFalse(results[-1][1])

    def test_a_stopped_uninstall_starts_the_monitor_again(self):
        ops = FakeOps(backups=True, restore_ok=False, location=str(DST))
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
        self.assertIn("delete_after_exit", ops.names())
        box.assert_not_called()                                             # --yes: quiet, the result goes to the log


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
            with mock.patch.object(installer, "_message_box", return_value=6), mock.patch.object(runtime, "FROZEN", True):
                self.assertEqual(installer.main(["install", "--target", tmp, "--yes"], ops=ops), 2)
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
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/L", "APPDATA": "C:/A"}):
            self.assertEqual(installer.default_target(), Path("C:/L/Programs/Ambysto Steady"))
            self.assertEqual(installer.startup_link().parent.name, "Startup")


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
