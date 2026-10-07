import os

# Backups go to backup.json in each test's data folder, never to the real HKLM key (ADR-0018).
os.environ["STABLEINTERNET_BACKUP"] = "file"
