"""Synthetic table/layout tests; these tests never open an acervo PDF."""
import unittest

from certificate_extractor import (
    TableCell,
    TableGrid,
    TableRow,
    extract_page,
    extract_table,
    normalize_unit,
    parse_localized_quantity,
)


def cell(text, column, row, *, x_width=18.0):
    x0 = float(column * 20)
    y0 = float(row * 10)
    return TableCell(text, (x0, y0, x0 + x_width, y0 + 8.0))


def standard_header(row=1):
    return TableRow((
        cell("Item", 0, row),
        cell("Especificação do Serviço", 1, row, x_width=130),
        cell("Und", 2, row),
        cell("Quant", 3, row),
    ), row)


def standard_row(number, service, unit, quantity, row, *, coords=True):
    if coords:
        cells = (cell(number, 0, row), cell(service, 1, row, x_width=130),
                 cell(unit, 2, row), cell(quantity, 3, row))
    else:
        cells = tuple(TableCell(value) for value in (number, service, unit, quantity))
    return TableRow(cells, row)


class SyntheticCertificateExtractorTests(unittest.TestCase):
    def extract_rows(self, rows, *, page=3):
        return extract_table(TableGrid(tuple(rows)), page_number=page, source_method="native")

    def test_01_extracts_simple_service_description(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Execução de pavimentação", "m²", "245,50", 2),
        ])
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].service_description_original, "Execução de pavimentação")
        self.assertEqual(result.items[0].item_type, "service")

    def test_02_joins_multiline_description_and_preserves_both_evidence_rows(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Execução de pavimentação", "m²", "245,50", 2),
            standard_row("", "em paralelepípedo granítico", "", "", 3),
        ])
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item.service_description_original,
                         "Execução de pavimentação\nem paralelepípedo granítico")
        self.assertIn("em paralelepípedo granítico", item.evidence_text)
        self.assertEqual(item.evidence_coordinates["y1"], 38.0)
        self.assertIn("continu", result.ignored_rows[-1].reason)

    def test_03_parses_brazilian_decimal_comma_without_changing_raw_value(self):
        result = self.extract_rows([
            standard_header(), standard_row("1", "Concreto", "m³", "1234,56", 2),
        ])
        item = result.items[0]
        self.assertEqual(item.quantity_original, "1234,56")
        self.assertEqual(item.quantity_normalized, "1234.56")

    def test_04_parses_brazilian_thousands_and_decimal_separators(self):
        result = self.extract_rows([
            standard_header(), standard_row("1", "Escavação", "m³", "1.234,56", 2),
        ])
        self.assertEqual(result.items[0].quantity_original, "1.234,56")
        self.assertEqual(result.items[0].quantity_normalized, "1234.56")
        self.assertEqual(parse_localized_quantity("1.234.567"), ("1234567", None))
        self.assertIsNotNone(parse_localized_quantity("1.234")[1])

    def test_05_keeps_square_and_cubic_meters_distinct(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Pavimentação", "m²", "10,00", 2),
            standard_row("2", "Aterro", "m³", "10,00", 3),
        ])
        self.assertEqual([item.unit_normalized for item in result.items], ["m²", "m³"])
        self.assertEqual(normalize_unit("metros quadrados"), "m²")
        self.assertEqual(normalize_unit("m3"), "m³")

    def test_06_normalizes_mass_length_time_and_count_units(self):
        expected = [("kg", "kg"), ("t", "t"), ("m", "m"),
                    ("hora", "h"), ("unidade", "un")]
        for original, canonical in expected:
            with self.subTest(unit=original):
                self.assertEqual(normalize_unit(original), canonical)
        rows = [standard_header()]
        rows.extend(standard_row(str(i), f"Serviço {unit}", unit, "2,00", i + 1)
                    for i, (unit, _) in enumerate(expected))
        result = self.extract_rows(rows)
        self.assertEqual([item.unit_normalized for item in result.items],
                         [canonical for _, canonical in expected])

    def test_07_repeated_headers_are_reported_and_not_emitted_as_services(self):
        result = self.extract_rows([
            standard_header(1),
            standard_row("1", "Escavação de vala", "m³", "30,00", 2),
            standard_header(3),
            standard_row("2", "Reaterro compactado", "m³", "20,00", 4),
        ])
        self.assertEqual(len(result.items), 2)
        self.assertEqual([row.reason for row in result.ignored_rows],
                         ["cabeçalho de tabela", "cabeçalho repetido"])

    def test_08_subtotals_and_totals_are_separate_from_service_rows(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Pavimentação", "m²", "500,00", 2),
            standard_row("", "Subtotal pavimentação", "", "500,00", 3),
            standard_row("", "TOTAL GERAL", "", "500,00", 4),
        ])
        self.assertEqual([item.item_type for item in result.items], ["service", "subtotal", "total"])
        self.assertEqual(result.items[1].service_description_original, "Subtotal pavimentação")

    def test_09_quantity_and_unit_stay_with_their_own_service_row(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Escavação", "m³", "12,50", 2),
            standard_row("2", "Compactação", "m²", "125,00", 3),
        ])
        self.assertEqual([(item.service_description_original, item.quantity_normalized,
                           item.unit_normalized) for item in result.items], [
            ("Escavação", "12.50", "m³"),
            ("Compactação", "125.00", "m²"),
        ])

    def test_10_evidence_coordinates_cover_row_cells_and_page_is_preserved(self):
        result = extract_table(TableGrid((
            standard_header(), standard_row("1", "Execução de guia", "m", "15,00", 2),
        )), page_number=7, source_method="native", document_path=r"C:\fake\pilot.pdf")
        item = result.items[0]
        self.assertEqual(item.document_path, r"C:\fake\pilot.pdf")
        self.assertEqual(item.page_number, 7)
        self.assertEqual(item.evidence_coordinates, {"x0": 0.0, "y0": 20.0, "x1": 150.0, "y1": 28.0})
        self.assertIn("Execução de guia", item.evidence_text)
        self.assertEqual(item.source_method, "native")

    def test_11_multiple_quantities_remain_unresolved_for_human_review(self):
        result = self.extract_rows([
            standard_header(), standard_row("1", "Serviço com alternativa", "m²", "10,00 / 20,00", 2),
        ])
        item = result.items[0]
        self.assertIsNone(item.quantity_normalized)
        self.assertEqual(item.quantity_original, "10,00 / 20,00")
        self.assertEqual(item.review_status, "pending")
        self.assertIn("mais de uma quantidade", " ".join(item.review_reasons))

    def test_12_same_structural_occurrence_is_deduplicated_but_reported(self):
        row = standard_row("1", "Serviço repetido", "m", "2,00", 2)
        result = self.extract_rows([standard_header(), row, row])
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(result.duplicate_rows), 1)
        self.assertIn("mesma ocorr", result.duplicate_rows[0].reason)

    def test_unheaded_line_without_unique_value_and_unit_stays_pending(self):
        row = TableRow((TableCell("Serviço 10,00 e 20,00 m²", (1, 2, 100, 12)),), 1)
        result = self.extract_rows([row])
        self.assertEqual(len(result.items), 1)
        self.assertIsNone(result.items[0].quantity_normalized)
        self.assertEqual(result.items[0].review_status, "pending")
        self.assertTrue(result.items[0].review_reasons)

    def test_pymupdf_page_adapter_uses_synthetic_table_object_only(self):
        class FakeTable:
            bbox = (0.0, 0.0, 100.0, 40.0)
            cells = [(0, 0, 10, 8), (10, 0, 80, 8), (80, 0, 90, 8), (90, 0, 100, 8),
                     (0, 10, 10, 18), (10, 10, 80, 18), (80, 10, 90, 18), (90, 10, 100, 18)]
            def extract(self):
                return [["Item", "Especificação do Serviço", "Und", "Quant"],
                        ["1", "Serviço sintético", "kg", "5,00"]]

        class FakeFinder:
            tables = [FakeTable()]

        class FakePage:
            def __init__(self):
                self.calls = []
            def find_tables(self, **kwargs):
                self.calls.append(kwargs)
                return FakeFinder()
            def get_text(self, *_args, **_kwargs):
                raise AssertionError("fallback não deve ser usado quando a tabela existe")

        page = FakePage()
        report = extract_page(page, page_number=1)
        self.assertEqual(len(page.calls), 1)
        self.assertEqual(report.items[0].service_description_original, "Serviço sintético")
        self.assertEqual(report.items[0].unit_normalized, "kg")

    def test_subtotal_label_with_item_number_is_not_a_service(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Pavimentacao", "m2", "10,00", 2),
            standard_row("9", "TOTAL GERAL", "", "10,00", 3),
        ])
        self.assertEqual([item.item_type for item in result.items], ["service", "total"])
        self.assertEqual(result.items[-1].service_description_original, "TOTAL GERAL")

    def test_ambiguous_or_nonadjacent_description_is_not_joined(self):
        result = self.extract_rows([
            standard_header(),
            standard_row("1", "Servico original", "m", "5,00", 2),
            standard_row("", "linha sem coordenadas", "", "", 3, coords=False),
        ])
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0].service_description_original, "Servico original")

    def test_invalid_coordinates_are_omitted_and_flagged_for_review(self):
        row = TableRow((TableCell("Descricao", (10, 10, 5, 20)),
                        TableCell("m", (float("nan"), 1, 2, 3)),
                        TableCell("2,00", (40, 10, 50, 20))), 1)
        result = self.extract_rows([row])
        self.assertTrue(result.items)
        item = result.items[0]
        self.assertEqual(item.evidence_coordinates, {"x0": 40.0, "y0": 10.0, "x1": 50.0, "y1": 20.0})
        self.assertTrue(any("coordenada" in r for r in item.review_reasons))

    def test_confidence_is_qualitative_not_a_probability(self):
        result = self.extract_rows([standard_header(), standard_row("1", "Servico", "m", "2,00", 2)])
        item = result.items[0]
        self.assertIn(item.reading_confidence, {"not_assessed", "low", "medium", "high"})
        self.assertIn(item.interpretation_confidence, {"not_assessed", "low", "medium", "high"})
        self.assertNotIsInstance(item.reading_confidence, float)

    def test_unassociated_numeric_row_is_preserved_in_report(self):
        row = TableRow((TableCell("2,00 m2", (1, 1, 20, 8)),), 1)
        result = self.extract_rows([row])
        self.assertEqual(len(result.unassociated_rows), 1)
        self.assertIn("sem associacao", result.unassociated_rows[0].reason)

    def test_installed_pymupdf_accepts_synthetic_in_memory_page(self):
        import pymupdf
        document = pymupdf.open()
        try:
            page = document.new_page()
            page.insert_text((30, 30), "Descricao Servico Quantidade Unidade")
            page.insert_text((30, 55), "Pavimentacao 12,50 m2")
            report = extract_page(page, page_number=1)
            self.assertTrue(callable(getattr(page, "find_tables", None)))
            self.assertIsInstance(report.items, tuple)
            self.assertEqual(report.items[0].source_method, "native")
        finally:
            document.close()

    def test_two_continuation_rows_keep_each_service_box_and_evidence(self):
        result = self.extract_rows([
            standard_header(1),
            standard_row("1", "Descricao principal", "m", "4,00", 2),
            standard_row("", "continuacao um", "", "", 3),
            standard_row("", "continuacao dois", "", "", 4),
        ])
        item = result.items[0]
        self.assertIn("continuacao um", item.service_description_original)
        self.assertIn("continuacao dois", item.service_description_original)
        segments = item.field_coordinates["service_segments"]
        self.assertEqual([segment["row_number"] for segment in segments], [2, 3, 4])
        self.assertEqual([segment["text"] for segment in segments],
                         ["Descricao principal", "continuacao um", "continuacao dois"])
        self.assertEqual([segment["coordinates"]["y0"] for segment in segments], [20.0, 30.0, 40.0])
        self.assertIn("continuacao um", item.evidence_text)
        self.assertIn("continuacao dois", item.evidence_text)

    def test_repeated_totals_and_subtotals_are_deduplicated(self):
        subtotal = standard_row("", "Subtotal geral", "", "4,00", 3)
        total = standard_row("", "TOTAL GERAL", "", "4,00", 4)
        report = self.extract_rows([standard_header(), subtotal, subtotal, total, total])
        self.assertEqual([item.item_type for item in report.items], ["subtotal", "total"])
        self.assertEqual(len(report.duplicate_rows), 2)
        self.assertTrue(all("subtotal/total" in row.reason for row in report.duplicate_rows))

    def test_total_is_detected_without_header_even_with_embedded_item_number(self):
        row = TableRow((TableCell("Item 9 - Total geral", (10, 20, 150, 30)),
                        TableCell("12,00", (160, 20, 190, 30))), 1)
        report = self.extract_rows([row])
        self.assertEqual(len(report.items), 1)
        self.assertEqual(report.items[0].item_type, "total")

    def test_signed_quantities_are_preserved_and_invalid_values_stay_unresolved(self):
        negative = self.extract_rows([standard_header(), standard_row("1", "Ajuste", "m", "-2,50", 2)])
        self.assertEqual(negative.items[0].quantity_original, "-2,50")
        self.assertEqual(negative.items[0].quantity_normalized, "-2.50")
        self.assertIsNone(parse_localized_quantity("Infinity")[0])
        invalid = self.extract_rows([standard_header(), standard_row("1", "Valor", "m", "1,2,3", 2)])
        self.assertIsNone(invalid.items[0].quantity_normalized)
        self.assertTrue(invalid.items[0].review_reasons)

    def test_find_tables_exception_and_empty_table_use_safe_fallback(self):
        class EmptyTable:
            bbox = (0, 0, 100, 40)
            cells = []
            rows = []
            def extract(self):
                return []
        class Finder:
            tables = [EmptyTable()]
        class FailingPage:
            def __init__(self): self.calls = 0
            def find_tables(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("synthetic finder failure")
                return Finder()
            def get_text(self, mode, **_kwargs):
                if mode == "words":
                    return [(10, 10, 40, 18, "Servico", 0, 0, 0),
                            (50, 10, 70, 18, "2,00", 0, 0, 1),
                            (75, 10, 90, 18, "m", 0, 0, 2)]
                return "Servico 2,00 m"
        report = extract_page(FailingPage(), page_number=1)
        self.assertTrue(any("finder failure" in warning for warning in report.warnings))
        self.assertTrue(any("sem celulas" in warning for warning in report.warnings))
        self.assertTrue(report.items)

    def test_missing_coordinates_are_kept_as_unverified_evidence(self):
        row = standard_row("1", "Servico sem caixas", "m", "2,00", 2, coords=False)
        report = self.extract_rows([standard_header(), row])
        item = report.items[0]
        self.assertEqual(item.evidence_coordinates, {})
        self.assertTrue(any("coordenadas da evid" in reason
                            for reason in item.review_reasons))
        self.assertEqual(item.interpretation_confidence, "low")


if __name__ == "__main__":
    unittest.main()
