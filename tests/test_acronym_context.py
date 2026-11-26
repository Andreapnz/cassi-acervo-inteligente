import unittest
from unittest.mock import patch

from search_engine import search


def page(text, name, number=1, path=None):
    return {
        "text": text,
        "native_text": "",
        "ocr_text": text,
        "name": name,
        "page_number": number,
        "document_path": path if path is not None else f"C:/acervo/{name}",
        "extraction_method": "ocr",
    }


class TechnicalAcronymContextTests(unittest.TestCase):
    def test_fuzzy_tss_never_matches_the_distinct_tsd_concept(self):
        pages = [page(
            "PavimentaÃ§Ã£o com Tratamento Superficial Duplo (TSD).", "duplo.pdf"
        )]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "TSS", fuzzy=True, include_related=True)
        self.assertEqual(rows, [])

    def test_fuzzy_tsd_never_matches_the_distinct_tss_concept(self):
        pages = [page(
            "PavimentaÃ§Ã£o com Tratamento Superficial Simples (TSS).", "simples.pdf"
        )]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "TSD", fuzzy=True, include_related=True)
        self.assertEqual(rows, [])

    def test_fuzzy_search_still_tolerates_ocr_typo_in_same_concept(self):
        pages = [page("PavimentaÃ§Ã£o com Tratamento Superficial Simpels.", "ocr.pdf")]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "TSS", fuzzy=True)
        self.assertTrue(rows)
        self.assertTrue(any(row["match_type"] == "approximate" for row in rows))

    def test_fuzzy_technical_phrase_requires_its_distinguishing_word(self):
        pages = [page(
            "Tratamento superficial da faixa de domÃ­nio.", "generic-treatment.pdf"
        )]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "TSD", fuzzy=True)
        self.assertEqual(rows, [])

        pages = [page("Tratamento superficial dupl0 na pista.", "ocr-duplo.pdf")]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "TSD", fuzzy=True)
        self.assertTrue(any(row["match_type"] == "ocr_variant" for row in rows))

    def test_legal_ocr_fragment_prof_tss_tonal_is_not_a_tss_result(self):
        text = (
            "Petição do licitante. ART PROF TSS TONAL. "
            "CARLOS CESAR FERNANDES DINIZ. Título: Engenheiro Civil."
        )
        pages = [
            page(text, "0771-2002.pdf"),
            page(text, "6062-2002.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=pages):
            results = search("unused", "TSS")
        self.assertEqual(results, [])

    def test_isolated_or_fragmented_letters_do_not_count_as_acronym(self):
        texts = [
            "Texto comum com TSS isolado, sem contexto técnico.",
            "Identificação: PROF TSS TONAL. Dados jurídicos do contrato.",
            "Identificação: PROF T S S TONAL. Dados cadastrais.",
            "A palavra PROFISSIONAL aparece no documento.",
        ]
        pages = [page(text, f"legal-{index}.pdf") for index, text in enumerate(texts)]
        with patch("search_engine.all_pages", return_value=pages):
            self.assertEqual(search("unused", "TSS"), [])

    def test_tss_and_tsd_require_their_own_technical_context(self):
        pages = [
            page("Tratamento superficial simples com ligante TSS.", "simples.pdf"),
            page("Aplicação de banho diluído sobre TSD (RR-2C).", "duplo.pdf"),
            page("PROF TSS TONAL.", "false-positive.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=pages):
            tss = search("unused", "TSS")
            tsd = search("unused", "TSD")

        self.assertTrue(tss)
        self.assertTrue(tsd)
        self.assertEqual({row["pdf"] for row in tss}, {"simples.pdf"})
        self.assertEqual({row["pdf"] for row in tsd}, {"duplo.pdf"})
        direct_tss = [row for row in tss if row["matched_text"] == "TSS"]
        direct_tsd = [row for row in tsd if row["matched_text"] == "TSD"]
        self.assertTrue(direct_tss)
        self.assertTrue(direct_tsd)
        self.assertTrue(all(row["match_type"] == "acronym" for row in direct_tss + direct_tsd))

    def test_full_tss_name_remains_searchable_and_expands_to_tss(self):
        pages = [page("Tratamento superficial simples com ligante.", "work.pdf")]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "Tratamento Superficial Simples")
        self.assertTrue(any(row["matched_text"] == "Tratamento superficial simples"
                            and row["match_type"] == "exact" for row in rows))

    def test_results_without_valid_document_page_or_excerpt_are_omitted(self):
        pages = [
            page("Concreto armado na fundação.", "valid.pdf", 3),
            page("Concreto armado em trecho sem caminho.", "", 0, path=""),
        ]
        with patch("search_engine.all_pages", return_value=pages):
            rows = search("unused", "concreto armado")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["pdf"], "valid.pdf")
        self.assertGreater(row["page"], 0)
        self.assertTrue(row["snippet"].strip())
        self.assertTrue(row["path"].strip())


if __name__ == "__main__":
    unittest.main()
