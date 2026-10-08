import os

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
