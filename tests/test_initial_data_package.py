"""Synthetic-only tests for the initial CASSI data payload helper."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest


HELPER_PATH = Path(__file__).resolve().parents[1] / "installer" / "prepare_initial_data.py"
SPEC = importlib.util.spec_from_file_location("prepare_initial_data", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
prepare_initial_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_initial_data)
RELOCATOR_PATH = Path(__file__).resolve().parents[1] / "initial_data.py"
RELOCATOR_SPEC = importlib.util.spec_from_file_location("cassi_initial_data", RELOCATOR_PATH)
assert RELOCATOR_SPEC is not None and RELOCATOR_SPEC.loader is not None
cassi_initial_data = importlib.util.module_from_spec(RELOCATOR_SPEC)
RELOCATOR_SPEC.loader.exec_module(cassi_initial_data)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_fixture(root: Path, mismatch_hash: bool = False) -> tuple[Path, Path, Path]:
    archive = root / "synthetic-source-archive"
    archive.mkdir()
    pdfs = {
        "synthetic-a.pdf": b"%PDF-FAKE synthetic content A",
        "synthetic-b.pdf": b"%PDF-FAKE synthetic content B",
    }
    for name, content in pdfs.items():
        (archive / name).write_bytes(content)

    database = root / "synthetic-source.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE documents (
            path TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
            indexed_sha256 TEXT, indexed_at TEXT NOT NULL, page_count INTEGER NOT NULL,
            status TEXT NOT NULL, error TEXT, duplicate_of TEXT, last_attempt_at TEXT
        );
        CREATE TABLE pages (
            id INTEGER PRIMARY KEY, document_path TEXT NOT NULL,
            page_number INTEGER NOT NULL, text TEXT NOT NULL,
            extraction_method TEXT NOT NULL, ocr_status TEXT NOT NULL,
            ocr_error TEXT, page_error TEXT, quality_warning TEXT,
            native_text TEXT NOT NULL, ocr_text TEXT NOT NULL,
            UNIQUE(document_path, page_number),
            FOREIGN KEY(document_path) REFERENCES documents(path) ON DELETE CASCADE
        );
        CREATE TABLE processing_attempts (
            id INTEGER PRIMARY KEY, document_path TEXT NOT NULL, sha256 TEXT,
            sha256_after TEXT, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, page_count INTEGER NOT NULL, error TEXT,
            warnings TEXT, previous_status TEXT, previous_error TEXT,
            previous_sha256 TEXT, had_snapshot INTEGER NOT NULL, owner_pid INTEGER
        );
        CREATE TABLE retained_metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    paths: dict[str, str] = {}
    for index, (name, content) in enumerate(pdfs.items(), start=1):
        paths[name] = str(archive / name)
        digest = _digest(archive / name)
        if mismatch_hash and index == 2:
            digest = "0" * 64
        duplicate = paths.get("synthetic-a.pdf") if index == 2 else None
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, 1, 'indexed', NULL, ?, ?) ",
            (paths[name], name, digest, digest, f"2026-01-0{index}", duplicate, f"2026-01-0{index}"),
        )
    for index, name in enumerate(pdfs, start=1):
        connection.execute(
            "INSERT INTO pages VALUES (?, ?, 1, ?, 'text', 'not_needed', NULL, NULL, NULL, ?, '')",
            (index, paths[name], f"Synthetic page text {index}", f"Native text {index}"),
        )
        connection.execute(
            "INSERT INTO processing_attempts VALUES (?, ?, ?, ?, ?, ?, 'indexed', 1, NULL, NULL, NULL, NULL, NULL, 0, 123)",
            (index, paths[name], _digest(archive / name), _digest(archive / name), "started", "finished"),
        )
    connection.execute("INSERT INTO retained_metadata VALUES ('sentinel', 'preserve me')")
    connection.commit()
    connection.close()
    return database, archive, root


def _make_certificate_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE reviews (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('version', '1');
            INSERT INTO reviews VALUES (1, 'synthetic');
        """)
        connection.commit()
    finally:
        connection.close()


class InitialDataPackageTests(unittest.TestCase):
    def test_certificate_database_is_backed_up_and_manifest_validated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cassi-certificate-db-seed-") as temporary:
            root = Path(temporary)
            database, archive, programdata = _make_fixture(root)
            certificate_database = database.with_name("certificate_analysis.db")
            _make_certificate_database(certificate_database)
            original_hash = _digest(certificate_database)
            output = root / "staging-with-certificates"

            manifest = prepare_initial_data.prepare_package(database, archive, output, programdata)

            payload = output / "CASSI"
            copied = payload / "certificate_analysis.db"
            metadata = manifest["certificate_analysis_database"]
            self.assertEqual(_digest(certificate_database), original_hash)
            self.assertEqual(_digest(copied), metadata["staged_sha256"])
            self.assertEqual(metadata["integrity_check"], "ok")
            self.assertEqual(metadata["foreign_key_check"], "ok")
            self.assertEqual(metadata["table_rows"], {"reviews": 1, "schema_meta": 1})
            cassi_initial_data._validate_certificate_database(payload, manifest)

            corrupted = bytearray(copied.read_bytes())
            corrupted[-1] ^= 1
            copied.write_bytes(corrupted)
            with self.assertRaises(cassi_initial_data.InitialDataError):
                cassi_initial_data._validate_certificate_database(payload, manifest)

    def test_backup_rebase_and_pdf_payload_preserve_synthetic_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cassi-initial-data-test-") as temporary:
            root = Path(temporary)
            database, archive, programdata = _make_fixture(root)
            original_db_hash = _digest(database)
            original_pdf_hashes = {path.name: _digest(path) for path in archive.glob("*.pdf")}
            output = root / "staging-output"

            manifest = prepare_initial_data.prepare_package(
                database, archive, output, programdata, expected_documents=2, expected_pages=2
            )

            self.assertEqual(_digest(database), original_db_hash)
            self.assertEqual(manifest["database"]["integrity_check"], "ok")
            self.assertEqual(manifest["database"]["foreign_key_check"], "ok")
            payload = output / "CASSI"
            self.assertFalse(Path(str(payload / "cassi.db") + "-wal").exists())
            self.assertFalse(Path(str(payload / "cassi.db") + "-shm").exists())
            self.assertEqual({path.name: _digest(path) for path in (payload / "Acervos").glob("*.pdf")}, original_pdf_hashes)
            self.assertEqual({entry["name"] for entry in manifest["pdfs"]}, set(original_pdf_hashes))

            copied = sqlite3.connect(payload / "cassi.db")
            try:
                copied.execute("PRAGMA foreign_keys=ON")
                self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(copied.execute("PRAGMA foreign_key_check").fetchall(), [])
                rows = copied.execute("SELECT path, name, duplicate_of FROM documents ORDER BY name").fetchall()
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(str(row[0]).startswith(str(programdata / "CASSI" / "Acervos")) for row in rows))
                self.assertEqual(Path(rows[1][2]), Path(rows[0][0]))
                self.assertEqual(copied.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 2)
                self.assertTrue(all(row[0].startswith(str(programdata / "CASSI" / "Acervos"))
                                    for row in copied.execute("SELECT document_path FROM pages")))
                self.assertTrue(all(row[0].startswith(str(programdata / "CASSI" / "Acervos"))
                                    for row in copied.execute("SELECT document_path FROM processing_attempts")))
                self.assertEqual(copied.execute("SELECT value FROM retained_metadata WHERE name='sentinel'").fetchone()[0], "preserve me")
                self.assertEqual(copied.execute("SELECT text FROM pages ORDER BY id").fetchall(),
                                 [("Synthetic page text 1",), ("Synthetic page text 2",)])
            finally:
                copied.close()

    def test_validation_failure_does_not_publish_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cassi-initial-data-fail-") as temporary:
            root = Path(temporary)
            database, archive, programdata = _make_fixture(root, mismatch_hash=True)
            output = root / "must-not-publish"
            original_db_hash = _digest(database)
            with self.assertRaises(prepare_initial_data.PackageValidationError):
                prepare_initial_data.prepare_package(database, archive, output, programdata)
            self.assertFalse(output.exists())
            self.assertEqual(_digest(database), original_db_hash)

    def test_existing_staging_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cassi-initial-data-existing-") as temporary:
            root = Path(temporary)
            database, archive, programdata = _make_fixture(root)
            output = root / "already-there"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("do not overwrite", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                prepare_initial_data.prepare_package(database, archive, output, programdata)
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not overwrite")

    def test_seed_relocates_to_two_distinct_programdata_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cassi-programdata-relocation-") as temporary:
            root = Path(temporary)
            database, archive, _ = _make_fixture(root)
            original_db_hash = _digest(database)
            build_programdata = root / "build-host" / "SharedData"
            build_programdata.mkdir(parents=True)
            seed_root = root / "seed-payload"
            prepare_initial_data.prepare_package(
                database, archive, seed_root, build_programdata,
                expected_documents=2, expected_pages=2,
            )
            seed = seed_root / "CASSI"

            installed_data_dirs = [
                build_programdata / "CASSI",
                root / "computer-a" / "ProgramData" / "CASSI",
                root / "computer-b" / "ApplicationData" / "CASSI",
            ]
            observed_paths = []
            for index, target_data in enumerate(installed_data_dirs):
                target_data.parent.mkdir(parents=True, exist_ok=True)
                target_data.mkdir(parents=True, exist_ok=True)
                shutil.copy2(seed / "cassi.db", target_data / "cassi.db")
                shutil.copy2(seed / "initial-data-manifest.json", target_data / "initial-data-manifest.json")
                shutil.copytree(seed / "Acervos", target_data / "Acervos")

                result = cassi_initial_data.relocate_initial_data(target_data)
                self.assertEqual(result["documents"], 2)
                self.assertEqual(result["pages"], 2)
                self.assertEqual(result["relocated"], index != 0)
                self.assertFalse((target_data / "initial-data-manifest.json").exists())
                copied = sqlite3.connect(target_data / "cassi.db")
                try:
                    self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(copied.execute("PRAGMA foreign_key_check").fetchall(), [])
                    paths = [row[0] for row in copied.execute("SELECT path FROM documents ORDER BY name")]
                    self.assertTrue(all(Path(path).is_relative_to(target_data / "Acervos") for path in paths))
                    self.assertEqual(copied.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 2)
                    self.assertEqual(copied.execute("SELECT value FROM retained_metadata WHERE name='sentinel'").fetchone()[0], "preserve me")
                    observed_paths.append(paths[0])
                finally:
                    copied.close()

            self.assertEqual(observed_paths[0], str(build_programdata / "CASSI" / "Acervos" / "synthetic-a.pdf"))
            self.assertNotEqual(observed_paths[1], observed_paths[2])
            self.assertEqual(_digest(database), original_db_hash)

    def test_missing_or_incompatible_seed_files_block_relocation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cassi-programdata-incomplete-") as temporary:
            root = Path(temporary)
            database, archive, _ = _make_fixture(root)
            build_programdata = root / "build-programdata"
            build_programdata.mkdir()
            seed_root = root / "seed"
            prepare_initial_data.prepare_package(database, archive, seed_root, build_programdata)
            for case in ("missing_pdf", "altered_pdf", "altered_index"):
                with self.subTest(case=case):
                    target_data = root / case / "CASSI"
                    shutil.copytree(seed_root / "CASSI", target_data)
                    if case == "missing_pdf":
                        (target_data / "Acervos" / "synthetic-b.pdf").unlink()
                    elif case == "altered_pdf":
                        (target_data / "Acervos" / "synthetic-b.pdf").write_bytes(b"incompatible synthetic PDF")
                    else:
                        changed = sqlite3.connect(target_data / "cassi.db")
                        changed.execute("UPDATE pages SET text='tampered' WHERE id=1")
                        changed.commit()
                        changed.close()
                    db_before = _digest(target_data / "cassi.db")

                    with self.assertRaises(cassi_initial_data.InitialDataError):
                        cassi_initial_data.relocate_initial_data(target_data)

                    self.assertEqual(_digest(target_data / "cassi.db"), db_before)
                    self.assertTrue((target_data / "initial-data-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
