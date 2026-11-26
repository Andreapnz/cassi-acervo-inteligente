import unittest
from unittest.mock import patch

from search_engine import normalize_text, search


def page(text: str, name: str = "sample.pdf", number: int = 1, method: str = "text") -> dict:
    return {"text": text, "name": name, "page_number": number,
            "document_path": f"C:/acervo/{name}", "extraction_method": method}


class SearchNormalizationTests(unittest.TestCase):
    def setUp(self):
        documents = patch("search_engine.all_documents", return_value=[])
        documents.start()
        self.addCleanup(documents.stop)

    def test_normalizes_case_accents_punctuation_and_duplicate_spaces(self):
        self.assertEqual(
            normalize_text("  TRÁTAMENTO---superficial!!   DUPLO "),
            "tratamento superficial duplo",
        )

    def test_exact_search_returns_ascii_keys_and_extraction_method(self):
        with patch("search_engine.all_pages", return_value=[page("TRÁTAMENTO---SUPERFICIAL   DUPLO", method="ocr")]):
            results = search("unused", "tratamento superficial duplo")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "exact")
        self.assertEqual(results[0]["extraction_method"], "ocr")
        self.assertEqual(results[0]["page"], 1)
        self.assertIn("page", results[0])
        self.assertNotIn("Página", results[0])

    def test_acronym_expands_both_directions(self):
        with patch("search_engine.all_pages", return_value=[page("Tratamento Superficial Duplo")]):
            result = search("unused", "TSD")
        self.assertEqual(result[0]["match_type"], "acronym")
        self.assertEqual(result[0]["matched_text"], "Tratamento Superficial Duplo")

        with patch("search_engine.all_pages", return_value=[
            page("Execução de banho diluído sobre TSD na pista")
        ]):
            result = search("unused", "Tratamento Superficial Duplo")
        self.assertEqual(result[0]["match_type"], "acronym")
        self.assertEqual(result[0]["matched_text"], "TSD")

    def test_related_term_is_labeled_and_opt_in(self):
        data = [page("Tratamento Superficial Duplo")]
        with patch("search_engine.all_pages", return_value=data):
            self.assertEqual(search("unused", "tratamento superficial em duas camadas"), [])
            result = search("unused", "tratamento superficial em duas camadas", include_related=True)
        self.assertEqual(result[0]["match_type"], "related_term")
        self.assertIn("não necessariamente equivalente", result[0]["relation"])

    def test_fuzzy_search_finds_small_ocr_typo(self):
        with patch("search_engine.all_pages", return_value=[page("Tratamento Superficial Dupl0")]):
            result = search("unused", "tratamento superficial duplo", fuzzy=True)
        self.assertTrue(result)
        self.assertEqual(result[0]["match_type"], "approximate")
        self.assertGreaterEqual(result[0]["relevance"], 50)
        self.assertLess(result[0]["relevance"], 86)

    def test_filename_search_matches_complete_name_once_per_pdf(self):
        data = [
            page("Texto sem relação", name="Relatorio-Compactacao.PDF", number=1),
            page("Outra página", name="Relatorio-Compactacao.PDF", number=2),
        ]
        documents = [{"path": "C:/acervo/Relatorio-Compactacao.PDF",
                      "name": "Relatorio-Compactacao.PDF"}]
        with patch("search_engine.all_pages", return_value=data), \
                patch("search_engine.all_documents", return_value=documents):
            results = search("unused", "relatorio-compactacao.pdf")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "filename")
        self.assertEqual(results[0]["pdf"], "Relatorio-Compactacao.PDF")
        self.assertIsNone(results[0]["page"])
        self.assertEqual(results[0]["source"], "filename")

    def test_filename_search_matches_partial_name(self):
        pages = [page("Conteúdo fictício", name="Relatorio-Compactacao.PDF")]
        documents = [{"path": "C:/acervo/Relatorio-Compactacao.PDF",
                      "name": "Relatorio-Compactacao.PDF"}]
        with patch("search_engine.all_pages", return_value=pages), \
                patch("search_engine.all_documents", return_value=documents):
            results = search("unused", "compactacao")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "filename")
        self.assertIsNone(results[0]["page"])

    def test_filename_search_returns_no_result_when_name_and_content_do_not_match(self):
        with patch("search_engine.all_pages", return_value=[
            page("Texto fictício", name="Relatorio-Compactacao.PDF")
        ]):
            self.assertEqual(search("unused", "ponte", fuzzy=False), [])

    def test_content_search_keeps_page_and_snippet_without_filename_match(self):
        with patch("search_engine.all_pages", return_value=[
            page("Concreto fck 25 MPa", name="ensaio.pdf", number=3)
        ]):
            results = search("unused", "fck 25 MPa")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "exact")
        self.assertEqual(results[0]["page"], 3)
        self.assertIn("Concreto fck 25 MPa", results[0]["snippet"])

    def test_filename_search_works_without_extracted_pages(self):
        documents = [{"path": "C:/acervo/Anexo-sem-texto.pdf",
                      "name": "Anexo-sem-texto.pdf"}]
        with patch("search_engine.all_pages", return_value=[]), \
                patch("search_engine.Path.is_file", return_value=True), \
                patch("search_engine.all_documents", return_value=documents):
            results = search("unused", "anexo-sem-texto")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "filename")
        self.assertEqual(results[0]["pdf"], "Anexo-sem-texto.pdf")
        self.assertIsNone(results[0]["page"])

    def test_content_and_filename_match_share_one_explicit_content_result(self):
        pages = [page("Concreto fck 25 MPa", name="concreto-ensaio.pdf", number=2)]
        documents = [{"path": "C:/acervo/concreto-ensaio.pdf",
                      "name": "concreto-ensaio.pdf"}]
        with patch("search_engine.all_pages", return_value=pages), \
                patch("search_engine.all_documents", return_value=documents):
            results = search("unused", "concreto")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "exact")
        self.assertEqual(results[0]["page"], 2)
        self.assertTrue(results[0]["filename_match"])


if __name__ == "__main__":
    unittest.main()
