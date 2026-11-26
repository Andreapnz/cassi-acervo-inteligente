import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import initialize, replace_document
from search_engine import _normalized_with_map, normalize_text, search


def page(text, name="tecnico.pdf", number=1, method="text", native=None, ocr=None):
    return {
        "text": text,
        "native_text": text if native is None and method == "text" else native or "",
        "ocr_text": text if ocr is None and method == "ocr" else ocr or "",
        "name": name,
        "page_number": number,
        "document_path": f"C:/acervo/{name}",
        "extraction_method": method,
    }


class TechnicalSearchTests(unittest.TestCase):
    def test_fast_search_normalization_matches_excerpt_offset_normalization(self):
        samples = [
            "CAFÉ de TERRAPLÉNAGEM",
            "T.S.D. e Tratamento Superficial Duplo",
            "346,00m³/h ou 346.00 metros cúbicos por hora",
            "Straße — São Paulo",
        ]
        for sample in samples:
            normalized, offsets = _normalized_with_map(sample)
            with self.subTest(sample=sample):
                self.assertEqual(normalized, normalize_text(sample))
                self.assertEqual(len(normalized), len(offsets))

    def test_tsd_and_tss_are_explicit_equivalences_in_both_directions(self):
        data = [
            page("Execução de banho diluído sobre TSD na pista.", "dup..pdf", 1),
            page("Tratamento superficial duplo previsto.", "duplo.pdf", 1),
            page("TSS aplicado em tratamento superficial simples com ligante.", "simples.pdf", 1),
            page("Tratamento superficial simples especificado.", "simples2.pdf", 1),
        ]
        with patch("search_engine.all_pages", return_value=data):
            tsd = search("unused", "TSD", fuzzy=True, include_related=True)
            full_tsd = search("unused", "tratamento superficial duplo", fuzzy=True,
                              include_related=True)
            tss = search("unused", "TSS", fuzzy=True, include_related=True)
            full_tss = search("unused", "tratamento superficial simples", fuzzy=True,
                              include_related=True)

        self.assertTrue(any(row["matched_text"] == "Tratamento superficial duplo"
                            and row["match_type"] == "acronym" for row in tsd))
        self.assertTrue(any(row["matched_text"] == "TSD" and row["match_type"] == "acronym"
                            for row in full_tsd))
        self.assertTrue(any(row["matched_text"] == "Tratamento superficial simples"
                            and row["match_type"] == "acronym" for row in tss))
        self.assertTrue(any(row["matched_text"] == "TSS" and row["match_type"] == "acronym"
                            for row in full_tss))

    def test_construction_terms_are_related_only_when_opted_in(self):
        cases = [
            ("Terraplenagem", "Aterro compactado."),
            ("Pavimentação", "Imprimação da base."),
            ("Drenagem", "Execução de sarjeta."),
            ("Concreto", "Resistência característica fck."),
            ("Topografia", "Serviço de nivelamento."),
        ]
        for query, text in cases:
            with self.subTest(query=query), patch(
                "search_engine.all_pages", return_value=[page(text)]
            ):
                self.assertEqual(search("unused", query), [])
                hits = search("unused", query, include_related=True)
                self.assertTrue(hits)
                self.assertEqual(hits[0]["match_type"], "related_term")
                self.assertIn("não necessariamente equivalente", hits[0]["relation"])

    def test_related_anchor_can_find_its_category_without_equating_them(self):
        with patch("search_engine.all_pages", return_value=[page("Corte e aterro da plataforma.")]):
            self.assertEqual(search("unused", "Terraplenagem"), [])
            hits = search("unused", "Terraplenagem", include_related=True)
        self.assertTrue(any(row["matched_text"].casefold() == "corte"
                            and row["match_type"] == "related_term" for row in hits))

    def test_drainage_does_not_fuzzy_match_escoamento_to_escoramento(self):
        data = [page("Escoramento da vala e da funda\u00e7\u00e3o.", "escoramento.pdf")]
        with patch("search_engine.all_pages", return_value=data):
            self.assertEqual(search("unused", "Drenagem", fuzzy=True, include_related=True), [])
            self.assertEqual(search("unused", "escoamento", fuzzy=True), [])

        data = [page("Escoamento superficial da \u00e1gua pluvial.", "escoamento.pdf")]
        with patch("search_engine.all_pages", return_value=data):
            related = search("unused", "Drenagem", include_related=True)
        self.assertEqual(len(related), 1)
        self.assertEqual(related[0]["match_type"], "related_uncertain")
        self.assertLess(related[0]["relevance"], 88)
        self.assertIn("menor confian\u00e7a", related[0]["match_reason"])
        self.assertIn("n\u00e3o confirmada como equival\u00eancia", related[0]["match_reason"])

    def test_drainage_keeps_exact_and_core_related_concepts_distinct(self):
        data = [
            page("Drenagem longitudinal da plataforma.", "drenagem.pdf"),
            page("Bueiro tubular sob a estrada.", "bueiro.pdf"),
            page("Sarjeta de concreto moldada no local.", "sarjeta.pdf"),
            page("Galeria de drenagem em concreto.", "galeria.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            exact_only = search("unused", "Drenagem")
            expanded = search("unused", "Drenagem", include_related=True)
        self.assertTrue(exact_only)
        self.assertTrue(all(row["match_type"] == "exact" for row in exact_only))
        self.assertEqual({row["pdf"] for row in exact_only}, {"drenagem.pdf", "galeria.pdf"})
        related = {row["pdf"]: row["match_type"] for row in expanded
                   if row["pdf"] != "drenagem.pdf"}
        self.assertEqual(related, {
            "bueiro.pdf": "related_term",
            "sarjeta.pdf": "related_term",
            "galeria.pdf": "related_term",
        })

    def test_drainage_tubulacao_requires_specific_local_drainage_context(self):
        data = [
            page("Tubula\u00e7\u00e3o PVC da rede de drenagem pluvial.", "rede_pluvial.pdf"),
            page("Tubula\u00e7\u00e3o para condu\u00e7\u00e3o de \u00e1guas pluviais.", "aguas_pluviais.pdf"),
            page("Tubula\u00e7\u00e3o de \u00e1gua pot\u00e1vel para abastecimento da obra.", "abastecimento.pdf"),
            page("Rede hidr\u00e1ulica predial com tubula\u00e7\u00e3o de \u00e1gua fria.", "hidraulica.pdf"),
            page("Tubula\u00e7\u00e3o da instala\u00e7\u00e3o el\u00e9trica junto ao quadro.", "eletrica.pdf"),
            page("Tubula\u00e7\u00e3o da rede coletora de esgoto sanit\u00e1rio.", "esgoto.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "Drenagem", include_related=True)
        retained = {row["pdf"] for row in hits
                    if normalize_text(row["matched_term"]) == "tubulacao"}
        self.assertEqual(retained, {"rede_pluvial.pdf", "aguas_pluviais.pdf"})
        for row in hits:
            if normalize_text(row["matched_term"]) == "tubulacao":
                self.assertEqual(row["match_type"], "related_term")
                self.assertIn("Contexto local confirmado", row["match_reason"])
                self.assertTrue(row["snippet"] and row["page"] >= 1 and row["pdf"])

    def test_drainage_tubulacao_rejects_non_drainage_cues_even_when_nearby(self):
        data = [
            page("Drenagem da \u00e1rea; tubula\u00e7\u00e3o de \u00e1gua pot\u00e1vel da rede de abastecimento.",
                 "water-near-drainage.pdf"),
            page("Bueiro pr\u00f3ximo \u00e0 tubula\u00e7\u00e3o da instala\u00e7\u00e3o el\u00e9trica.",
                 "electric-near-bueiro.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "Drenagem", include_related=True)
        self.assertFalse(any(normalize_text(row["matched_term"]) == "tubulacao"
                             for row in hits))

    def test_drainage_escoamento_rejects_soil_context_but_keeps_water_context(self):
        data = [
            page("Escoamento ou ruptura do terreno das funda\u00e7\u00f5es; descompress\u00e3o do terreno pela \u00e1gua.",
                 "fundacao.pdf"),
            page("Escoamento superficial da \u00e1gua pluvial.", "pluvial.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "Drenagem", include_related=True)
        self.assertEqual([row["pdf"] for row in hits], ["pluvial.pdf"])
        self.assertEqual(hits[0]["match_type"], "related_uncertain")
        self.assertIn("Evidência local de menor confiança", hits[0]["match_reason"])

    def test_direct_tubulacao_and_escoamento_searches_remain_available(self):
        data = [
            page("Tubula\u00e7\u00e3o em PVC da instala\u00e7\u00e3o predial.", "tubulacao.pdf"),
            page("Escoamento ou ruptura do terreno das funda\u00e7\u00f5es.", "escoamento.pdf"),
            page("Escoramento da vala.", "escoramento.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            tubing = search("unused", "tubula\u00e7\u00e3o")
            flow = search("unused", "escoamento", fuzzy=True)
            shoring = search("unused", "escoramento", fuzzy=True)
        self.assertTrue(any(row["pdf"] == "tubulacao.pdf" and row["match_type"] == "exact"
                            for row in tubing))
        self.assertTrue(any(row["pdf"] == "escoamento.pdf" and row["match_type"] == "exact"
                            for row in flow))
        self.assertFalse(any(row["pdf"] == "escoramento.pdf" for row in flow))
        self.assertTrue(any(row["pdf"] == "escoramento.pdf" and row["match_type"] == "exact"
                            for row in shoring))

    def test_distant_esgoto_does_not_validate_water_point_tubulacao(self):
        data = [page("Ponto de esgoto anterior. Ponto de agua com tubulacao em PVC rigido.",
                     "ponto_agua.pdf")]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "Drenagem", include_related=True)
        self.assertFalse(any(row["matched_term"].casefold() == "tubula\u00e7\u00e3o" for row in hits))

    def test_ocr_escoramento_variant_is_not_accepted_as_escoamento(self):
        data = [page("Escoroamento da vala.", "ocr_escoramento.pdf", method="ocr")]
        with patch("search_engine.all_pages", return_value=data):
            flow = search("unused", "escoamento", fuzzy=True)
            shoring = search("unused", "escoramento", fuzzy=True)
        self.assertEqual(flow, [])
        self.assertEqual(shoring[0]["match_type"], "ocr_variant")

    def test_terraplenagem_corte_requires_earthwork_context_and_keeps_direct_search(self):
        data = [
            page("Corte de subleito em solo natural.", "subleito.pdf"),
            page("Regularizacao mecanica: corte ou aterro do terreno.", "corte_aterro.pdf"),
            page("Valeta de protecao de corte/aterro.", "talude.pdf"),
            page("Execucao de sarjetas de corte em concreto.", "sarjetas.pdf"),
            page("Escavado o reservatorio, a seccao do reservatorio (em corte) sera regularizada.",
                 "reservatorio.pdf"),
            page("Escavacdo de corte, emprestimo ou jazidas em material de primeira categoria.",
                 "jazida.pdf"),
            page("Ferro/corte, dobragem e colocacao de armadura.", "armadura.pdf"),
            page("Corte da geomembrana existente.", "geomembrana.pdf"),
            page("Corte de tubulacao em RPVC.", "tubulacao.pdf"),
            page("Corte de vegetacao de porte arboreo.", "vegetacao.pdf"),
            page("Corte em alvenaria.", "alvenaria.pdf"),
            page("Corte e religacao de pena d'agua.", "ligacao_agua.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            related = search("unused", "Terraplenagem", include_related=True)
            direct = search("unused", "corte")
        cut_hits = [row for row in related if row["matched_term"].casefold() == "corte"]
        self.assertEqual({row["pdf"] for row in cut_hits},
                         {"subleito.pdf", "corte_aterro.pdf", "talude.pdf", "sarjetas.pdf",
                          "reservatorio.pdf", "jazida.pdf"})
        self.assertTrue(all("Contexto do trecho" in row["match_reason"] for row in cut_hits))
        self.assertEqual(len(direct), 12)
        self.assertTrue(all(row["match_type"] == "exact" for row in direct))

    def test_aterro_sanitario_and_art_excavation_are_unconfirmed_separately(self):
        data = [
            page("Manutencao do aterro sanitario.", "sanitario.pdf"),
            page("Restricoes e observacoes da ART: nao possui atribuicoes para escavacao.",
                 "art.pdf"),
            page("Profissional nao possutATRIBUCOES PARA escavacao de material com explosivos.",
                 "art_ocr.pdf"),
            page("Escavacao manual de valas em solo de primeira categoria.", "valas.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            related = search("unused", "Terraplenagem", include_related=True)
            direct_aterro = search("unused", "aterro")
            direct_escavacao = search("unused", "escavacao")
        by_pdf = {row["pdf"]: row for row in related}
        self.assertEqual(by_pdf["sanitario.pdf"]["match_type"], "related_uncertain")
        self.assertIn("não confirmada", by_pdf["sanitario.pdf"]["match_reason"])
        self.assertEqual(by_pdf["art.pdf"]["match_type"], "related_uncertain")
        self.assertEqual(by_pdf["art_ocr.pdf"]["match_type"], "related_uncertain")
        self.assertEqual(by_pdf["valas.pdf"]["match_type"], "related_term")
        self.assertIn("Contexto do trecho", by_pdf["valas.pdf"]["match_reason"])
        self.assertTrue(any(row["pdf"] == "sanitario.pdf" and row["match_type"] == "exact"
                            for row in direct_aterro))
        self.assertTrue(any(row["pdf"] == "valas.pdf" and row["match_type"] == "exact"
                            for row in direct_escavacao))

    def test_direct_earthwork_anchor_searches_are_not_context_filtered(self):
        data = [
            page("Corte de tubulacao.", "corte.pdf"),
            page("Escavacao de vala.", "escavacao.pdf"),
            page("Aterro sanitario.", "aterro.pdf"),
            page("Compactacao de concreto.", "compactacao.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = {term: search("unused", term) for term in
                    ("corte", "escavacao", "aterro", "compactacao")}
        self.assertTrue(all(rows and rows[0]["match_type"] == "exact"
                            for rows in hits.values()))

    def test_concreto_armado_ocr_candidates_are_not_promoted_to_exact(self):
        data = [
            page("CONCEITO ARMADO COM FORMA", "conceito.pdf", method="ocr"),
            page("CONCRETO ARMADA COM FORMA", "armada.pdf", method="ocr"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "concreto armado", fuzzy=True)
        by_pdf = {row["pdf"]: row for row in hits}
        self.assertEqual(by_pdf["conceito.pdf"]["match_type"], "approximate")
        self.assertLess(by_pdf["conceito.pdf"]["relevance"], 80)
        self.assertIn("baixa confiança", by_pdf["conceito.pdf"]["match_reason"])
        self.assertEqual(by_pdf["armada.pdf"]["match_type"], "ocr_variant")
        self.assertIn("confira o PDF", by_pdf["armada.pdf"]["match_reason"])

    def test_terraplenagem_split_ocr_is_explained_and_native_spelling_stays_approximate(self):
        data = [
            page("TER RAPLENAGEM da plataforma", "ocr.pdf", method="ocr"),
            page("TER RAPLENAGEM da plataforma", "native.pdf", method="text"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "Terraplenagem", fuzzy=True)
        by_pdf = {row["pdf"]: row for row in hits}
        self.assertEqual(by_pdf["ocr.pdf"]["match_type"], "ocr_variant")
        self.assertEqual(by_pdf["native.pdf"]["match_type"], "approximate")
        self.assertTrue(all(row["match_reason"] for row in hits))

    def test_quantities_support_decimal_and_unit_variants(self):
        cases = [
            ("12,50 m²", "12.50 metros quadrados"),
            ("346,00 m³/h", "346.00m3/h"),
            ("25 m³", "25 metros cúbicos"),
            ("2,5 km", "2.50 quilômetros"),
            ("300 kg", "300 quilogramas"),
            ("12 toneladas", "12 t"),
            ("18 m", "18 metros"),
        ]
        for query, text in cases:
            with self.subTest(query=query), patch(
                "search_engine.all_pages", return_value=[page(text, method="ocr")]
            ):
                hits = search("unused", query, fuzzy=True)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["match_type"], "exact")
                self.assertEqual(hits[0]["source"], "ocr")

    def test_numeric_search_does_not_match_similar_values_or_wrong_units(self):
        data = [
            page("346,001 m³/h 346,01 m³/h 346,00 m²/h 2,01 kg"),
            page("346.00 m3/h", name="equivalent-decimal.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "346,00 m³/h", fuzzy=True)
            near = search("unused", "2,00 kg", fuzzy=True)
        self.assertEqual([(row["pdf"], row["matched_text"]) for row in hits],
                         [("equivalent-decimal.pdf", "346.00 m3/h")])
        self.assertEqual(near, [])

    def test_brazilian_decimal_and_thousands_separators_are_distinct(self):
        data = [
            page("ExtensÃ£o: 10,00 m", "decimal-comma.pdf"),
            page("ExtensÃ£o: 10.00m", "decimal-point.pdf"),
            page("ExtensÃ£o: 10.000 M", "thousands-spaced.pdf"),
            page("ExtensÃ£o: 10.000m", "thousands-attached.pdf"),
            page("ExtensÃ£o: 10000 m", "integer-thousands.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            ten_meters = search("unused", "10,00 m", fuzzy=True)
            ten_thousand_meters = search("unused", "10.000 m", fuzzy=True)

        self.assertEqual(
            {row["pdf"] for row in ten_meters},
            {"decimal-comma.pdf", "decimal-point.pdf"},
        )
        self.assertEqual(
            {row["pdf"] for row in ten_thousand_meters},
            {"thousands-spaced.pdf", "thousands-attached.pdf", "integer-thousands.pdf"},
        )

    def test_decimal_points_thousands_and_mixed_brazilian_format(self):
        data = [
            page("10.25 m", "decimal-point.pdf"),
            page("10,25 m", "decimal-comma.pdf"),
            page("1.234,50 m", "brazilian-thousands-decimal.pdf"),
            page("1,234.50 m", "international-thousands-decimal.pdf"),
            page("1.234 m", "thousand-only.pdf"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            decimal = search("unused", "10,25 m")
            grouped_decimal = search("unused", "1.234,50 m")
            thousand = search("unused", "1.234 m")

        self.assertEqual({row["pdf"] for row in decimal},
                         {"decimal-point.pdf", "decimal-comma.pdf"})
        self.assertEqual({row["pdf"] for row in grouped_decimal},
                         {"brazilian-thousands-decimal.pdf", "international-thousands-decimal.pdf"})
        self.assertEqual({row["pdf"] for row in thousand}, {"thousand-only.pdf"})

    def test_decimal_query_does_not_match_integer_ocr_fragment(self):
        data = [
            page("Quantidade 2", "integer.pdf", method="ocr"),
            page("Quantidade 2.00", "decimal.pdf", method="ocr"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "2,00")
        self.assertEqual([(row["pdf"], row["matched_text"]) for row in hits],
                         [("decimal.pdf", "2.00")])

    def test_accent_punctuation_and_line_breaks_are_normalized_without_losing_excerpt(self):
        source = "ESCAVAÇÃO\n DE TERRAPLÉNAGEM"
        self.assertEqual(normalize_text("  TERRAPLÉNAGEM---   "), "terraplenagem")
        with patch("search_engine.all_pages", return_value=[page(source)]):
            hits = search("unused", "escavacao de terraplenagem")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["matched_text"], source)

    def test_related_results_rank_before_approximate_but_after_exact(self):
        data = [
            page("Terraplenagem no projeto.", "exact.pdf"),
            page("Aterro da plataforma.", "related.pdf"),
            page("Terraplenageem no projeto.", "approximate.pdf"),
            page("TER RAPLENAGEM no projeto.", "ocr.pdf", method="ocr"),
        ]
        with patch("search_engine.all_pages", return_value=data):
            hits = search("unused", "Terraplenagem", fuzzy=True, include_related=True)
        types = [row["match_type"] for row in hits]
        self.assertIn("exact", types)
        self.assertIn("related_term", types)
        self.assertIn("approximate", types)
        self.assertIn("ocr_variant", types)
        self.assertEqual(types, sorted(
            types, key={"exact": 0, "acronym": 1, "ocr_variant": 2,
                        "related_term": 3, "approximate": 4}.get
        ))

    def test_ocr_text_remains_original_and_is_searchable_without_reindexing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ocr-search.db"
            initialize(db)
            original_ocr = "CONCRETO ARMADO\nVolume: 12,50m³"
            replace_document(db, {
                "path": "C:/acervo/ocr.pdf", "name": "ocr.pdf",
                "sha256": "test", "indexed_at": "now",
            }, [{
                "text": original_ocr, "native_text": "", "ocr_text": original_ocr,
                "extraction_method": "ocr", "ocr_status": "recognized",
            }])
            hits = search(str(db), "12.50 metros cúbicos")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["source"], "ocr")
            self.assertEqual(hits[0]["extraction_method"], "ocr")
            self.assertEqual(hits[0]["matched_text"], "12,50m³")


if __name__ == "__main__":
    unittest.main()
