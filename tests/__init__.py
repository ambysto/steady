import os
import tempfile

# Backups go to backup.json in each test's data folder, never to the real HKLM key (ADR-0018).
os.environ["STABLEINTERNET_BACKUP"] = "file"


class _NoRealInstallerOps:
    """installer.Ops changes the real machine (program folders, HKLM, the monitor task). A test must pass
    a fake; one that reaches the real class fails here instead of acting on the PC."""

    def __getattr__(self, name):
        raise AssertionError(f"a test reached the real installer.Ops.{name}; pass a fake")


def _guard_installer_ops():
    from app import installer
    installer.Ops = _NoRealInstallerOps


_guard_installer_ops()


def _isolate_speedtest_store():
    """The server reads the latest speed test from the data folder when it starts; a test must not see
    the PC's own (it would add check #14's result to the suggestions). Each Api gets an empty file."""
    from app import speedtest
    folder = tempfile.mkdtemp(prefix="si-speedtest-")
    count = [0]

    def store():
        count[0] += 1
        return speedtest.Store(speedtest.Path(folder) / f"speedtest-{count[0]}.json")
    speedtest.default_store = store


_isolate_speedtest_store()
