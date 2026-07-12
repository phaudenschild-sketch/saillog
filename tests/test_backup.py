"""Tests für die Datensicherung (ZIP-Backup + Aufräumen)."""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from triplog import backup
from triplog.storage import LogEntry, LogbookStore


class BackupTest(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.mkdtemp()
        self._db = os.path.join(self._work, "logbook.sqlite3")
        self._cfg = os.path.join(self._work, "config.json")
        Path(self._cfg).write_text('{"boat_name": "Masarasi"}', encoding="utf-8")
        store = LogbookStore(self._db)
        store.add(LogEntry(timestamp="2026-07-10T10:00:00Z", entry_type="auto",
                           lat=43.5, lon=16.0))
        self._dest = os.path.join(self._work, "backups")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._work, ignore_errors=True)

    def test_creates_zip_with_db_and_config(self):
        out = backup.create_backup(self._db, self._cfg, self._dest, "20260710-120000")
        self.assertTrue(out.exists())
        self.assertEqual(out.name, "triplog-backup-20260710-120000.zip")
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        self.assertIn("logbook.sqlite3", names)
        self.assertIn("config.json", names)
        # kein temporäres Zwischen-DB-File hinterlassen
        self.assertFalse(list(Path(self._dest).glob(".tmp-*")))

    def test_backup_db_is_readable(self):
        out = backup.create_backup(self._db, self._cfg, self._dest, "20260710-120001")
        extract = os.path.join(self._work, "restore")
        with zipfile.ZipFile(out) as zf:
            zf.extractall(extract)
        restored = LogbookStore(os.path.join(extract, "logbook.sqlite3"))
        self.assertEqual(restored.count(), 1)

    def test_without_config(self):
        out = backup.create_backup(self._db, None, self._dest, "20260710-120002")
        with zipfile.ZipFile(out) as zf:
            self.assertEqual(zf.namelist(), ["logbook.sqlite3"])

    def test_prune_keeps_newest(self):
        for stamp in ("20260710-100000", "20260710-110000", "20260710-120000",
                      "20260710-130000"):
            backup.create_backup(self._db, self._cfg, self._dest, stamp)
        removed = backup.prune_backups(self._dest, keep=2)
        self.assertEqual(removed, 2)
        remaining = [p.name for p in backup.list_backups(self._dest)]
        self.assertEqual(remaining, [
            "triplog-backup-20260710-120000.zip",
            "triplog-backup-20260710-130000.zip",
        ])

    def test_prune_noop_when_few(self):
        backup.create_backup(self._db, self._cfg, self._dest, "20260710-140000")
        self.assertEqual(backup.prune_backups(self._dest, keep=5), 0)


if __name__ == "__main__":
    unittest.main()
