import tempfile
import unittest
from pathlib import Path

from database import initialize, replace_document
from pdf_processor import _combined_search_text
from search_engine import search


class HybridTextTests(unittest.TestCase):
    def test_combined_text_keeps_sources_and_deduplicates_native_lines(self):
        native = "Certificate header\nNative metadata"
        ocr = "Certificate header\nTable row\nRepeated value\nRepeated value"
        combined = _combined_search_text(native, ocr)
        self.assertEqual(combined.count("Certificate header"), 1)
        self.assertEqual(combined.count("Repeated value"), 2)
        self.assertIn("Native metadata", combined)
        self.assertIn("Table row", combined)

    def test_combined_text_separates_value_from_attached_unit_without_mutating_ocr(self):
        native = ""
        ocr = "Flow 346,00m3/h at 17,52m, quantity 2,00"
        combined = _combined_search_text(native, ocr)
        self.assertIn("346,00 m3/h", combined)
        self.assertIn("17,52 m", combined)
        self.assertEqual(ocr, "Flow 346,00m3/h at 17,52m, quantity 2,00")

    def test_four_numeric_queries_return_correct_hybrid_pdf_page_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hybrid.db"
            initialize(db)
            native = "Native certificate metadata"
            ocr = "PUMP FLOW 346,00m3/h HEIGHT 17,52m QUANTITY 2,00"
            combined = _combined_search_text(native, ocr)
            replace_document(db, {
                "path": "C:/acervo/2220463185-2017.pdf",
                "name": "2220463185-2017.pdf", "sha256": "sha", "indexed_at": "now",
            }, ["Page without pump values"] * 26 + [{
                "text": combined, "native_text": native, "ocr_text": ocr,
                "extraction_method": "hybrid", "ocr_status": "recognized",
            }])

            expected_match = {
                "346,00": "346,00",
                "17,52": "17,52",
                "2,00": "2,00",
                "346,00 m3/h": "346,00 m3/h",
            }
            for query, expected in expected_match.items():
                with self.subTest(query=query):
                    rows = search(str(db), query)
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual(row["pdf"], "2220463185-2017.pdf")
                    self.assertEqual(row["path"], "C:/acervo/2220463185-2017.pdf")
                    self.assertEqual(row["page"], 27)
                    self.assertTrue(row["snippet"])
                    self.assertEqual(row["matched_text"], expected)
                    self.assertEqual(row["extraction_method"], "hybrid")
                    self.assertEqual(row["source"], "ocr")

    def test_two_distinct_2_00_page_occurrences_are_not_duplicated_across_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "amounts.db"
            initialize(db)
            page_27_native = "Native certificate metadata"
            page_27_ocr = "PUMP QUANTITY 2,00"
            page_28_native = "Quantity 2,00"
            page_28_ocr = "Quantity 2,00"
            replace_document(db, {
                "path": "C:/acervo/2220463185-2017.pdf",
                "name": "2220463185-2017.pdf", "sha256": "sha", "indexed_at": "now",
            }, ["No amount here"] * 26 + [
                {"text": _combined_search_text(page_27_native, page_27_ocr),
                 "native_text": page_27_native, "ocr_text": page_27_ocr,
                 "extraction_method": "hybrid", "ocr_status": "recognized"},
                {"text": _combined_search_text(page_28_native, page_28_ocr),
                 "native_text": page_28_native, "ocr_text": page_28_ocr,
                 "extraction_method": "hybrid", "ocr_status": "recognized"},
            ])

            rows = search(str(db), "2,00")
            self.assertEqual([(row["page"], row["source"]) for row in rows],
                             [(27, "ocr"), (28, "native")])
            self.assertEqual(len({(row["path"], row["page"]) for row in rows}), 2)

    def test_term_and_acronym_search_cover_native_and_ocr_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "terms.db"
            initialize(db)
            replace_document(db, {
                "path": "C:/acervo/pavimentacao.pdf",
                "name": "pavimentacao.pdf", "sha256": "terms", "indexed_at": "now",
            }, [
                {"text": "Tratamento Superficial Duplo", "native_text": "Tratamento Superficial Duplo",
                 "ocr_text": "", "extraction_method": "text"},
                {"text": "TSD executed on the road", "native_text": "", "ocr_text": "TSD executed on the road",
                 "extraction_method": "ocr"},
            ])

            full_term = search(str(db), "tratamento superficial duplo")
            acronym = search(str(db), "TSD")
            self.assertEqual([(row["page"], row["source"]) for row in full_term],
                             [(1, "native"), (2, "ocr")])
            self.assertEqual([(row["page"], row["source"]) for row in acronym],
                             [(2, "ocr"), (1, "native")])
            self.assertEqual({row["pdf"] for row in full_term + acronym}, {"pavimentacao.pdf"})

    def test_numeric_query_does_not_match_a_different_longer_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "numeric.db"
            initialize(db)
            replace_document(db, {"path": "x.pdf", "name": "x.pdf", "sha256": "x", "indexed_at": "now"},
                             ["Value 346,001 m3/h"])
            self.assertEqual(search(str(db), "346,00"), [])


if __name__ == "__main__":
    unittest.main()
