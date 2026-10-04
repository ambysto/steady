import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import uuid
from pathlib import Path

from app.singleton import SingleInstance
from app.storage import Storage

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(sys.platform == "win32", "named mutex is Windows-only")
class SingleInstanceTests(unittest.TestCase):
    def name(self):
        return f"StableInternetTest.{uuid.uuid4().hex}"

    def test_second_acquire_of_same_name_fails(self):
        name = self.name()
        a, b = SingleInstance(name), SingleInstance(name)
        self.addCleanup(a.release)
        self.addCleanup(b.release)
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())

    def test_release_lets_another_in(self):
        name = self.name()
        a, b = SingleInstance(name), SingleInstance(name)
        self.addCleanup(b.release)
        self.assertTrue(a.acquire())
        a.release()
        self.assertTrue(b.acquire())

    def test_acquire_twice_on_same_object_is_idempotent(self):
        a = SingleInstance(self.name())
        self.addCleanup(a.release)
        self.assertTrue(a.acquire())
        self.assertTrue(a.acquire())

    def test_different_names_are_independent(self):
        a, b = SingleInstance(self.name()), SingleInstance(self.name())
        self.addCleanup(a.release)
        self.addCleanup(b.release)
        self.assertTrue(a.acquire())
        self.assertTrue(b.acquire())

    def test_release_without_acquire_and_context_manager(self):
        name = self.name()
        SingleInstance(name).release()
        with SingleInstance(name) as g:
            self.assertTrue(g.acquire())
        self.assertTrue(SingleInstance(name).acquire())  # freed on exit

    def test_name_gets_local_prefix(self):
        self.assertEqual(SingleInstance("x").name, "Local\\x")
        self.assertEqual(SingleInstance("Global\\x").name, "Global\\x")

    def test_mutex_is_freed_when_the_owning_process_dies(self):
        name = self.name()
        code = (f"from app.singleton import SingleInstance; import time;"
                f"g = SingleInstance({name!r}); assert g.acquire(); print('held', flush=True); time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.kill)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        mine = SingleInstance(name)
        self.addCleanup(mine.release)
        self.assertFalse(mine.acquire())
        proc.kill()  # no clean shutdown: a stale PID file would stay behind, a mutex must not
        proc.wait(10)
        self.assertTrue(mine.acquire())


class MutexNameTests(unittest.TestCase):
    def test_scoped_to_data_directory(self):
        from app import monitor
        names = []
        for d in (tempfile.mkdtemp(), tempfile.mkdtemp()):
            with unittest.mock.patch.dict(os.environ, {"STABLEINTERNET_DATA": d}):
                names.append(monitor.mutex_name())
                same_dir_other_case = monitor.mutex_name()
            os.rmdir(d)
            self.assertEqual(names[-1], same_dir_other_case)
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all(n.startswith(monitor.MUTEX_NAME + ".") for n in names))


@unittest.skipUnless(sys.platform == "win32", "needs Windows")
class MonitorMainSingleInstanceTests(unittest.TestCase):
    def test_second_monitor_exits_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, STABLEINTERNET_DATA=tmp)
            run = [sys.executable, "-m", "app.monitor", "--seconds", "6"]
            first = subprocess.Popen(run, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(2.5)
                started = time.monotonic()
                second = subprocess.run(run, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
                self.assertLess(time.monotonic() - started, 4)
                self.assertEqual(second.returncode, 0)
                self.assertIn("already running", second.stderr + second.stdout)
                first.wait(30)
                with Storage(Path(tmp) / "metrics.db") as db:
                    kinds = [e["kind"] for e in db.query_events()]
            finally:
                first.kill()
                first.wait(10)
            self.assertEqual(kinds.count("monitor_start"), 1)
            self.assertEqual(kinds.count("monitor_stop"), 1)


if __name__ == "__main__":
    unittest.main()
