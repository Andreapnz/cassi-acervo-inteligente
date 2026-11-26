import tempfile
import unittest
import sqlite3
import hashlib
from pathlib import Path
from unittest.mock import patch
import database as database_module
from database import (all_pages, begin_attempt, clone_database, document_snapshot_fingerprint,
                      create_database_backup, finish_attempt, initialize,
                      publish_candidate_snapshot, replace_document,
                      restore_database_backup, stats, verify_database_backup)


class DatabaseTests(unittest.TestCase):
    def test_persists_document_and_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.db"
            initialize(db)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "abc", "indexed_at": "now"}, ["hello"])
            self.assertEqual(stats(db)["documents"], 1)
            self.assertEqual(stats(db)["pages"], 1)
            self.assertEqual(stats(db)["errors"], 0)
            self.assertEqual(all_pages(db)[0]["text"], "hello")

    def test_migrates_legacy_index_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE documents(path TEXT PRIMARY KEY,name TEXT NOT NULL,sha256 TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,page_count INTEGER NOT NULL,status TEXT NOT NULL,error TEXT);
                CREATE TABLE pages(id INTEGER PRIMARY KEY,document_path TEXT NOT NULL,page_number INTEGER NOT NULL,
                    text TEXT NOT NULL,UNIQUE(document_path,page_number));
                INSERT INTO documents VALUES('x.pdf','x.pdf','abc','today',1,'ok',NULL);
                INSERT INTO pages(document_path,page_number,text) VALUES('x.pdf',1,'legacy text');
            """)
            conn.commit()
            conn.close()
            initialize(db)
            self.assertEqual(all_pages(db)[0]["text"], "legacy text")
            self.assertEqual(stats(db)["indexed"], 1)
            self.assertEqual(all_pages(db)[0]["ocr_status"], "not_needed")
            self.assertEqual(all_pages(db)[0]["native_text"], "legacy text")
            self.assertEqual(all_pages(db)[0]["ocr_text"], "")

    def test_clone_database_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            candidate = Path(tmp) / "stage" / "candidate.db"
            initialize(source)
            replace_document(source, {"path": "x.pdf", "name": "x.pdf", "sha256": "abc", "indexed_at": "now"},
                             ["source snapshot"])
            before = source.read_bytes()
            clone_database(source, candidate)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(all_pages(candidate)[0]["text"], "source snapshot")

    def test_backup_records_hashes_and_passes_sqlite_integrity_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cassi.db"
            backup_dir = Path(tmp) / "CASSI-Backups"
            initialize(db)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "abc", "indexed_at": "now"},
                             ["historical snapshot"])
            before = hashlib.sha256(db.read_bytes()).hexdigest()

            metadata = create_database_backup(db, backup_dir)

            self.assertTrue(Path(metadata["backup_database"]).is_file())
            self.assertTrue(Path(metadata["backup_database"] + ".json").is_file())
            self.assertEqual(metadata["source_sha256_before"], before)
            self.assertEqual(metadata["source_sha256_after"], hashlib.sha256(db.read_bytes()).hexdigest())
            self.assertEqual(metadata["backup_sha256"], hashlib.sha256(
                Path(metadata["backup_database"]).read_bytes()).hexdigest())
            self.assertEqual(metadata["integrity_check"], "ok")
            verified = verify_database_backup(metadata["backup_database"])
            self.assertEqual(verified["backup_sha256"], metadata["backup_sha256"])
            self.assertEqual(all_pages(metadata["backup_database"])[0]["text"], "historical snapshot")
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), metadata["source_sha256_after"])

    def test_restore_recovers_full_snapshot_and_processing_history_on_temp_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cassi.db"
            backup_dir = Path(tmp) / "backups"
            initialize(db)
            path = "x.pdf"
            replace_document(db, {"path": path, "name": "x.pdf", "sha256": "v1", "indexed_at": "old"},
                             ["original page snapshot"])
            attempt = begin_attempt(db, path, "x.pdf", "v1")
            finish_attempt(db, attempt["attempt_id"], path, "v1", "indexed",
                           [{"text": "original page snapshot", "native_text": "original page snapshot"}],
                           sha256_after="v1")
            original_fingerprint = document_snapshot_fingerprint(db, path)
            historical = create_database_backup(db, backup_dir)

            replace_document(db, {"path": path, "name": "x.pdf", "sha256": "v2", "indexed_at": "new"},
                             ["new page snapshot"])
            result = restore_database_backup(historical["backup_database"], db, backup_dir)

            self.assertEqual(document_snapshot_fingerprint(db, path), original_fingerprint)
            self.assertEqual(all_pages(db)[0]["text"], "original page snapshot")
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT count(*) FROM processing_attempts").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT count(*) FROM attempt_pages").fetchone()[0], 1)
            finally:
                conn.close()
            self.assertEqual(result["integrity_check"], "ok")
            self.assertTrue(Path(result["safety_backup"]["backup_database"]).is_file())
            self.assertEqual(verify_database_backup(result["safety_backup"]["backup_database"])["integrity_check"],
                             "ok")

    def test_restore_swap_failure_keeps_current_database_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cassi.db"
            backup_dir = Path(tmp) / "backups"
            initialize(db)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "backup", "indexed_at": "old"},
                             ["backup snapshot"])
            backup = create_database_backup(db, backup_dir)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "current", "indexed_at": "new"},
                             ["current snapshot"])
            current_fingerprint = document_snapshot_fingerprint(db, "x.pdf")
            original_replace = database_module.os.replace

            def fail_when_swapping_database(source, destination):
                if Path(destination).resolve() == db.resolve():
                    raise OSError("simulated atomic replacement failure")
                return original_replace(source, destination)

            with patch("database.os.replace", side_effect=fail_when_swapping_database):
                with self.assertRaisesRegex(OSError, "simulated atomic replacement failure"):
                    restore_database_backup(backup["backup_database"], db, backup_dir)

            self.assertEqual(document_snapshot_fingerprint(db, "x.pdf"), current_fingerprint)
            self.assertEqual(all_pages(db)[0]["text"], "current snapshot")
            self.assertTrue(list(backup_dir.glob("cassi-*.db")))

    def test_post_swap_integrity_failure_recovers_from_safety_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cassi.db"
            backup_dir = Path(tmp) / "backups"
            initialize(db)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "backup", "indexed_at": "old"},
                             ["backup snapshot"])
            backup = create_database_backup(db, backup_dir)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "current", "indexed_at": "new"},
                             ["current snapshot"])
            current_fingerprint = document_snapshot_fingerprint(db, "x.pdf")
            original_check = database_module._integrity_check
            failed_target_check = False

            def fail_target_check_once(path):
                nonlocal failed_target_check
                if Path(path).resolve() == db.resolve() and not failed_target_check:
                    failed_target_check = True
                    raise sqlite3.DatabaseError("simulated post-swap integrity failure")
                return original_check(path)

            with patch("database._integrity_check", side_effect=fail_target_check_once):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "simulated post-swap"):
                    restore_database_backup(backup["backup_database"], db, backup_dir)

            self.assertTrue(failed_target_check)
            self.assertEqual(document_snapshot_fingerprint(db, "x.pdf"), current_fingerprint)
            self.assertEqual(all_pages(db)[0]["text"], "current snapshot")

    def test_candidate_publication_is_atomic_and_rejects_stale_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.db"
            candidate = Path(tmp) / "candidate.db"
            backup_dir = Path(tmp) / "backups"
            document_path = "C:/acervo/2220463185-2017.pdf"
            initialize(target)
            original_pages = ["native metadata"] * 27
            replace_document(target, {"path": document_path, "name": "2220463185-2017.pdf",
                                      "sha256": "same-pdf", "indexed_at": "old"}, original_pages)
            fingerprint = document_snapshot_fingerprint(target, document_path)
            clone_database(target, candidate)
            initialize(candidate)
            attempt = begin_attempt(candidate, document_path, "2220463185-2017.pdf", "same-pdf")
            prepared = [{"text": "native metadata", "native_text": "native metadata",
                         "extraction_method": "text", "ocr_status": "not_needed"} for _ in range(26)]
            prepared.append({"text": "native metadata bomba 346,00 m3/h",
                             "native_text": "native metadata", "ocr_text": "bomba 346,00m3/h",
                             "extraction_method": "hybrid", "ocr_status": "recognized"})
            finish_attempt(candidate, attempt["attempt_id"], document_path, "same-pdf", "indexed",
                           prepared, sha256_after="same-pdf")

            original_write_pages = database_module._write_pages
            def write_then_fail(conn, path, pages):
                original_write_pages(conn, path, pages)
                raise RuntimeError("simulated failure during atomic publication")

            with patch("database._write_pages", side_effect=write_then_fail):
                with self.assertRaises(RuntimeError):
                    publish_candidate_snapshot(candidate, target, document_path, fingerprint, backup_dir)
            self.assertEqual(document_snapshot_fingerprint(target, document_path), fingerprint)
            valid_backups = list(backup_dir.glob("cassi-*.db"))
            self.assertEqual(len(valid_backups), 1)
            self.assertEqual(verify_database_backup(valid_backups[0])["integrity_check"], "ok")

            publication = publish_candidate_snapshot(candidate, target, document_path, fingerprint, backup_dir)
            self.assertTrue(Path(publication["backup_database"]).is_file())
            self.assertEqual(len(list(backup_dir.glob("cassi-*.db"))), 2)
            page27 = next(page for page in all_pages(target) if page["page_number"] == 27)
            self.assertEqual(page27["extraction_method"], "hybrid")
            self.assertEqual(page27["native_text"], "native metadata")
            self.assertEqual(page27["ocr_text"], "bomba 346,00m3/h")
            committed_fingerprint = document_snapshot_fingerprint(target, document_path)

            with self.assertRaises(RuntimeError):
                publish_candidate_snapshot(candidate, target, document_path, "stale-fingerprint", backup_dir)
            self.assertEqual(document_snapshot_fingerprint(target, document_path), committed_fingerprint)

    def test_publication_is_aborted_if_prepublication_backup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.db"
            candidate = Path(tmp) / "candidate.db"
            backup_dir = Path(tmp) / "backups"
            document_path = "C:/acervo/2220463185-2017.pdf"
            initialize(target)
            replace_document(target, {"path": document_path, "name": "2220463185-2017.pdf",
                                      "sha256": "same-pdf", "indexed_at": "old"}, ["old snapshot"])
            fingerprint = document_snapshot_fingerprint(target, document_path)
            clone_database(target, candidate)
            initialize(candidate)
            attempt = begin_attempt(candidate, document_path, "2220463185-2017.pdf", "same-pdf")
            finish_attempt(candidate, attempt["attempt_id"], document_path, "same-pdf", "indexed",
                           [{"text": "candidate snapshot", "native_text": "candidate snapshot"}],
                           sha256_after="same-pdf")

            with patch("database.create_database_backup", side_effect=OSError("backup storage unavailable")):
                with self.assertRaisesRegex(OSError, "backup storage unavailable"):
                    publish_candidate_snapshot(candidate, target, document_path, fingerprint, backup_dir)
            self.assertEqual(document_snapshot_fingerprint(target, document_path), fingerprint)


if __name__ == "__main__":
    unittest.main()
