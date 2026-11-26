"""Fase 4 tests use fictitious files and temporary SQLite databases only."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest

from certificate_analyzer import AnalysisRequest, analyze_documents
from certificate_extractor import (ExtractionReport, PageLayout, TextSpan, TableCell,
                                   TableGrid, TableRow, extract_table)
from certificate_repository import (
    CertificateRepositoryError,
    add_extracted_item,
    add_unassociated_row,
    persist_extraction_report,
    record_failed_analysis,
    connect_certificate_database,
    create_analysis,
    create_certificate_database_backup,
    get_document,
    initialize_certificate_database,
    integrity_check,
    list_extracted_items,
    list_reviews,
    list_unassociated_rows,
    record_unassociated_review,
    register_document,
    restore_certificate_database_backup,
    recover_incomplete_certificate_restore,
    validate_database_path,
    validate_original_pdf_path,
    verify_certificate_database_backup,
)
from certificate_review import approve_item, correct_item, reject_item, review_unassociated_row


class CertificateStructureTests(unittest.TestCase):
    def setUp(self):
        self._prior_backup_key = os.environ.get("CASSI_CERTIFICATE_BACKUP_HMAC_KEY")
        # Ephemeral test key: generated in memory, never logged or persisted.
        os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"] = secrets.token_hex(32)
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.pdf_root = self.base / "archive"
        self.pdf_root.mkdir()
        self.pdf = self.pdf_root / "fictional-certificate.pdf"
        self.pdf.write_bytes(b"%PDF-fictitious test fixture; not a valid or real certificate")
        self.pdf_hash = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.database = self.base / "analysis" / "certificate_analysis.db"
        self.legacy_database = self.base / "cassi.db"
        self.legacy_database.write_bytes(b"primary database sentinel")
        self.legacy_hash = hashlib.sha256(self.legacy_database.read_bytes()).hexdigest()
        self.safety = {
            "pdf_root": self.pdf_root,
            "cassi_database_path": self.legacy_database,
        }
        initialize_certificate_database(self.database, **self.safety)

    def tearDown(self):
        self.temp.cleanup()
        if self._prior_backup_key is None:
            os.environ.pop("CASSI_CERTIFICATE_BACKUP_HMAC_KEY", None)
        else:
            os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"] = self._prior_backup_key

    def _register(self, path=None, digest=None):
        source = Path(path or self.pdf)
        return register_document(
            self.database,
            filename=source.name,
            original_path=source,
            file_hash=digest or self.pdf_hash,
            page_count=4,
            document_type="digital",
            **self.safety,
        )

    def _analysis(self, document_id):
        return create_analysis(
            self.database,
            document_id=document_id,
            analysis_version="structure-only-test-v1",
            run_number=1,
            **self.safety,
        )

    def _item(self, analysis_id, dedup_key="occurrence-1"):
        return add_extracted_item(
            self.database,
            analysis_id=analysis_id,
            page_number=2,
            service_description_original="Serviço fictício de pavimentação",
            quantity_original="1.234,50",
            unit_original="m²",
            quantity_normalized="1234.50",
            unit_normalized="m²",
            evidence_text="Linha fictícia: serviço 1.234,50 m²",
            evidence_coordinates={"x0": 10.0, "y0": 20.0, "x1": 80.0, "y1": 30.0},
            field_coordinates={"service": {"x0": 10.0, "y0": 20.0, "x1": 50.0, "y1": 30.0},
                               "service_segments": [{"row_number": 3, "text": "Servico ficticio",
                                                     "coordinates": {"x0": 10.0, "y0": 20.0, "x1": 50.0, "y1": 30.0}}]},
            page_width=100.0,
            page_height=100.0,
            source_method="native",
            reading_confidence="high",
            interpretation_confidence="medium",
            dedup_key=dedup_key,
            source_row_number=3,
            review_reasons=("quantidade pendente de confer?ncia",),
            **self.safety,
        )

    def test_01_creates_independent_database(self):
        self.assertTrue(self.database.is_file())
        self.assertNotEqual(self.database.resolve(), self.legacy_database.resolve())
        self.assertEqual(hashlib.sha256(self.legacy_database.read_bytes()).hexdigest(), self.legacy_hash)

    def test_02_creates_required_tables_indexes_and_triggers(self):
        with connect_certificate_database(self.database, **self.safety) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertTrue({"documents", "analyses", "extracted_items", "reviews",
                             "unassociated_rows", "unassociated_row_reviews"}.issubset(tables))
            indexes = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
            self.assertIn("idx_certificate_items_analysis_page", indexes)
            triggers = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )}
            self.assertIn("trg_reviews_no_update", triggers)
            self.assertIn("trg_extracted_source_immutable", triggers)
            self.assertIn("trg_extracted_items_no_delete", triggers)

    def test_03_foreign_keys_are_enabled_and_declared(self):
        with connect_certificate_database(self.database, **self.safety) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertTrue(conn.execute("PRAGMA foreign_key_list(analyses)").fetchall())
            self.assertTrue(conn.execute("PRAGMA foreign_key_list(extracted_items)").fetchall())
            self.assertTrue(conn.execute("PRAGMA foreign_key_list(reviews)").fetchall())

    def test_04_inserts_and_reads_fictional_document_analysis_and_item(self):
        doc = self._register()
        analysis_id = self._analysis(doc["id"])
        item_id = self._item(analysis_id)
        self.assertEqual(get_document(self.database, doc["id"], **self.safety)["file_hash"], self.pdf_hash)
        rows = list_extracted_items(self.database, analysis_id, **self.safety)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], item_id)
        self.assertEqual(rows[0]["quantity_original"], "1.234,50")
        self.assertEqual(rows[0]["quantity_normalized"], "1234.50")
        self.assertEqual(rows[0]["reading_confidence"], "high")
        self.assertEqual(rows[0]["interpretation_confidence"], "medium")
        self.assertEqual(json.loads(rows[0]["evidence_coordinates"])["x0"], 10.0)
        self.assertEqual(json.loads(rows[0]["field_coordinates"])["service_segments"][0]["row_number"], 3)
        self.assertEqual(rows[0]["source_row_number"], 3)
        self.assertEqual(json.loads(rows[0]["review_reasons"]), ["quantidade pendente de confer?ncia"])

    def test_05_integrity_referential_constraints_reject_orphan_rows(self):
        with connect_certificate_database(self.database, **self.safety) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO analyses(document_id,analysis_version,run_number,started_at) VALUES(999,'x',1,'now')"
                )
        self.assertEqual(integrity_check(self.database, **self.safety), "ok")

    def test_06_reviews_are_append_only_and_corrections_preserve_original_values(self):
        doc = self._register()
        analysis_id = self._analysis(doc["id"])
        item_id = self._item(analysis_id)
        correct_item(self.database, item_id, "revisor fictício",
                     previous_value={"quantity": "1.234,50"},
                     corrected_value={"quantity": "1.234,00"}, **self.safety)
        approve_item(self.database, item_id, "segundo revisor", **self.safety)
        events = list_reviews(self.database, item_id, **self.safety)
        self.assertEqual([event["action"] for event in events], ["correct", "approve"])
        self.assertEqual(json.loads(events[0]["corrected_value"])["quantity"], "1.234,00")
        self.assertEqual(list_extracted_items(self.database, analysis_id, **self.safety)[0]["quantity_original"], "1.234,50")
        with connect_certificate_database(self.database, **self.safety) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE reviews SET notes='alterado' WHERE id=?", (events[0]["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM reviews WHERE id=?", (events[0]["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE extracted_items SET quantity_original='999' WHERE id=?", (item_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM extracted_items WHERE id=?", (item_id,))

    def test_reject_action_updates_status_without_removing_history(self):
        doc = self._register()
        analysis_id = self._analysis(doc["id"])
        item_id = self._item(analysis_id)
        reject_item(self.database, item_id, "revisor fictício", "evidência insuficiente", **self.safety)
        self.assertEqual(list_extracted_items(self.database, analysis_id, **self.safety)[0]["review_status"], "rejected")
        self.assertEqual(len(list_reviews(self.database, item_id, **self.safety)), 1)

    def test_07_prevents_duplicate_file_versions_and_duplicate_items(self):
        first = self._register()
        same = self._register()
        self.assertEqual(first["id"], same["id"])
        copy = self.pdf_root / "identical-copy.pdf"
        copy.write_bytes(self.pdf.read_bytes())
        second_path = self._register(path=copy)
        self.assertNotEqual(first["id"], second_path["id"])
        analysis_id = self._analysis(first["id"])
        self._item(analysis_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self._item(analysis_id)

    def test_08_validates_pdf_and_database_paths(self):
        self.assertEqual(validate_original_pdf_path(self.pdf, pdf_root=self.pdf_root), self.pdf.resolve())
        outside = self.base / "outside.pdf"
        outside.touch()
        with self.assertRaises(CertificateRepositoryError):
            validate_original_pdf_path(outside, pdf_root=self.pdf_root)
        wrong_type = self.pdf_root / "not-a-pdf.txt"
        wrong_type.touch()
        with self.assertRaises(CertificateRepositoryError):
            validate_original_pdf_path(wrong_type, pdf_root=self.pdf_root)
        with self.assertRaises(CertificateRepositoryError):
            validate_database_path(self.pdf_root / "unsafe.db", pdf_root=self.pdf_root,
                                   cassi_database_path=self.legacy_database)
        with self.assertRaises(CertificateRepositoryError):
            validate_database_path(self.legacy_database, pdf_root=self.pdf_root,
                                   cassi_database_path=self.legacy_database)

    def test_09_refuses_unknown_existing_database(self):
        unknown = self.base / "unknown.db"
        unknown.write_bytes(b"not the certificate-analysis database")
        with self.assertRaises(CertificateRepositoryError):
            initialize_certificate_database(unknown, **self.safety)

    def test_10_original_pdf_fixture_is_not_modified_by_repository_operations(self):
        before = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        before_stat = self.pdf.stat()
        document = self._register()
        analysis_id = self._analysis(document["id"])
        self._item(analysis_id)
        after = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        after_stat = self.pdf.stat()
        self.assertEqual(before, after)
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(hashlib.sha256(self.legacy_database.read_bytes()).hexdigest(), self.legacy_hash)

    def test_analyzer_runs_only_explicit_synthetic_pdf_and_persists_traceability(self):
        import pymupdf
        synthetic = self.pdf_root / "synthetic.pdf"
        document = pymupdf.open()
        page = document.new_page(width=300, height=200)
        page.insert_text((20, 30), "Pavimentacao 12,50 m2")
        page.insert_text((20, 60), "2,00 m2")
        document.save(synthetic)
        document.close()
        before = hashlib.sha256(synthetic.read_bytes()).hexdigest()
        result = analyze_documents(AnalysisRequest(
            (synthetic,), "synthetic-v1", self.database, self.pdf_root, self.legacy_database))
        self.assertEqual(result[0].status, "completed")
        self.assertEqual(hashlib.sha256(synthetic.read_bytes()).hexdigest(), before)
        with connect_certificate_database(self.database, **self.safety) as conn:
            analysis_id = result[0].analysis_id
            item = conn.execute("SELECT * FROM extracted_items WHERE analysis_id=?", (analysis_id,)).fetchone()
            self.assertIsNotNone(item)
            self.assertEqual(item["page_number"], 1)
            self.assertIn("Pavimentacao", item["evidence_text"])
            doc = conn.execute("SELECT original_path,file_hash FROM documents WHERE id=?",
                               (result[0].document_id,)).fetchone()
            self.assertEqual(doc["original_path"], str(synthetic.resolve()))
            self.assertEqual(doc["file_hash"], before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_pages WHERE analysis_id=?",
                                          (analysis_id,)).fetchone()[0], 1)
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM analysis_diagnostics WHERE analysis_id=?",
                                                 (analysis_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM unassociated_rows WHERE analysis_id=?",
                                          (analysis_id,)).fetchone()[0], 1)
            warnings_json = conn.execute("SELECT warnings_json FROM analyses WHERE id=?",
                                         (analysis_id,)).fetchone()[0]
            self.assertIn("tabela", warnings_json)
        self.assertEqual(TextSpan("ficticio", (0.0, 0.0, 1.0, 1.0), "native").source, "native")
        self.assertEqual(PageLayout(1, "native", (), ()).page_number, 1)

    def test_failed_page_then_retry_has_no_partial_or_duplicate_items(self):
        import pymupdf
        import certificate_analyzer
        from unittest.mock import patch
        synthetic = self.pdf_root / "retry.pdf"
        document = pymupdf.open()
        for page_index in range(2):
            page = document.new_page(width=300, height=200)
            page.insert_text((20, 30), f"Servico {page_index + 1} 3,00 m")
        document.save(synthetic)
        document.close()
        real_extract = certificate_analyzer.extract_page
        def fail_second(page, *, page_number, **kwargs):
            if page_number == 2:
                raise RuntimeError("synthetic page failure")
            return real_extract(page, page_number=page_number, **kwargs)
        request = AnalysisRequest((synthetic,), "retry-v1", self.database, self.pdf_root, self.legacy_database)
        with patch("certificate_analyzer.extract_page", side_effect=fail_second):
            first = analyze_documents(request)[0]
        self.assertEqual(first.status, "failed")
        with connect_certificate_database(self.database, **self.safety) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM extracted_items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT status FROM analyses WHERE id=?", (first.analysis_id,)).fetchone()[0], "failed")
        second = analyze_documents(request)[0]
        self.assertEqual(second.status, "completed")
        with connect_certificate_database(self.database, **self.safety) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents WHERE original_path=?",
                                          (str(synthetic.resolve()),)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM analyses WHERE document_id=(SELECT id FROM documents WHERE original_path=?)",
                                          (str(synthetic.resolve()),)).fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM extracted_items WHERE analysis_id=?",
                                          (second.analysis_id,)).fetchone()[0], 2)

    def test_repository_rejects_bad_or_out_of_bounds_coordinates(self):
        doc = self._register()
        analysis_id = self._analysis(doc["id"])
        base = dict(database_path=self.database, analysis_id=analysis_id, page_number=2, service_description_original="Ficticio",
                    quantity_original="1,00", unit_original="m", quantity_normalized="1.00",
                    unit_normalized="m", evidence_text="Ficticio 1,00 m", source_method="native",
                    reading_confidence="high", interpretation_confidence="high", **self.safety)
        bad_boxes = [
            {"x0": -1, "y0": 0, "x1": 10, "y1": 10},
            {"x0": 0, "y0": 0, "x1": float("inf"), "y1": 10},
            {"x0": 0, "y0": 0, "x1": 101, "y1": 10},
            {"x0": 20, "y0": 0, "x1": 10, "y1": 10},
            [0, 0, 10, 10],
        ]
        for index, box in enumerate(bad_boxes):
            with self.subTest(box=box), self.assertRaises(CertificateRepositoryError):
                add_extracted_item(**base, dedup_key=f"bad-{index}", evidence_coordinates=box,
                                   page_width=100, page_height=100)
        with self.assertRaises(CertificateRepositoryError):
            add_extracted_item(**base, dedup_key="missing-bounds",
                               evidence_coordinates={"x0": 0, "y0": 0, "x1": 10, "y1": 10})
        with connect_certificate_database(self.database, **self.safety) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM extracted_items").fetchone()[0], 0)

    def test_v1_migration_preserves_rows_and_legacy_confidence_scores(self):
        legacy = self.base / "migration" / "certificate_analysis.db"
        legacy.parent.mkdir()
        conn = sqlite3.connect(legacy)
        conn.executescript("""
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES('schema_version','1');
            CREATE TABLE documents(id INTEGER PRIMARY KEY,filename TEXT NOT NULL,original_path TEXT NOT NULL,
                file_hash TEXT NOT NULL CHECK(length(file_hash)=64),page_count INTEGER NOT NULL CHECK(page_count >= 0),
                document_type TEXT NOT NULL CHECK(document_type IN ('digital','scanned','hybrid','unknown')),created_at TEXT NOT NULL,
                UNIQUE(original_path,file_hash));
            CREATE TABLE analyses(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,analysis_version TEXT NOT NULL,
                run_number INTEGER NOT NULL CHECK(run_number > 0),status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled')),
                started_at TEXT NOT NULL,completed_at TEXT,error TEXT,
                UNIQUE(document_id,run_number));
            CREATE TABLE extracted_items(id INTEGER PRIMARY KEY,analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT,page_number INTEGER NOT NULL CHECK(page_number > 0),
                item_type TEXT NOT NULL CHECK(item_type IN ('service','header','subtotal','total','other')),service_description_original TEXT NOT NULL,quantity_original TEXT,unit_original TEXT,
                quantity_normalized TEXT,unit_normalized TEXT,evidence_text TEXT NOT NULL,evidence_coordinates TEXT NOT NULL,
                source_method TEXT NOT NULL CHECK(source_method IN ('native','ocr','hybrid','unknown')),reading_confidence REAL NOT NULL,interpretation_confidence REAL NOT NULL,
                review_status TEXT NOT NULL CHECK(review_status IN ('pending','approved','rejected','corrected')),dedup_key TEXT NOT NULL,UNIQUE(analysis_id,page_number,dedup_key));
            CREATE TABLE reviews(id INTEGER PRIMARY KEY,extracted_item_id INTEGER NOT NULL REFERENCES extracted_items(id) ON DELETE RESTRICT,
                action TEXT NOT NULL CHECK(action IN ('approve','reject','correct')),
                previous_value TEXT,corrected_value TEXT,reviewer TEXT NOT NULL,review_date TEXT NOT NULL,notes TEXT);
            INSERT INTO documents VALUES(1,'fake.pdf','C:/synthetic/fake.pdf',printf('%064d',1),1,'digital','now');
            INSERT INTO analyses VALUES(1,1,'v1',1,'completed','now','now',NULL);
            INSERT INTO extracted_items VALUES(1,1,1,'service','Synthetic service','2,00','m','2.00','m',
                'Synthetic service 2,00 m','{}','native',0.75,0.55,'pending','old-key');
            INSERT INTO reviews VALUES(1,1,'correct','{"quantity":"2,00"}','{"quantity":"2.10"}',
                'reviewer synthetic','2026-01-01T00:00:00Z','legacy history');
        """)
        conn.commit(); conn.close()
        safety = {"pdf_root": self.pdf_root, "cassi_database_path": self.legacy_database}
        initialize_certificate_database(legacy, backup_directory=self.base / "v1-migration-backups", **safety)
        migrated = sqlite3.connect(legacy)
        try:
            migrated.row_factory = sqlite3.Row
            row = migrated.execute("SELECT * FROM extracted_items WHERE id=1").fetchone()
            self.assertEqual(migrated.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "3")
            self.assertEqual(row["reading_confidence_legacy_score"], 0.75)
            self.assertEqual(row["interpretation_confidence_legacy_score"], 0.55)
            self.assertEqual(row["reading_confidence"], "not_assessed")
            self.assertEqual(row["service_description_original"], "Synthetic service")
            self.assertEqual(migrated.execute("SELECT notes FROM reviews WHERE id=1").fetchone()[0], "legacy history")
            self.assertEqual(migrated.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            migrated.close()

    def test_unassociated_evidence_and_review_reason_are_persisted(self):
        doc = self._register()
        analysis_id = self._analysis(doc["id"])
        row_id = add_unassociated_row(
            self.database, analysis_id=analysis_id, page_number=2, row_number=8,
            evidence_text="2,00 m2 sem descricao", evidence_coordinates={"x0": 1, "y0": 2, "x1": 3, "y1": 4},
            page_width=100, page_height=100, source_method="native",
            reason="sem associacao estrutural", dedup_key="row-eight",
            **self.safety)
        duplicate = list_unassociated_rows(self.database, analysis_id, **self.safety)
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0]["id"], row_id)
        event_id = review_unassociated_row(
            self.database, row_id, "correct", "revisora sintetica",
            previous_value={"service": None}, corrected_value={"service": "Pavimentacao"}, **self.safety)
        self.assertGreater(event_id, 0)
        with connect_certificate_database(self.database, **self.safety) as conn:
            status = conn.execute("SELECT review_status FROM unassociated_rows WHERE id=?", (row_id,)).fetchone()[0]
            self.assertEqual(status, "corrected")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE unassociated_row_reviews SET notes='changed' WHERE id=?", (event_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE unassociated_rows SET evidence_text='changed' WHERE id=?", (row_id,))

    def test_complete_report_persistence_is_atomic_and_idempotent(self):
        from certificate_extractor import extract_table, TableGrid, TableRow, TableCell
        synthetic = self.pdf_root / "report.pdf"
        synthetic.write_bytes(b"%PDF synthetic-only persistence metadata")
        digest = hashlib.sha256(synthetic.read_bytes()).hexdigest()
        grid = TableGrid((
            TableRow((TableCell("Descricao", (0, 0, 20, 10)),
                      TableCell("Quantidade", (20, 0, 40, 10)),
                      TableCell("Unidade", (40, 0, 60, 10))), 1),
            TableRow((TableCell("Servico sintetico", (0, 10, 20, 20)),
                      TableCell("5,00", (20, 10, 40, 20)),
                      TableCell("m", (40, 10, 60, 20))), 2),
            TableRow((TableCell("", (0, 20, 20, 30)), TableCell("7,00", (20, 20, 40, 30)),
                      TableCell("m", (40, 20, 60, 30))), 3),
        ))
        report = extract_table(grid, page_number=1, source_method="native", document_path=str(synthetic))
        report = ExtractionReport(report.items, report.ignored_rows, report.duplicate_rows,
                                  ("aviso sintetico",), report.unassociated_rows)
        kwargs = dict(
            database_path=self.database, filename=synthetic.name, original_path=synthetic,
            file_hash=digest, page_count=1, document_type="digital", analysis_version="atomic-v1",
            run_number=1, report=report,
            page_metadata={1: {"width": 100, "height": 100, "source_method": "native",
                               "warnings": ["aviso sintetico"]}}, **self.safety)
        first = persist_extraction_report(**kwargs)
        second = persist_extraction_report(**kwargs)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["analysis_id"], second["analysis_id"])
        with connect_certificate_database(self.database, **self.safety) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM extracted_items WHERE analysis_id=?",
                                          (first["analysis_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM unassociated_rows WHERE analysis_id=?",
                                          (first["analysis_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_diagnostics WHERE analysis_id=? AND kind='warning'",
                                          (first["analysis_id"],)).fetchone()[0], 1)

    def test_item_traceability_rejects_evidence_from_another_document(self):
        doc = self._register()
        from certificate_extractor import extract_table, TableGrid, TableRow, TableCell
        report = extract_table(TableGrid((TableRow((
            TableCell("Descricao", (0, 0, 10, 10)), TableCell("Quantidade", (10, 0, 20, 10)),
            TableCell("Unidade", (20, 0, 30, 10))), 1), TableRow((
            TableCell("Servico", (0, 10, 10, 20)), TableCell("1,00", (10, 10, 20, 20)),
            TableCell("m", (20, 10, 30, 20))), 2))), page_number=1,
            source_method="native", document_path=str(self.pdf_root / "other.pdf"))
        kwargs = dict(filename=self.pdf.name, original_path=self.pdf, file_hash=self.pdf_hash,
                      page_count=4, document_type="digital", analysis_version="trace-v1", run_number=1,
                      report=report, page_metadata={1: {"width": 100, "height": 100,
                      "source_method": "native", "warnings": []}}, **self.safety)
        with self.assertRaises(CertificateRepositoryError):
            persist_extraction_report(self.database, **kwargs)
        with connect_certificate_database(self.database, **self.safety) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM analyses WHERE document_id=?", (doc["id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM extracted_items").fetchone()[0], 0)

    def test_v2_migration_adds_field_evidence_without_losing_qualitative_values(self):
        legacy = self.base / "migration-v2" / "certificate_analysis.db"
        legacy.parent.mkdir()
        conn = sqlite3.connect(legacy)
        conn.executescript("""
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES('schema_version','2');
            PRAGMA user_version=2;
            CREATE TABLE documents(id INTEGER PRIMARY KEY,filename TEXT NOT NULL,original_path TEXT NOT NULL,
                file_hash TEXT NOT NULL CHECK(length(file_hash)=64),page_count INTEGER NOT NULL CHECK(page_count >= 0),
                document_type TEXT NOT NULL CHECK(document_type IN ('digital','scanned','hybrid','unknown')),created_at TEXT NOT NULL,
                UNIQUE(original_path,file_hash));
            CREATE TABLE analyses(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,analysis_version TEXT NOT NULL,
                run_number INTEGER NOT NULL CHECK(run_number > 0),status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled')),
                started_at TEXT NOT NULL,completed_at TEXT,error TEXT,
                UNIQUE(document_id,run_number));
            CREATE TABLE extracted_items(id INTEGER PRIMARY KEY,analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT,page_number INTEGER NOT NULL CHECK(page_number > 0),
                source_row_number INTEGER NOT NULL CHECK(source_row_number >= 0),item_type TEXT NOT NULL CHECK(item_type IN ('service','header','subtotal','total','other')),service_description_original TEXT NOT NULL,
                quantity_original TEXT,unit_original TEXT,quantity_normalized TEXT,unit_normalized TEXT,
                evidence_text TEXT NOT NULL,evidence_coordinates TEXT NOT NULL,
                source_method TEXT NOT NULL CHECK(source_method IN ('native','ocr','hybrid','unknown')),
                reading_confidence TEXT NOT NULL CHECK(reading_confidence IN ('not_assessed','low','medium','high')),
                interpretation_confidence TEXT NOT NULL CHECK(interpretation_confidence IN ('not_assessed','low','medium','high')),
                reading_confidence_legacy_score REAL,interpretation_confidence_legacy_score REAL,
                review_reasons TEXT NOT NULL,review_status TEXT NOT NULL CHECK(review_status IN ('pending','approved','rejected','corrected')),dedup_key TEXT NOT NULL,
                UNIQUE(analysis_id,page_number,dedup_key));
            CREATE TABLE reviews(id INTEGER PRIMARY KEY,extracted_item_id INTEGER NOT NULL REFERENCES extracted_items(id) ON DELETE RESTRICT,
                action TEXT NOT NULL CHECK(action IN ('approve','reject','correct')),
                previous_value TEXT,corrected_value TEXT,reviewer TEXT NOT NULL,review_date TEXT NOT NULL,notes TEXT);
            CREATE TRIGGER trg_extracted_source_immutable
                BEFORE UPDATE OF analysis_id,page_number,item_type,service_description_original,
                    quantity_original,unit_original,quantity_normalized,unit_normalized,evidence_text,
                    evidence_coordinates,source_method,reading_confidence,interpretation_confidence,
                    reading_confidence_legacy_score,interpretation_confidence_legacy_score,
                    review_reasons,source_row_number,dedup_key
                ON extracted_items BEGIN SELECT RAISE(ABORT,'immutable v2 source'); END;
            INSERT INTO documents VALUES(1,'fake.pdf','C:/synthetic-v2/fake.pdf',printf('%064d',2),1,'digital','now');
            INSERT INTO analyses VALUES(1,1,'v2',1,'completed','now','now',NULL);
            INSERT INTO extracted_items VALUES(1,1,1,4,'service','V2 synthetic','3,00','m','3.00','m',
                'V2 synthetic 3,00 m','{}','native','medium','high',0.75,0.95,'["revise"]','pending','v2-key');
            INSERT INTO reviews VALUES(1,1,'approve',NULL,NULL,'reviewer synthetic','2026-01-02T00:00:00Z','v2 history');
        """)
        conn.commit()
        conn.close()
        initialize_certificate_database(legacy, pdf_root=self.pdf_root,
                                        cassi_database_path=self.legacy_database,
                                        backup_directory=self.base / "v2-migration-backups")
        migrated = sqlite3.connect(legacy)
        try:
            migrated.row_factory = sqlite3.Row
            row = migrated.execute("SELECT * FROM extracted_items WHERE id=1").fetchone()
            self.assertEqual(migrated.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "3")
            self.assertEqual(row["reading_confidence"], "medium")
            self.assertEqual(row["interpretation_confidence"], "high")
            self.assertEqual(row["reading_confidence_legacy_score"], 0.75)
            self.assertEqual(row["field_coordinates"], "{}")
            self.assertEqual(migrated.execute("SELECT warnings_json FROM analyses WHERE id=1").fetchone()[0], "[]")
            self.assertEqual(migrated.execute("SELECT notes FROM reviews WHERE id=1").fetchone()[0], "v2 history")
            trigger_sql = migrated.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_extracted_source_immutable'"
            ).fetchone()[0]
            self.assertIn("field_coordinates", trigger_sql)
            for column in (
                "analysis_id", "page_number", "item_type", "service_description_original",
                "quantity_original", "unit_original", "quantity_normalized", "unit_normalized",
                "evidence_text", "evidence_coordinates", "field_coordinates", "source_method",
                "reading_confidence", "interpretation_confidence", "reading_confidence_legacy_score",
                "interpretation_confidence_legacy_score", "review_reasons", "source_row_number", "dedup_key",
            ):
                with self.subTest(immutable_column=column), self.assertRaises(sqlite3.IntegrityError):
                    migrated.execute(f"UPDATE extracted_items SET {column}={column} WHERE id=1")
            self.assertEqual(migrated.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            migrated.close()

    def test_v2_migration_creates_validated_backup_before_upgrading(self):
        from unittest.mock import patch
        import certificate_repository

        legacy = self.base / "backup-v2" / "certificate_analysis.db"
        legacy.parent.mkdir()
        self._create_v2_fixture(legacy)
        backup_dir = self.base / "migration-backups"
        with patch.object(certificate_repository, "DATABASE_BACKUP_DIR", backup_dir):
            initialize_certificate_database(
                legacy, pdf_root=self.pdf_root, cassi_database_path=self.legacy_database,
            )
        backups = list(backup_dir.glob("certificate_analysis_pre_v3_*.db"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(Path(str(backups[0]) + ".json").is_file())
        verified = verify_certificate_database_backup(backups[0])
        self.assertEqual(verified["size"], backups[0].stat().st_size)
        self.assertEqual(verified["integrity_check"], "ok")
        backup = sqlite3.connect(backups[0].as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "2")
            self.assertEqual(backup.execute("SELECT service_description_original FROM extracted_items WHERE id=1").fetchone()[0], "V2 synthetic")
        finally:
            backup.close()
        migrated = sqlite3.connect(legacy)
        try:
            self.assertEqual(migrated.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "3")
        finally:
            migrated.close()

    def test_certificate_backup_is_unique_validated_and_does_not_change_source(self):
        doc = self._register()
        analysis_id = self._analysis(doc["id"])
        self._item(analysis_id)
        source_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        backup_dir = self.base / "certificate-backups"
        first = create_certificate_database_backup(
            self.database, backup_directory=backup_dir, **self.safety,
        )
        second = create_certificate_database_backup(
            self.database, backup_directory=backup_dir, **self.safety,
        )
        self.assertNotEqual(first["backup_path"], second["backup_path"])
        self.assertEqual(first["integrity_check"], "ok")
        self.assertEqual(first["size"], Path(first["backup_path"]).stat().st_size)
        self.assertEqual(first["sha256"], hashlib.sha256(Path(first["backup_path"]).read_bytes()).hexdigest())
        self.assertEqual(first["schema_version"], 3)
        manifest_text = Path(first["manifest_path"]).read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertRegex(manifest["hmac_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn(os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"], manifest_text)
        self.assertEqual(verify_certificate_database_backup(first["backup_path"])["sha256"], first["sha256"])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(hashlib.sha256(self.legacy_database.read_bytes()).hexdigest(), self.legacy_hash)

    def test_certificate_backup_restores_to_copy_and_preserves_current_target(self):
        original_doc = self._register()
        original_analysis = self._analysis(original_doc["id"])
        self._item(original_analysis)
        backup_dir = self.base / "restore-backups"
        backup = create_certificate_database_backup(
            self.database, backup_directory=backup_dir, **self.safety,
        )
        with connect_certificate_database(self.database, **self.safety) as source_conn:
            expected_description = source_conn.execute(
                "SELECT service_description_original FROM extracted_items"
            ).fetchone()[0]
        original_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()

        target = self.base / "restore-copy.db"
        initialize_certificate_database(target, **self.safety)
        second_pdf = self.pdf_root / "other-fictional.pdf"
        second_pdf.write_bytes(b"synthetic only, not a real document")
        second_hash = hashlib.sha256(second_pdf.read_bytes()).hexdigest()
        register_document(
            target, filename=second_pdf.name, original_path=second_pdf, file_hash=second_hash,
            page_count=1, document_type="digital", **self.safety,
        )
        before_target_hash = hashlib.sha256(target.read_bytes()).hexdigest()

        result = restore_certificate_database_backup(
            backup["backup_path"], target, backup_directory=backup_dir, **self.safety,
        )
        self.assertEqual(result["integrity_check"], "ok")
        self.assertEqual(result["schema_version"], 3)
        self.assertIsNotNone(result["safety_backup_path"])
        self.assertTrue(Path(result["safety_backup_path"]).is_file())
        self.assertEqual(verify_certificate_database_backup(result["safety_backup_path"])["integrity_check"], "ok")
        with connect_certificate_database(target, **self.safety) as restored:
            self.assertEqual([row[0] for row in restored.execute("SELECT filename FROM documents")], [self.pdf.name])
            self.assertEqual(restored.execute("SELECT service_description_original FROM extracted_items").fetchone()[0],
                             expected_description)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), original_hash)
        self.assertNotEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before_target_hash)
        self.assertEqual(hashlib.sha256(self.legacy_database.read_bytes()).hexdigest(), self.legacy_hash)

    def test_corrupted_certificate_backup_is_rejected_without_changing_database(self):
        doc = self._register()
        self._analysis(doc["id"])
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "corrupt-backups", **self.safety,
        )
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with Path(backup["backup_path"]).open("ab") as stream:
            stream.write(b"synthetic corruption")
        with self.assertRaisesRegex(CertificateRepositoryError, "Tamanho|SHA-256"):
            verify_certificate_database_backup(backup["backup_path"])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_restore_rejects_incompatible_schema_backup_without_replacing_target(self):
        old_db = self.base / "old-v2.db"
        self._create_v2_fixture(old_db)
        backup = create_certificate_database_backup(
            old_db, backup_directory=self.base / "old-version-backups", **self.safety,
        )
        target = self.base / "current-v3.db"
        initialize_certificate_database(target, **self.safety)
        original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaises(CertificateRepositoryError):
            restore_certificate_database_backup(
                backup["backup_path"], target, backup_directory=self.base / "version-restore-backups",
                **self.safety,
            )
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), original_hash)

    def _rewrite_backup_manifest_after_test_mutation(self, backup_path):
        backup_path = Path(backup_path)
        manifest_path = Path(str(backup_path) + ".json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["backup_size"] = backup_path.stat().st_size
        manifest["backup_sha256"] = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        manifest["integrity_check"] = "ok"
        unsigned = {name: value for name, value in manifest.items() if name != "hmac_sha256"}
        payload = json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        manifest["hmac_sha256"] = hmac.new(
            bytes.fromhex(os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"]),
            payload, hashlib.sha256,
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_backup_operations_fail_closed_without_external_hmac_key(self):
        backup_dir = self.base / "missing-auth-key"
        signed_backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "signed-before-key-removal", **self.safety,
        )
        os.environ.pop("CASSI_CERTIFICATE_BACKUP_HMAC_KEY", None)
        with self.assertRaisesRegex(CertificateRepositoryError, "CASSI_CERTIFICATE_BACKUP_HMAC_KEY"):
            create_certificate_database_backup(
                self.database, backup_directory=backup_dir, **self.safety,
            )
        with self.assertRaisesRegex(CertificateRepositoryError, "CASSI_CERTIFICATE_BACKUP_HMAC_KEY"):
            verify_certificate_database_backup(signed_backup["backup_path"])
        self.assertFalse(backup_dir.exists())

    def test_coordinated_database_and_manifest_replacement_without_key_is_rejected(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "coordinated-replacement", **self.safety,
        )
        backup_file = Path(backup["backup_path"])
        manifest_path = Path(backup["manifest_path"])
        original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        data = bytearray(backup_file.read_bytes())
        data[-128] ^= 1
        backup_file.write_bytes(data)
        coordinated = dict(original_manifest)
        coordinated["backup_size"] = backup_file.stat().st_size
        coordinated["backup_sha256"] = hashlib.sha256(backup_file.read_bytes()).hexdigest()
        coordinated["hmac_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(coordinated), encoding="utf-8")
        with self.assertRaisesRegex(CertificateRepositoryError, "HMAC"):
            verify_certificate_database_backup(backup_file)

    def test_altered_manifest_signature_is_rejected(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "altered-signed-manifest", **self.safety,
        )
        manifest_path = Path(backup["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = 2
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CertificateRepositoryError, "HMAC"):
            verify_certificate_database_backup(backup["backup_path"])

    def test_backup_from_another_key_is_rejected(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "wrong-key", **self.safety,
        )
        replacement_key = secrets.token_hex(32)
        while replacement_key == os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"]:
            replacement_key = secrets.token_hex(32)
        os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"] = replacement_key
        with self.assertRaisesRegex(CertificateRepositoryError, "HMAC"):
            verify_certificate_database_backup(backup["backup_path"])

    def test_malformed_backup_key_is_rejected_without_creating_backup(self):
        backup_dir = self.base / "malformed-auth-key"
        os.environ["CASSI_CERTIFICATE_BACKUP_HMAC_KEY"] = "not-a-valid-hex-key"
        with self.assertRaisesRegex(CertificateRepositoryError, "64 caracteres hexadecimais"):
            create_certificate_database_backup(
                self.database, backup_directory=backup_dir, **self.safety,
            )
        self.assertFalse(backup_dir.exists())

    def test_same_size_corruption_rejected_by_sha_without_touching_target(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "same-size-corrupt", **self.safety,
        )
        target = self.base / "same-size-target.db"
        initialize_certificate_database(target, **self.safety)
        target_before = hashlib.sha256(target.read_bytes()).hexdigest()
        backup_file = Path(backup["backup_path"])
        data = bytearray(backup_file.read_bytes())
        data[-128] ^= 0x01
        backup_file.write_bytes(data)
        self.assertEqual(backup_file.stat().st_size, backup["size"])
        with self.assertRaisesRegex(CertificateRepositoryError, "SHA-256"):
            restore_certificate_database_backup(backup_file, target, **self.safety)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), target_before)

    def test_manifest_tampering_rejected_without_touching_target(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "manifest-tamper", **self.safety,
        )
        target = self.base / "manifest-target.db"
        initialize_certificate_database(target, **self.safety)
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest_path = Path(backup["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["backup_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(CertificateRepositoryError):
            restore_certificate_database_backup(backup["backup_path"], target, backup_directory=self.base / "restore-safety", **self.safety)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)

    def test_manifest_consistent_but_schema_meta_user_version_mismatch_is_rejected(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "version-mismatch", **self.safety,
        )
        backup_file = Path(backup["backup_path"])
        conn = sqlite3.connect(backup_file)
        conn.execute("PRAGMA user_version=2")
        conn.commit(); conn.close()
        self._rewrite_backup_manifest_after_test_mutation(backup_file)
        with self.assertRaisesRegex(CertificateRepositoryError, "diverg"):
            verify_certificate_database_backup(backup_file)

    def test_missing_required_v3_objects_and_constraints_are_rejected(self):
        for suffix, mutation in (
            ("trigger", "DROP TRIGGER trg_extracted_source_immutable"),
            ("index", "DROP INDEX idx_certificate_documents_hash"),
            ("table", "DROP TABLE analysis_diagnostics"),
        ):
            with self.subTest(missing=suffix):
                backup = create_certificate_database_backup(
                    self.database, backup_directory=self.base / f"missing-{suffix}-backup", **self.safety,
                )
                backup_file = Path(backup["backup_path"])
                conn = sqlite3.connect(backup_file)
                conn.execute(mutation); conn.commit(); conn.close()
                self._rewrite_backup_manifest_after_test_mutation(backup_file)
                with self.assertRaises(CertificateRepositoryError):
                    verify_certificate_database_backup(backup_file)

    def test_missing_check_constraint_in_backup_is_rejected(self):
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "missing-check", **self.safety,
        )
        backup_file = Path(backup["backup_path"])
        conn = sqlite3.connect(backup_file)
        conn.execute("PRAGMA writable_schema=ON")
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
        self.assertIn("CHECK(length(file_hash)=64)", row[0])
        conn.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name='documents'",
                     (row[0].replace("CHECK(length(file_hash)=64)", ""),))
        conn.execute("PRAGMA schema_version=12345")
        conn.commit(); conn.close()
        self._rewrite_backup_manifest_after_test_mutation(backup_file)
        with self.assertRaisesRegex(CertificateRepositoryError, "CHECK"):
            verify_certificate_database_backup(backup_file)

    def test_hard_link_to_primary_database_is_rejected(self):
        alias = self.base / "analysis-hardlink.db"
        try:
            os.link(self.legacy_database, alias)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard link indispon?vel neste volume: {exc}")
        with self.assertRaises(CertificateRepositoryError):
            validate_database_path(alias, pdf_root=self.pdf_root,
                                   cassi_database_path=self.legacy_database)

    def test_abrupt_exit_after_atomic_swap_recovers_completed_candidate(self):
        from unittest.mock import patch
        import certificate_repository
        source_doc = self._register(); self._analysis(source_doc["id"])
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "abrupt-after", **self.safety,
        )
        target = self.base / "abrupt-after-target.db"
        initialize_certificate_database(target, **self.safety)
        real_replace = certificate_repository._durable_replace
        interrupted = False
        def replace_then_crash(source, destination):
            nonlocal interrupted
            result = real_replace(source, destination)
            if Path(destination) == target and ".restore-stage-" in Path(source).name and not interrupted:
                interrupted = True
                raise SystemExit("synthetic abrupt termination")
            return result
        with patch("certificate_repository._durable_replace", side_effect=replace_then_crash):
            with self.assertRaises(SystemExit):
                restore_certificate_database_backup(backup["backup_path"], target, backup_directory=self.base / "restore-safety", **self.safety)
        self.assertTrue(certificate_repository._restore_journal_path(target).exists())
        self.assertEqual(recover_incomplete_certificate_restore(target, **self.safety), "restore_completed")
        self.assertFalse(certificate_repository._restore_journal_path(target).exists())
        with connect_certificate_database(target, **self.safety) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_abrupt_exit_before_swap_preserves_original_and_recovers_temporary_state(self):
        from unittest.mock import patch
        import certificate_repository
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "abrupt-before", **self.safety,
        )
        target = self.base / "abrupt-before-target.db"
        initialize_certificate_database(target, **self.safety)
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        real_replace = certificate_repository._durable_replace
        def crash_before_swap(source, destination):
            if Path(destination) == target and ".restore-stage-" in Path(source).name:
                raise SystemExit("synthetic abrupt termination")
            return real_replace(source, destination)
        with patch("certificate_repository._durable_replace", side_effect=crash_before_swap):
            with self.assertRaises(SystemExit):
                restore_certificate_database_backup(backup["backup_path"], target, backup_directory=self.base / "restore-safety", **self.safety)
        self.assertTrue(certificate_repository._restore_journal_path(target).exists())
        self.assertEqual(recover_incomplete_certificate_restore(target, **self.safety), "original_preserved")
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        self.assertFalse(certificate_repository._restore_journal_path(target).exists())

    def test_second_connection_waits_while_restore_publication_is_in_progress(self):
        from unittest.mock import patch
        import threading
        import time
        import certificate_repository
        doc = self._register(); self._analysis(doc["id"])
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "two-connection-backup", **self.safety,
        )
        target = self.base / "two-connection-target.db"
        initialize_certificate_database(target, **self.safety)
        original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        swap_validating = threading.Event(); allow_restore = threading.Event()
        connected = threading.Event(); errors = []
        real_inspect = certificate_repository._inspect_certificate_database
        def pause_after_swap(path):
            result = real_inspect(path)
            if Path(path) == target and hashlib.sha256(target.read_bytes()).hexdigest() != original_hash:
                swap_validating.set()
                if not allow_restore.wait(5):
                    raise TimeoutError("synthetic restore release timed out")
            return result
        def restore_worker():
            try:
                restore_certificate_database_backup(
                    backup["backup_path"], target,
                    backup_directory=self.base / "two-connection-safety", **self.safety,
                )
            except BaseException as exc:
                errors.append(exc)
        def reader_worker():
            try:
                with connect_certificate_database(target, **self.safety) as conn:
                    conn.execute("SELECT COUNT(*) FROM documents").fetchone()
            except BaseException as exc:
                errors.append(exc)
            finally:
                connected.set()
        with patch("certificate_repository._inspect_certificate_database", side_effect=pause_after_swap):
            restorer = threading.Thread(target=restore_worker, daemon=True); restorer.start()
            self.assertTrue(swap_validating.wait(5))
            reader = threading.Thread(target=reader_worker, daemon=True); reader.start()
            time.sleep(0.15)
            self.assertFalse(connected.is_set(), "second repository connection entered during restore")
            allow_restore.set()
            restorer.join(5); reader.join(5)
        self.assertFalse(restorer.is_alive()); self.assertFalse(reader.is_alive())
        self.assertTrue(connected.is_set()); self.assertFalse(errors, repr(errors))

    def test_repository_connection_blocks_restore_until_connection_closes(self):
        import threading
        import time
        backup = create_certificate_database_backup(
            self.database, backup_directory=self.base / "concurrent-restore", **self.safety,
        )
        target = self.base / "concurrent-target.db"
        initialize_certificate_database(target, **self.safety)
        entered = threading.Event(); done = threading.Event(); failures = []
        def restore_worker():
            entered.set()
            try:
                restore_certificate_database_backup(backup["backup_path"], target, backup_directory=self.base / "restore-safety", **self.safety)
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()
        with connect_certificate_database(target, **self.safety) as conn:
            worker = threading.Thread(target=restore_worker, daemon=True)
            worker.start(); self.assertTrue(entered.wait(2))
            time.sleep(0.15)
            self.assertFalse(done.is_set(), "restore did not wait for active repository connection")
        worker.join(5)
        self.assertTrue(done.is_set())
        self.assertFalse(failures, repr(failures))
        with connect_certificate_database(target, **self.safety) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")


    def _terminate_restore_subprocess(self, backup_path, target, backup_dir, *, after_swap):
        import json
        import subprocess
        import sys
        config = json.dumps({
            "backup": str(backup_path), "target": str(target), "backup_dir": str(backup_dir),
            "pdf_root": str(self.pdf_root), "primary": str(self.legacy_database),
            "after_swap": after_swap,
        })
        code = r'''
import json, os, sys
from pathlib import Path
import certificate_repository as repo
cfg=json.loads(sys.argv[1]); target=Path(cfg["target"])
real=repo._durable_replace
def crash(source,destination):
    source=Path(source); destination=Path(destination)
    if destination == target and ".restore-stage-" in source.name:
        if cfg["after_swap"]:
            real(source,destination)
            os._exit(73)
        os._exit(74)
    return real(source,destination)
repo._durable_replace=crash
repo.restore_certificate_database_backup(
    cfg["backup"], target, backup_directory=cfg["backup_dir"],
    pdf_root=cfg["pdf_root"], cassi_database_path=cfg["primary"])
'''
        return subprocess.run([sys.executable, "-c", code, config], cwd=str(Path.cwd()),
                              capture_output=True, text=True, timeout=20)

    def _create_distinct_restore_backup(self, suffix):
        doc = self._register()
        with connect_certificate_database(self.database, **self.safety) as conn:
            run_number = conn.execute(
                "SELECT COALESCE(MAX(run_number),0)+1 FROM analyses WHERE document_id=?",
                (doc["id"],),
            ).fetchone()[0]
        create_analysis(self.database, document_id=doc["id"],
                        analysis_version=f"synthetic-{suffix}", run_number=run_number,
                        **self.safety)
        return create_certificate_database_backup(
            self.database, backup_directory=self.base / suffix, **self.safety,
        )

    def test_real_subprocess_crash_before_and_after_publish_recovers(self):
        import certificate_repository
        backup = self._create_distinct_restore_backup("subprocess-source-backup")
        for after_swap, expected_state in ((False, "original_preserved"), (True, "restore_completed")):
            with self.subTest(after_swap=after_swap):
                target = self.base / f"subprocess-target-{after_swap}.db"
                initialize_certificate_database(target, **self.safety)
                before = hashlib.sha256(target.read_bytes()).hexdigest()
                result = self._terminate_restore_subprocess(
                    backup["backup_path"], target, self.base / f"safety-{after_swap}",
                    after_swap=after_swap,
                )
                self.assertEqual(result.returncode, 73 if after_swap else 74, result.stderr)
                self.assertTrue(certificate_repository._restore_journal_path(target).is_file())
                state = recover_incomplete_certificate_restore(target, **self.safety)
                self.assertEqual(state, expected_state)
                with connect_certificate_database(target, **self.safety) as conn:
                    self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                self.assertEqual(count, 1 if after_swap else 0)
                if not after_swap:
                    self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)

    def test_backup_swap_after_snapshot_does_not_change_published_content(self):
        from unittest.mock import patch
        import certificate_repository
        backup = self._create_distinct_restore_backup("toctou-source")
        replacement = self._create_distinct_restore_backup("toctou-replacement")
        source = Path(backup["backup_path"]); replacement_bytes = Path(replacement["backup_path"]).read_bytes()
        target = self.base / "toctou-target.db"
        initialize_certificate_database(target, **self.safety)
        real_validate = certificate_repository._validate_backup_snapshot
        swapped = False
        def validate_then_swap(path, manifest, **kwargs):
            nonlocal swapped
            metadata = real_validate(path, manifest, **kwargs)
            if Path(path).name.startswith(f".{target.name}.restore-stage-") and not swapped:
                swapped = True
                source.write_bytes(replacement_bytes)
            return metadata
        with patch("certificate_repository._validate_backup_snapshot", side_effect=validate_then_swap):
            restore_certificate_database_backup(
                source, target, backup_directory=self.base / "toctou-safety", **self.safety,
            )
        self.assertTrue(swapped)
        with connect_certificate_database(target, **self.safety) as conn:
            filenames = [row[0] for row in conn.execute("SELECT filename FROM documents")]
        self.assertEqual(filenames, [self.pdf.name])

    def test_copy_hash_divergence_rejects_restore_and_preserves_destination(self):
        from unittest.mock import patch
        import certificate_repository
        backup = self._create_distinct_restore_backup("copy-hash-source")
        source = Path(backup["backup_path"])
        target = self.base / "copy-hash-target.db"
        initialize_certificate_database(target, **self.safety)
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        real_read_manifest = certificate_repository._read_backup_manifest
        mutated = False
        def read_then_mutate(path):
            nonlocal mutated
            manifest = real_read_manifest(path)
            if Path(path) == source and not mutated:
                mutated = True
                content = bytearray(source.read_bytes()); content[-128] ^= 1
                source.write_bytes(content)
            return manifest
        with patch("certificate_repository._read_backup_manifest", side_effect=read_then_mutate):
            with self.assertRaisesRegex(CertificateRepositoryError, "SHA-256"):
                restore_certificate_database_backup(
                    source, target, backup_directory=self.base / "copy-hash-safety", **self.safety,
                )
        self.assertTrue(mutated)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        import certificate_repository as repo
        self.assertFalse(repo._restore_journal_path(target).exists())

    def test_separate_process_holding_repository_connection_blocks_restore(self):
        import json
        import subprocess
        import sys
        import threading
        import time
        backup = self._create_distinct_restore_backup("external-process-source")
        target = self.base / "external-process-target.db"
        initialize_certificate_database(target, **self.safety)
        ready = self.base / "external-holder-ready"
        release = self.base / "external-holder-release"
        cfg = json.dumps({"db": str(target), "pdf": str(self.pdf_root),
                          "primary": str(self.legacy_database), "ready": str(ready),
                          "release": str(release)})
        code = r'''
import json,sys,time
from pathlib import Path
from certificate_repository import connect_certificate_database
c=json.loads(sys.argv[1])
with connect_certificate_database(c["db"],pdf_root=c["pdf"],cassi_database_path=c["primary"]):
    Path(c["ready"]).write_text("ready")
    deadline=time.monotonic()+10
    while not Path(c["release"]).exists() and time.monotonic()<deadline: time.sleep(.02)
'''
        process = subprocess.Popen([sys.executable, "-c", code, cfg], cwd=str(Path.cwd()),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+8
            while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists(), "external process did not acquire the DB connection")
            finished = threading.Event(); errors=[]
            def restore():
                try:
                    restore_certificate_database_backup(
                        backup["backup_path"], target,
                        backup_directory=self.base / "external-process-safety", **self.safety,
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    finished.set()
            worker=threading.Thread(target=restore,daemon=True); worker.start()
            time.sleep(.2)
            self.assertFalse(finished.is_set(), "restore published while another process held the repository connection")
            release.write_text("release")
            worker.join(8)
            self.assertTrue(finished.is_set()); self.assertFalse(errors, repr(errors))
        finally:
            release.write_text("release")
            stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr or stdout)
        with connect_certificate_database(target, **self.safety) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)

    def test_adulterated_journal_and_missing_or_corrupt_safety_backup_preserve_target(self):
        import certificate_repository
        backup = self._create_distinct_restore_backup("recovery-protection-source")
        for mode in ("journal", "missing-safety", "corrupt-safety"):
            with self.subTest(mode=mode):
                target = self.base / f"recovery-protection-{mode}.db"
                initialize_certificate_database(target, **self.safety)
                result = self._terminate_restore_subprocess(
                    backup["backup_path"], target, self.base / f"recovery-protection-safety-{mode}",
                    after_swap=True,
                )
                self.assertEqual(result.returncode, 73, result.stderr)
                journal_path = certificate_repository._restore_journal_path(target)
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                candidate_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if mode == "journal":
                    journal["state"] = "corrupted-state"
                    journal_path.write_text(json.dumps(journal), encoding="utf-8")
                else:
                    safety = Path(journal["safety_backup_path"])
                    if mode == "missing-safety":
                        safety.unlink()
                        Path(str(safety)+".json").unlink()
                    else:
                        raw=bytearray(safety.read_bytes()); raw[-128] ^= 1; safety.write_bytes(raw)
                with self.assertRaises(Exception):
                    recover_incomplete_certificate_restore(target, **self.safety)
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), candidate_hash)
                self.assertTrue(journal_path.exists())

    def test_posix_replace_failure_is_propagated_without_claiming_success(self):
        from unittest.mock import patch
        import certificate_repository
        source=self.base/"posix-publish-source.tmp"; destination=self.base/"posix-publish-destination.tmp"
        source.write_bytes(b"new synthetic bytes"); destination.write_bytes(b"old synthetic bytes")
        with patch("certificate_repository.os.name","posix"), \
             patch("certificate_repository.os.replace",side_effect=OSError("synthetic os.replace failure")) as replace:
            with self.assertRaisesRegex(OSError,"os.replace failure"):
                certificate_repository._durable_replace(source,destination)
        replace.assert_called_once_with(source,destination)
        self.assertEqual(source.read_bytes(),b"new synthetic bytes")
        self.assertEqual(destination.read_bytes(),b"old synthetic bytes")

    @unittest.skipUnless(os.name == "nt", "Windows MoveFileExW path only")
    def test_movefileexw_failure_is_propagated_without_claiming_success(self):
        from unittest.mock import Mock, patch
        import certificate_repository
        source=self.base/"movefile-source.tmp"; destination=self.base/"movefile-destination.tmp"
        source.write_bytes(b"new synthetic bytes"); destination.write_bytes(b"old synthetic bytes")
        api=Mock(return_value=0)
        kernel=type("Kernel",(),{"MoveFileExW":api})()
        with patch.object(certificate_repository.ctypes,"WinDLL",return_value=kernel), \
             patch.object(certificate_repository.ctypes,"get_last_error",return_value=5), \
             patch.object(certificate_repository.ctypes,"WinError",side_effect=OSError("MoveFileExW denied")):
            with self.assertRaisesRegex(OSError,"MoveFileExW denied"):
                certificate_repository._durable_replace(source,destination)
        self.assertEqual(source.read_bytes(),b"new synthetic bytes")
        self.assertEqual(destination.read_bytes(),b"old synthetic bytes")

    def test_missing_diary_with_orphan_stage_preserves_all_artifacts(self):
        import certificate_repository
        target=self.base/"missing-diary-target.db"
        initialize_certificate_database(target, **self.safety)
        before=hashlib.sha256(target.read_bytes()).hexdigest()
        stage=target.parent/f".{target.name}.restore-stage-orphan.db"
        stage.write_bytes(b"synthetic orphan stage evidence")
        journal_tmp=target.parent/f".{target.name}.restore-journal-orphan.tmp"
        journal_tmp.write_text("synthetic journal temp evidence",encoding="utf-8")
        with self.assertRaises(CertificateRepositoryError):
            recover_incomplete_certificate_restore(target, **self.safety)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)
        self.assertEqual(stage.read_bytes(),b"synthetic orphan stage evidence")
        self.assertEqual(journal_tmp.read_text(encoding="utf-8"),"synthetic journal temp evidence")
        self.assertFalse(certificate_repository._restore_journal_path(target).exists())

    def test_corrupt_diary_preserves_stage_diary_and_destination(self):
        import certificate_repository
        target=self.base/"corrupt-diary-target.db"
        initialize_certificate_database(target, **self.safety)
        before=hashlib.sha256(target.read_bytes()).hexdigest()
        stage=target.parent/f".{target.name}.restore-stage-orphan.db"
        stage.write_bytes(b"candidate bytes for manual review")
        journal=certificate_repository._restore_journal_path(target)
        journal.write_text("{invalid json",encoding="utf-8")
        with self.assertRaises(CertificateRepositoryError):
            recover_incomplete_certificate_restore(target, **self.safety)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)
        self.assertEqual(stage.read_bytes(),b"candidate bytes for manual review")
        self.assertEqual(journal.read_text(encoding="utf-8"),"{invalid json")

    def test_stage_fsync_failure_is_not_reported_as_success_and_preserves_evidence(self):
        from unittest.mock import patch
        import certificate_repository
        backup=self._create_distinct_restore_backup("fsync-stage-source")
        target=self.base/"fsync-stage-target.db"
        initialize_certificate_database(target, **self.safety)
        before=hashlib.sha256(target.read_bytes()).hexdigest()
        with patch("certificate_repository.os.fsync",side_effect=OSError("synthetic staging fsync failure")):
            with self.assertRaisesRegex(OSError,"staging fsync"):
                restore_certificate_database_backup(
                    backup["backup_path"],target,backup_directory=self.base/"fsync-stage-backups",**self.safety)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)
        orphans=certificate_repository._restore_temporary_files(target)
        self.assertTrue(orphans)
        self.assertTrue(any(path.stat().st_size for path in orphans))
        with self.assertRaises(CertificateRepositoryError):
            recover_incomplete_certificate_restore(target, **self.safety)
        self.assertTrue(all(path.exists() for path in orphans))

    def test_directory_sync_failure_after_publish_rolls_back_and_propagates(self):
        from unittest.mock import patch
        import certificate_repository
        olddoc=self._register(); self._analysis(olddoc["id"])
        target_backup=create_certificate_database_backup(
            self.database,backup_directory=self.base/"dirsync-source-backups",**self.safety)
        target=self.base/"dirsync-target.db"
        initialize_certificate_database(target, **self.safety)
        oldpdf=self.pdf_root/"old-target.pdf"
        oldpdf.write_bytes(b"fictional old target sentinel")
        register_document(target,filename=oldpdf.name,original_path=oldpdf,
                          file_hash=hashlib.sha256(oldpdf.read_bytes()).hexdigest(),page_count=1,
                          document_type="digital",**self.safety)
        real_sync=certificate_repository._sync_directory
        target_syncs=0
        def fail_publish_directory_sync(path):
            nonlocal target_syncs
            if Path(path)==target.parent:
                target_syncs+=1
                if target_syncs==2:
                    raise OSError("synthetic directory fsync failure")
            return real_sync(path)
        with patch("certificate_repository._sync_directory",side_effect=fail_publish_directory_sync):
            with self.assertRaisesRegex(OSError,"directory fsync"):
                restore_certificate_database_backup(
                    target_backup["backup_path"],target,
                    backup_directory=self.base/"dirsync-safety",**self.safety)
        self.assertGreaterEqual(target_syncs,3)
        with connect_certificate_database(target,**self.safety) as conn:
            self.assertEqual([row[0] for row in conn.execute("SELECT filename FROM documents")],[oldpdf.name])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_next_repository_open_recovers_after_real_process_exit(self):
        backup=self._create_distinct_restore_backup("restart-recovery-source")
        target=self.base/"restart-recovery-target.db"
        initialize_certificate_database(target, **self.safety)
        result=self._terminate_restore_subprocess(
            backup["backup_path"],target,self.base/"restart-recovery-safety",after_swap=True)
        self.assertEqual(result.returncode,73,result.stderr)
        with connect_certificate_database(target,**self.safety) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],1)
        import certificate_repository
        self.assertFalse(certificate_repository._restore_journal_path(target).exists())

    def test_failed_atomic_restore_recovers_previous_target(self):
        from unittest.mock import patch
        import certificate_repository

        source_doc = self._register()
        self._analysis(source_doc["id"])
        backup_dir = self.base / "restore-failure-backups"
        backup = create_certificate_database_backup(
            self.database, backup_directory=backup_dir, **self.safety,
        )
        target = self.base / "restore-failure-target.db"
        initialize_certificate_database(target, **self.safety)
        second_pdf = self.pdf_root / "target-before-failure.pdf"
        second_pdf.write_bytes(b"synthetic target state")
        register_document(
            target, filename=second_pdf.name, original_path=second_pdf,
            file_hash=hashlib.sha256(second_pdf.read_bytes()).hexdigest(), page_count=1,
            document_type="digital", **self.safety,
        )
        target_before = hashlib.sha256(target.read_bytes()).hexdigest()
        real_replace = certificate_repository._durable_replace
        failed_once = False

        def fail_candidate_swap(source, destination):
            nonlocal failed_once
            if (not failed_once and Path(destination) == target
                    and ".restore-" in Path(source).name):
                failed_once = True
                raise OSError("synthetic atomic swap failure")
            return real_replace(source, destination)

        with patch("certificate_repository._durable_replace", side_effect=fail_candidate_swap):
            with self.assertRaisesRegex(OSError, "synthetic atomic swap failure"):
                restore_certificate_database_backup(
                    backup["backup_path"], target, backup_directory=backup_dir, **self.safety,
                )
        self.assertTrue(failed_once)
        with connect_certificate_database(target, **self.safety) as current:
            self.assertEqual(current.execute("SELECT filename FROM documents").fetchone()[0], second_pdf.name)
            self.assertEqual(current.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(hashlib.sha256(self.legacy_database.read_bytes()).hexdigest(), self.legacy_hash)

    def test_post_swap_validation_failure_restores_previous_target(self):
        from unittest.mock import patch
        import certificate_repository

        source_doc = self._register()
        self._analysis(source_doc["id"])
        backup_dir = self.base / "post-swap-backups"
        backup = create_certificate_database_backup(
            self.database, backup_directory=backup_dir, **self.safety,
        )
        target = self.base / "post-swap-target.db"
        initialize_certificate_database(target, **self.safety)
        second_pdf = self.pdf_root / "post-swap-target-state.pdf"
        second_pdf.write_bytes(b"synthetic state before restore")
        register_document(
            target, filename=second_pdf.name, original_path=second_pdf,
            file_hash=hashlib.sha256(second_pdf.read_bytes()).hexdigest(), page_count=1,
            document_type="digital", **self.safety,
        )
        target_before = hashlib.sha256(target.read_bytes()).hexdigest()
        real_replace = certificate_repository._durable_replace
        real_inspect = certificate_repository._inspect_certificate_database
        candidate_installed = False
        failed_once = False

        def track_atomic_install(source, destination):
            nonlocal candidate_installed
            result = real_replace(source, destination)
            if Path(destination) == target and ".restore-" in Path(source).name:
                candidate_installed = True
            return result

        def fail_post_swap_check(path):
            nonlocal failed_once
            if Path(path) == target and candidate_installed and not failed_once:
                failed_once = True
                raise CertificateRepositoryError("synthetic post-swap validation failure")
            return real_inspect(path)

        with patch("certificate_repository._durable_replace", side_effect=track_atomic_install), \
             patch("certificate_repository._inspect_certificate_database", side_effect=fail_post_swap_check):
            with self.assertRaisesRegex(CertificateRepositoryError, "post-swap"):
                restore_certificate_database_backup(
                    backup["backup_path"], target, backup_directory=backup_dir, **self.safety,
                )
        self.assertTrue(failed_once)
        with connect_certificate_database(target, **self.safety) as current:
            self.assertEqual(current.execute("SELECT filename FROM documents").fetchone()[0], second_pdf.name)
            self.assertEqual(current.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_migration_failure_rolls_back_schema_and_preserves_validated_backup(self):
        from unittest.mock import patch
        import certificate_repository

        legacy = self.base / "rollback-v2" / "certificate_analysis.db"
        legacy.parent.mkdir()
        self._create_v2_fixture(legacy)
        backup_dir = self.base / "rollback-backups"
        with patch.object(certificate_repository, "_validate_migrated_schema",
                          side_effect=RuntimeError("synthetic failure before commit")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                initialize_certificate_database(
                    legacy, backup_directory=backup_dir, pdf_root=self.pdf_root,
                    cassi_database_path=self.legacy_database,
                )
        rolled_back = sqlite3.connect(legacy)
        try:
            self.assertEqual(rolled_back.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "2")
            columns = {row[1] for row in rolled_back.execute("PRAGMA table_info(extracted_items)")}
            self.assertNotIn("field_coordinates", columns)
            self.assertEqual(rolled_back.execute("SELECT reading_confidence,interpretation_confidence FROM extracted_items WHERE id=1").fetchone(), ("medium", "high"))
            old_trigger = rolled_back.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_extracted_source_immutable'").fetchone()[0]
            self.assertNotIn("field_coordinates", old_trigger)
            self.assertEqual(rolled_back.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            rolled_back.close()
        backups = list(backup_dir.glob("certificate_analysis_pre_v3_*.db"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0].as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(backup.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "2")
        finally:
            backup.close()

    def test_invalid_backup_prevents_migration(self):
        from unittest.mock import patch
        import certificate_repository

        legacy = self.base / "invalid-backup-v2" / "certificate_analysis.db"
        legacy.parent.mkdir()
        self._create_v2_fixture(legacy)
        with patch.object(certificate_repository, "_create_validated_migration_backup",
                          side_effect=CertificateRepositoryError("synthetic backup validation failure")):
            with self.assertRaisesRegex(CertificateRepositoryError, "backup validation"):
                initialize_certificate_database(
                    legacy, pdf_root=self.pdf_root, cassi_database_path=self.legacy_database,
                )
        unchanged = sqlite3.connect(legacy)
        try:
            self.assertEqual(unchanged.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "2")
            self.assertNotIn("field_coordinates", {row[1] for row in unchanged.execute("PRAGMA table_info(extracted_items)")})
        finally:
            unchanged.close()

    @staticmethod
    def _create_v2_fixture(path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES('schema_version','2');
            PRAGMA user_version=2;
            CREATE TABLE documents(id INTEGER PRIMARY KEY,filename TEXT NOT NULL,original_path TEXT NOT NULL,
                file_hash TEXT NOT NULL CHECK(length(file_hash)=64),page_count INTEGER NOT NULL CHECK(page_count >= 0),
                document_type TEXT NOT NULL CHECK(document_type IN ('digital','scanned','hybrid','unknown')),created_at TEXT NOT NULL,
                UNIQUE(original_path,file_hash));
            CREATE TABLE analyses(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,analysis_version TEXT NOT NULL,
                run_number INTEGER NOT NULL CHECK(run_number > 0),status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled')),
                started_at TEXT NOT NULL,completed_at TEXT,error TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',UNIQUE(document_id,run_number));
            CREATE TABLE extracted_items(id INTEGER PRIMARY KEY,analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE RESTRICT,page_number INTEGER NOT NULL CHECK(page_number > 0),
                source_row_number INTEGER NOT NULL CHECK(source_row_number >= 0),item_type TEXT NOT NULL CHECK(item_type IN ('service','header','subtotal','total','other')),service_description_original TEXT NOT NULL,
                quantity_original TEXT,unit_original TEXT,quantity_normalized TEXT,unit_normalized TEXT,
                evidence_text TEXT NOT NULL,evidence_coordinates TEXT NOT NULL,
                source_method TEXT NOT NULL CHECK(source_method IN ('native','ocr','hybrid','unknown')),
                reading_confidence TEXT NOT NULL CHECK(reading_confidence IN ('not_assessed','low','medium','high')),
                interpretation_confidence TEXT NOT NULL CHECK(interpretation_confidence IN ('not_assessed','low','medium','high')),
                reading_confidence_legacy_score REAL,interpretation_confidence_legacy_score REAL,
                review_reasons TEXT NOT NULL,review_status TEXT NOT NULL CHECK(review_status IN ('pending','approved','rejected','corrected')),dedup_key TEXT NOT NULL,
                UNIQUE(analysis_id,page_number,dedup_key));
            CREATE TABLE reviews(id INTEGER PRIMARY KEY,extracted_item_id INTEGER NOT NULL REFERENCES extracted_items(id) ON DELETE RESTRICT,
                action TEXT NOT NULL CHECK(action IN ('approve','reject','correct')),
                previous_value TEXT,corrected_value TEXT,reviewer TEXT NOT NULL,review_date TEXT NOT NULL,notes TEXT);
            CREATE TRIGGER trg_extracted_source_immutable
                BEFORE UPDATE OF analysis_id,page_number,item_type,service_description_original,
                    quantity_original,unit_original,quantity_normalized,unit_normalized,evidence_text,
                    evidence_coordinates,source_method,reading_confidence,interpretation_confidence,
                    reading_confidence_legacy_score,interpretation_confidence_legacy_score,
                    review_reasons,source_row_number,dedup_key
                ON extracted_items BEGIN SELECT RAISE(ABORT,'immutable v2 source'); END;
            INSERT INTO documents VALUES(1,'fake.pdf','C:/synthetic-v2/fake.pdf',printf('%064d',2),1,'digital','now');
            INSERT INTO analyses VALUES(1,1,'v2',1,'completed','now','now',NULL,'[]');
            INSERT INTO extracted_items VALUES(1,1,1,4,'service','V2 synthetic','3,00','m','3.00','m',
                'V2 synthetic 3,00 m','{}','native','medium','high',0.75,0.95,'["revise"]','pending','v2-key');
            INSERT INTO reviews VALUES(1,1,'approve',NULL,NULL,'reviewer synthetic','2026-01-02T00:00:00Z','v2 history');
        """)
        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
