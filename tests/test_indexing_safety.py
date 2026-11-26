import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pdf_processor

from database import (all_documents, all_pages, attempts_for_document, begin_attempt,
                      get_document, initialize, ocr_issues, recover_interrupted,
                      replace_document, stats)
from pdf_processor import (_quality_comparison, index_files, index_folder,
                           preview_folder_index, sha256_file)


def make_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 400), text, fontsize=12)
    doc.save(path)
    doc.close()


class IndexingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "pdfs"
        self.source.mkdir()
        self.db = self.root / "test-index.db"
        initialize(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def index(self, progress=None):
        return index_folder(self.source, self.db, ocr=False, progress=progress)

    def test_reindexes_changed_document(self):
        pdf = self.source / "obra.pdf"
        make_pdf(pdf, "Texto original suficiente para extração nativa do CASSI.")
        first = self.index()
        old_hash = get_document(self.db, str(pdf.resolve()))["indexed_sha256"]
        make_pdf(pdf, "Conteúdo atualizado suficiente para reindexação segura do documento.")
        second = self.index()
        doc = get_document(self.db, str(pdf.resolve()))
        self.assertEqual(first["indexed"], 1)
        self.assertEqual(second["indexed"], 1)
        self.assertNotEqual(doc["indexed_sha256"], old_hash)
        self.assertIn("atualizado", all_pages(self.db)[0]["text"])
        self.assertEqual([a["status"] for a in attempts_for_document(self.db, str(pdf.resolve()))],
                         ["indexed", "indexed"])
        for attempt in attempts_for_document(self.db, str(pdf.resolve())):
            self.assertEqual(attempt["sha256"], attempt["sha256_after"])

    def test_explicit_file_list_never_scans_neighboring_pdfs(self):
        selected = self.source / "selected.pdf"
        unselected = self.source / "do-not-index.pdf"
        make_pdf(selected, "Arquivo aprovado explicitamente para o piloto CASSI.")
        make_pdf(unselected, "Arquivo vizinho que não deve ser processado.")
        result = index_files([selected], self.db, ocr=False)
        docs = all_documents(self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["name"], selected.name)

    def test_folder_indexing_uses_only_pdfs_directly_in_the_acervo_root(self):
        direct = self.source / "root.pdf"
        nested_dir = self.source / "existing-folder"
        nested_dir.mkdir()
        nested = nested_dir / "nested.pdf"
        make_pdf(direct, "Direct root PDF for the centralized CASSI collection.")
        make_pdf(nested, "Nested PDF must not be scanned or indexed.")

        result = self.index()

        self.assertEqual(result["total"], 1)
        self.assertEqual([doc["path"] for doc in all_documents(self.db)], [str(direct.resolve())])

    def test_preview_reports_new_changed_unchanged_and_duplicate_files_without_indexing(self):
        unchanged = self.source / "unchanged.pdf"
        altered = self.source / "altered.pdf"
        new_copy = self.source / "new-copy.pdf"
        nested_dir = self.source / "nested"
        nested_dir.mkdir()
        (nested_dir / "ignored.pdf").write_bytes(b"nested file")
        unchanged.write_bytes(b"same current bytes")
        new_copy.write_bytes(b"same current bytes")
        altered.write_bytes(b"new altered bytes")

        replace_document(self.db, {
            "path": str(unchanged.resolve()), "name": unchanged.name,
            "sha256": sha256_file(unchanged), "indexed_at": "old",
        }, ["unchanged snapshot"])
        replace_document(self.db, {
            "path": str(altered.resolve()), "name": altered.name,
            "sha256": "old-hash", "indexed_at": "old",
        }, ["old snapshot"])
        replace_document(self.db, {
            "path": "C:/other-acervo/known-copy.pdf", "name": "known-copy.pdf",
            "sha256": sha256_file(new_copy), "indexed_at": "old",
        }, ["existing duplicate content"])
        before = [(row["document_path"], row["text"]) for row in all_pages(self.db)]
        before_documents = all_documents(self.db)
        before_attempts = attempts_for_document(self.db)

        with (patch("pdf_processor.extract_pages", side_effect=AssertionError("preview must not extract")) as extract,
              patch("pdf_processor.initialize", side_effect=AssertionError("preview must not initialize/write")) as init,
              patch("pdf_processor.begin_attempt", side_effect=AssertionError("preview must not write")) as begin,
              patch("pdf_processor.finish_attempt", side_effect=AssertionError("preview must not write")) as finish):
            preview = preview_folder_index(self.source, self.db)
            extract.assert_not_called()
            init.assert_not_called()
            begin.assert_not_called()
            finish.assert_not_called()

        self.assertEqual(preview["total"], 3)
        self.assertEqual(preview["existing"], 1)
        self.assertEqual(preview["new"], 1)
        self.assertEqual(preview["altered"], 1)
        self.assertEqual(preview["duplicates"], 2)
        self.assertEqual(set(preview["processing_paths"]), {str(new_copy.resolve()), str(altered.resolve())})
        self.assertNotIn(str((nested_dir / "ignored.pdf").resolve()), preview["file_hashes"])
        self.assertEqual([(row["document_path"], row["text"]) for row in all_pages(self.db)], before)
        self.assertEqual(all_documents(self.db), before_documents)
        self.assertEqual(attempts_for_document(self.db), before_attempts)

    def test_image_text_quality_check_flags_content_mismatch(self):
        native = "".join(f"nativeword{i} " for i in range(120))
        ocr = "differentword1 differentword2 differentword3 differentword4 differentword5 differentword6 "
        warning, overlap, balance = _quality_comparison(native, ocr)
        self.assertIsNotNone(warning)
        self.assertLess(overlap, 0.35)
        self.assertLess(balance, 0.35)

    def test_failed_reindex_preserves_last_valid_pages_and_hash(self):
        pdf = self.source / "obra.pdf"
        make_pdf(pdf, "Versão válida indexada e preservada se a extração nova falhar.")
        self.index()
        path = str(pdf.resolve())
        old_hash = get_document(self.db, path)["indexed_sha256"]
        old_page = all_pages(self.db)[0]
        make_pdf(pdf, "Nova versão com extração simulada como falha temporária.")
        with patch("pdf_processor.extract_pages", side_effect=RuntimeError("erro de teste")):
            result = self.index()
        doc = get_document(self.db, path)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["indexed_sha256"], old_hash)
        self.assertEqual(all_pages(self.db)[0]["text"], old_page["text"])
        self.assertEqual(all_pages(self.db)[0]["native_text"], old_page["native_text"])
        self.assertEqual(all_pages(self.db)[0]["ocr_text"], old_page["ocr_text"])
        self.assertEqual(attempts_for_document(self.db, path)[-1]["status"], "error")

    def test_hybrid_file_list_is_explicit_and_stores_both_sources(self):
        selected = self.source / "selected.pdf"
        neighbor = self.source / "neighbor.pdf"
        make_pdf(selected, "Native source sufficient for a hybrid regression fixture.")
        make_pdf(neighbor, "This neighboring file must not be read or indexed.")
        hybrid_page = {"text": "Native source OCR table value 346,00 m3/h",
                       "native_text": "Native source", "ocr_text": "OCR table value 346,00m3/h",
                       "extraction_method": "hybrid", "ocr_status": "recognized",
                       "ocr_error": None, "page_error": None}
        with patch("pdf_processor.extract_pages", return_value=[hybrid_page]) as extract:
            result = index_files([selected], self.db, ocr=True,
                                 hybrid_pages_by_path={selected: {1}})
        extract.assert_called_once()
        self.assertEqual(extract.call_args.kwargs["hybrid_pages"], {1})
        self.assertEqual(result["total"], 1)
        self.assertEqual([doc["name"] for doc in all_documents(self.db)], [selected.name])
        stored = all_pages(self.db)[0]
        self.assertEqual(stored["native_text"], "Native source")
        self.assertEqual(stored["ocr_text"], "OCR table value 346,00m3/h")
        self.assertIn("346,00 m3/h", stored["text"])

    def test_interrupted_attempt_recovers_and_resume_indexes_new_version(self):
        pdf = self.source / "obra.pdf"
        make_pdf(pdf, "Snapshot anterior deve sobreviver a interrupção inesperada.")
        self.index()
        path = str(pdf.resolve())
        old_text = all_pages(self.db)[0]["text"]
        make_pdf(pdf, "Texto novo será indexado ao retomar processamento interrompido.")
        attempt = begin_attempt(self.db, path, pdf.name, sha256_file(pdf))
        self.assertEqual(get_document(self.db, path)["status"], "pending")
        self.assertEqual(recover_interrupted(self.db, set()), 1)
        self.assertEqual(get_document(self.db, path)["status"], "indexed")
        self.assertEqual(all_pages(self.db)[0]["text"], old_text)
        self.assertEqual(attempts_for_document(self.db, path)[-1]["status"], "interrupted")
        resumed = self.index()
        self.assertEqual(resumed["indexed"], 1)
        self.assertIn("retomar", all_pages(self.db)[0]["text"])

    def test_ocr_failure_is_visible_and_does_not_replace_valid_snapshot(self):
        pdf = self.source / "scan.pdf"
        make_pdf(pdf, "Versão válida do documento antes de simular OCR com falha.")
        self.index()
        path = str(pdf.resolve())
        old_text = all_pages(self.db)[0]["text"]
        make_pdf(pdf, "Novo conteúdo cuja tentativa de OCR será marcada com aviso.")
        failed_page = {"text": "texto parcial", "extraction_method": "ocr", "ocr_status": "failed",
                       "ocr_error": "Tesseract indisponível no teste", "page_error": None}
        with patch("pdf_processor.extract_pages", return_value=[failed_page]):
            result = self.index()
        self.assertEqual(result["warnings"], 1)
        self.assertEqual(get_document(self.db, path)["status"], "warning")
        self.assertEqual(all_pages(self.db)[0]["text"], old_text)
        self.assertEqual(ocr_issues(self.db)[0]["ocr_error"], "Tesseract indisponível no teste")
        with patch("pdf_processor.extract_pages", return_value=[{"text": "Novo texto completo após repetir OCR.",
               "extraction_method": "ocr", "ocr_status": "recognized", "ocr_error": None, "page_error": None}]) as extract:
            retried = self.index()
        extract.assert_called_once()
        self.assertEqual(retried["indexed"], 1)
        self.assertEqual(get_document(self.db, path)["status"], "indexed")
        self.assertIn("completo", all_pages(self.db)[0]["text"])

    def test_unchanged_document_is_skipped_without_extracting_again(self):
        pdf = self.source / "obra.pdf"
        make_pdf(pdf, "Documento inalterado com texto nativo suficiente para teste.")
        self.index()
        with patch("pdf_processor.extract_pages", side_effect=AssertionError("não deve extrair")) as extract:
            result = self.index()
        extract.assert_not_called()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(attempts_for_document(self.db)), 1)

    def test_identical_files_in_different_paths_are_retained_and_marked_duplicate(self):
        first = self.source / "a.pdf"
        second = self.source / "b.pdf"
        make_pdf(first, "Dois caminhos podem apontar para conteúdo PDF idêntico.")
        second.write_bytes(first.read_bytes())
        result = self.index()
        docs = all_documents(self.db)
        self.assertEqual(result["indexed"], 2)
        self.assertEqual(len(docs), 2)
        duplicate = [doc for doc in docs if doc["duplicate_of"]]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0]["duplicate_of"], str(first.resolve()))
        again = self.index()
        self.assertEqual(again["skipped"], 2)
        self.assertEqual(stats(self.db)["documents"], 2)

    def test_progress_reports_document_and_page_counters(self):
        one = self.source / "a-good.pdf"
        warning = self.source / "b-warning.pdf"
        broken = self.source / "c-broken.pdf"
        make_pdf(one, "Documento correto para observar o progresso da indexação.")
        make_pdf(warning, "Documento para simular aviso de OCR no progresso da indexação.")
        broken.write_bytes(b"not a pdf")
        events = []
        actual_extract = pdf_processor.extract_pages

        def extract_with_warning(path, ocr=True, language="por+eng", progress=None):
            if Path(path).name == warning.name:
                return [{"text": "parcial", "extraction_method": "ocr", "ocr_status": "failed",
                         "ocr_error": "erro de OCR simulado", "page_error": None}]
            return actual_extract(path, ocr=ocr, language=language, progress=progress)

        with patch("pdf_processor.extract_pages", side_effect=extract_with_warning):
            result = self.index(progress=events.append)
        page_events = [event for event in events if event["phase"] == "page"]
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["completed"], 3)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(result["warnings"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertTrue(page_events)
        self.assertEqual(page_events[0]["page_current"], 1)
        self.assertEqual(page_events[0]["page_total"], 1)
        for event in events:
            for key in ("total", "completed", "pending", "warnings", "errors",
                        "current_pdf", "page_current", "page_total", "elapsed_seconds"):
                self.assertIn(key, event)
        self.assertEqual(events[-1]["phase"], "complete")

    def test_bad_pdf_does_not_stop_later_documents(self):
        (self.source / "a-broken.pdf").write_bytes(b"not a pdf")
        good = self.source / "b-good.pdf"
        make_pdf(good, "Documento válido continua após falha do PDF anterior.")
        result = self.index()
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["indexed"], 1)
        statuses = {d["name"]: d["status"] for d in all_documents(self.db)}
        self.assertEqual(statuses["a-broken.pdf"], "error")
        self.assertEqual(statuses["b-good.pdf"], "indexed")


if __name__ == "__main__":
    unittest.main()
