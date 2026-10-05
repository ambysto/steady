import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import autostart
from app.storage import Storage

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

QUERY_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Delay>PT20S</Delay></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec>
    <Command>C:\\Python311\\pythonw.exe</Command>
    <Arguments>"D:\\x\\run_monitor.pyw"</Arguments>
  </Exec></Actions>
</Task>"""


class FakeRunner:
    """Records calls instead of touching Task Scheduler."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.calls = []
        self.files = []
        self.result = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if "/XML" in args and "/Create" in args:
            path = args[args.index("/XML") + 1]
            self.files.append(Path(path).read_bytes())  # capture before install() deletes it
        return self.result


def parse(xml: str) -> ET.Element:
    # ElementTree refuses a str that declares UTF-16; parse the bytes form instead.
    return ET.fromstring(xml.encode("utf-16"))


class TaskXmlTests(unittest.TestCase):
    def setUp(self):
        self.root = parse(autostart.build_task_xml(user="PC\\me"))

    def val(self, path):
        return self.root.find(path, NS).text

    def test_no_72_hour_limit_and_runs_on_battery(self):
        # The reason the task is built from XML at all.
        self.assertEqual(self.val("t:Settings/t:ExecutionTimeLimit"), "PT0S")
        self.assertEqual(self.val("t:Settings/t:DisallowStartIfOnBatteries"), "false")
        self.assertEqual(self.val("t:Settings/t:StopIfGoingOnBatteries"), "false")

    def test_restarts_after_crash_and_never_runs_twice(self):
        self.assertEqual(self.val("t:Settings/t:RestartOnFailure/t:Count"), "3")
        self.assertEqual(self.val("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")

    def test_logon_trigger_for_this_user_with_delay(self):
        self.assertEqual(self.val("t:Triggers/t:LogonTrigger/t:UserId"), "PC\\me")
        self.assertEqual(self.val("t:Triggers/t:LogonTrigger/t:Delay"), "PT20S")
        self.assertEqual(self.val("t:Principals/t:Principal/t:LogonType"), "InteractiveToken")

    def test_run_level(self):
        self.assertEqual(self.val("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")
        high = parse(autostart.build_task_xml(highest=True, user="PC\\me"))
        self.assertEqual(high.find("t:Principals/t:Principal/t:RunLevel", NS).text, "HighestAvailable")

    def test_action_uses_pythonw_and_launcher_from_repo_root(self):
        self.assertEqual(Path(self.val("t:Actions/t:Exec/t:Command")).name.lower(), "pythonw.exe")
        self.assertIn("run_monitor.pyw", self.val("t:Actions/t:Exec/t:Arguments"))
        self.assertTrue(autostart.launcher_path().exists())
        self.assertEqual(Path(self.val("t:Actions/t:Exec/t:WorkingDirectory")), autostart.config.ROOT)

    def test_special_characters_in_user_and_paths_are_escaped(self):
        with mock.patch.object(autostart, "pythonw_path", return_value=Path("C:/A&B <x>/pythonw.exe")):
            root = parse(autostart.build_task_xml(user="DOM\\R&D"))
        self.assertEqual(root.find("t:Principals/t:Principal/t:UserId", NS).text, "DOM\\R&D")
        self.assertIn("A&B <x>", root.find("t:Actions/t:Exec/t:Command", NS).text)

    def test_current_user_needs_username(self):
        with mock.patch.dict(os.environ, {"USERNAME": "", "USERDOMAIN": "X"}):
            with self.assertRaises(RuntimeError):
                autostart.current_user()


@unittest.skipUnless(sys.platform == "win32", "needs Task Scheduler")
class ValidateTests(unittest.TestCase):
    """TASK_VALIDATE_ONLY: Task Scheduler checks the XML and registers nothing."""

    def test_real_definition_is_accepted(self):
        ok, message = autostart.validate_xml(autostart.build_task_xml())
        self.assertTrue(ok, message)

    def test_broken_definition_is_rejected(self):
        bad = autostart.build_task_xml().replace("<Priority>7</Priority>", "<Priority>banana</Priority>")
        ok, message = autostart.validate_xml(bad)
        self.assertFalse(ok)
        self.assertIn("Priority", message)

    def test_validation_registers_nothing(self):
        before = autostart.status().installed
        autostart.validate_xml(autostart.build_task_xml())
        self.assertEqual(autostart.status().installed, before)


class ArgsTests(unittest.TestCase):
    def test_create_delete_query(self):
        self.assertEqual(autostart.build_create_args("C:/t.xml"),
                         ["schtasks", "/Create", "/TN", autostart.TASK_NAME, "/XML", "C:/t.xml", "/F"])
        self.assertEqual(autostart.build_delete_args()[:3], ["schtasks", "/Delete", "/TN"])
        self.assertIn("/XML", autostart.build_query_args())


class LegacyTaskTests(unittest.TestCase):
    def runner(self, present):
        calls = []

        def run(args, **kw):
            calls.append(args[1:4])
            code = 0 if args[1] != "/Query" or args[3] in present else 1
            return SimpleNamespace(returncode=code, stdout=b"", stderr=b"")
        return run, calls

    def test_installing_removes_the_task_registered_under_the_old_name(self):
        run, calls = self.runner(present={"StableInternet Monitor"})
        with mock.patch.object(autostart.tempfile, "mkstemp", return_value=(os.open(os.devnull, os.O_WRONLY), os.devnull)), \
                mock.patch.object(autostart.os, "unlink"), mock.patch.object(autostart.time, "sleep") as sleep:
            self.assertTrue(autostart.install(runner=run).ok)
        self.assertEqual(calls, [["/Create", "/TN", "Ambysto Steady Monitor"], ["/Query", "/TN", "StableInternet Monitor"],
                                 ["/End", "/TN", "StableInternet Monitor"], ["/Delete", "/TN", "StableInternet Monitor"]])
        sleep.assert_called_once()                              # lets the old monitor release its mutex

    def test_nothing_to_remove(self):
        run, calls = self.runner(present=set())
        self.assertEqual(autostart.remove_legacy_tasks(run), [])
        self.assertEqual(calls, [["/Query", "/TN", "StableInternet Monitor"]])


class ParseTests(unittest.TestCase):
    def test_parse_installed(self):
        st = autostart.parse_query_xml(QUERY_XML)
        self.assertTrue(st.installed)
        self.assertEqual((st.run_level, st.time_limit), ("LeastPrivilege", "PT0S"))
        self.assertIn("pythonw.exe", st.command)
        self.assertEqual(st.problems, [])

    def test_missing_run_level_means_least_privilege(self):
        # What Task Scheduler actually returns for our installed task (seen 2026-10-04).
        xml = QUERY_XML.replace("<RunLevel>LeastPrivilege</RunLevel>", "")
        self.assertEqual(autostart.parse_query_xml(xml).run_level, "LeastPrivilege")

    def test_missing_time_limit_means_the_72h_default(self):
        st = autostart.parse_query_xml(QUERY_XML.replace("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", ""))
        self.assertEqual(st.time_limit, "PT72H")
        self.assertTrue(st.problems)

    def test_parse_not_a_task(self):
        self.assertFalse(autostart.parse_query_xml("ERROR: The system cannot find the file specified.").installed)
        self.assertFalse(autostart.parse_query_xml("").installed)


class StatusTests(unittest.TestCase):
    def test_not_installed_when_query_fails(self):
        r = FakeRunner(returncode=1, stderr=b"ERROR: The system cannot find the file specified.")
        self.assertFalse(autostart.status(r).installed)
        self.assertEqual(r.calls[0][:2], ["schtasks", "/Query"])

    def test_installed(self):
        st = autostart.status(FakeRunner(stdout=QUERY_XML.encode("utf-8")))
        self.assertTrue(st.installed)

    def test_real_query_is_read_only_and_does_not_raise(self):
        self.assertIsInstance(autostart.status().installed, bool)


class ActionTests(unittest.TestCase):
    def test_install_writes_utf16_xml_passes_it_to_schtasks_then_deletes_it(self):
        r = FakeRunner()
        res = autostart.install(runner=r)
        self.assertTrue(res.ok)
        args = r.calls[0]                                         # then the legacy-name cleanup
        path = args[args.index("/XML") + 1]
        self.assertFalse(Path(path).exists())                    # temp file cleaned up
        data = r.files[0]
        self.assertTrue(data.startswith(b"\xff\xfe"))            # UTF-16 LE BOM
        self.assertIn("ExecutionTimeLimit>PT0S", data.decode("utf-16"))

    def test_temp_file_removed_even_when_schtasks_fails(self):
        r = FakeRunner(returncode=1, stderr=b"ERROR: Access is denied.")
        res = autostart.install(highest=True, runner=r)
        self.assertFalse(res.ok)
        self.assertIn("Access is denied", res.message)
        args = r.calls[0]
        self.assertFalse(Path(args[args.index("/XML") + 1]).exists())

    def test_uninstall(self):
        r = FakeRunner()
        self.assertTrue(autostart.uninstall(runner=r).ok)
        self.assertEqual(r.calls, [autostart.build_delete_args()])


class CliTests(unittest.TestCase):
    def run_cli(self, argv, runner):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = autostart.main(argv, runner)
        return code, out.getvalue()

    def test_install_without_apply_is_a_dry_run_showing_the_xml(self):
        r = FakeRunner()
        code, out = self.run_cli(["install"], r)
        self.assertEqual((code, r.calls), (0, []))  # nothing executed
        self.assertIn("dry run", out)
        self.assertIn("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", out)

    def test_uninstall_without_apply_is_a_dry_run(self):
        r = FakeRunner()
        code, out = self.run_cli(["uninstall"], r)
        self.assertEqual((code, r.calls), (0, []))
        self.assertIn("/Delete", out)

    def test_install_with_apply_executes(self):
        r = FakeRunner()
        code, _ = self.run_cli(["install", "--apply"], r)
        self.assertEqual((code, r.calls[0][1]), (0, "/Create"))

    def test_apply_failure_gives_nonzero_exit(self):
        code, out = self.run_cli(["install", "--apply"], FakeRunner(returncode=1, stderr=b"nope"))
        self.assertEqual(code, 1)
        self.assertIn("nope", out)

    def test_status_reports_a_72h_problem(self):
        xml = QUERY_XML.replace("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", "")
        code, out = self.run_cli(["status"], FakeRunner(stdout=xml.encode()))
        self.assertEqual(code, 0)
        self.assertIn("problem", out)


@unittest.skipUnless(sys.platform == "win32", "needs pythonw.exe")
class LauncherTests(unittest.TestCase):
    def test_pythonw_launcher_runs_without_a_console_and_logs_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, STABLEINTERNET_DATA=tmp)
            proc = subprocess.run([str(autostart.pythonw_path()), str(autostart.launcher_path()), "--seconds", "3"],
                                  cwd=tempfile.gettempdir(), env=env, timeout=60)  # cwd elsewhere: launcher must cope
            self.assertEqual(proc.returncode, 0)
            log = (Path(tmp) / "monitor.log").read_text(encoding="utf-8")
            self.assertIn("monitoring ->", log)
            with Storage(Path(tmp) / "metrics.db") as db:
                kinds = [e["kind"] for e in db.query_events()]
            self.assertEqual((kinds.count("monitor_start"), kinds.count("monitor_stop")), (1, 1))


if __name__ == "__main__":
    unittest.main()
