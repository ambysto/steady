import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from app import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRITER = """
import sys
from app import config
for i in range(25):
    config.put_backup_entry(f"{sys.argv[1]}:{i}", {"i": i})
"""


class BackupLockTests(unittest.TestCase):
    """backup.json is written by the monitor, the elevated helper and the uninstaller at once."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"STABLEINTERNET_DATA": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_entries_from_several_processes_all_survive(self):
        procs = [subprocess.Popen([sys.executable, "-c", WRITER, name], cwd=ROOT, env=dict(os.environ))
                 for name in ("monitor", "elevated", "uninstall")]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        self.assertEqual(len(config.load_backup()), 75)

    def test_removing_one_entry_keeps_the_others(self):
        config.put_backup_entry("wifi_power_saving", {"original": {}})
        config.put_backup_entry("failover:21", {"original": {"automatic": True}})
        config.put_backup_entry("failover:21", None)
        self.assertEqual(list(config.load_backup()), ["wifi_power_saving"])

    def test_a_held_lock_times_out_instead_of_hanging(self):
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import time\nfrom app import config\nwith config.backup_lock():\n"
                                   "    print('held', flush=True)\n    time.sleep(5)"],
                                  cwd=ROOT, env=dict(os.environ), stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        with self.assertRaises(TimeoutError):
            with config.backup_lock(timeout=0.3):
                pass


if __name__ == "__main__":
    unittest.main()
