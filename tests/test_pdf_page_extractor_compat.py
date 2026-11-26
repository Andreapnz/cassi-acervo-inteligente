"""Synthetic tests for PageLayout compatibility with extract_page()."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))

from pdf_certificate_compat_adapter import to_certificate_page
from pdf_page_adapter import digital_page_records, ocr_page_records, table_page_records
from pdf_page_extractor_compat import extract_compatible_page, extract_layout_page
from test_pdf_digital_adapter import _make_synthetic_pdf as _digital_pdf
from test_pdf_ocr_adapter import _scan_page, _synthetic_image_pdf
from test_pdf_table_adapter import _draw_table, _make_pdf, _draw_complex_table


class PageExtractorCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-page-extractor-compat-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _table_layout(self, name: str, content: bytes):
        path = self.root / name
        path.write_bytes(content)
        record = table_page_records(path)[0]
        return to_certificate_page(record).layout

    @staticmethod
    def _signature(report):
        def geometry_only(value):
            if isinstance(value, dict):
                return {key: geometry_only(part) for key, part in value.items()
                        if key != "row_number"}
            if isinstance(value, list):
                return [geometry_only(part) for part in value]
            return value

        return [
            (item.service_description_original, item.unit_original, item.quantity_original,
             item.page_number, item.evidence_coordinates, geometry_only(item.field_coordinates))
            for item in report.items
        ]

    def _compare_to_expected_table_parser(self, name: str, content: bytes, expected) -> None:
        layout = self._table_layout(name, content)
        reference = reference_table_extraction(layout, "synthetic://" + name)
        actual = extract_layout_page(layout, document_path="synthetic://" + name)

        self.assertEqual(self._signature(actual), self._signature(reference))
        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original,
              item.page_number) for item in actual.items],
            expected,
        )
        self.assertTrue(all(item.evidence_coordinates for item in actual.items))

    def test_bordered_table_matches_certificate_parser(self) -> None:
        self._compare_to_expected_table_parser(
            "bordered.pdf", _make_pdf(_draw_table(bordered=True)),
            [
                ("Concreto fck 25 MPa", "m3", "12,50", 1),
                ("Lançamento de\nconcreto armado", "m", "2.500,75", 1),
                ("Serviço sem unidade", None, "2,00", 1),
            ],
        )

    def test_unbordered_table_matches_with_existing_warning(self) -> None:
        layout = self._table_layout("unbordered.pdf", _make_pdf(_draw_table(bordered=False)))
        actual = extract_layout_page(layout)
        reference = reference_table_extraction(layout)

        self.assertEqual(self._signature(actual), self._signature(reference))
        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original)
             for item in actual.items],
            [
                ("Concreto fck 25 MPa", "m3", "12,50"),
                ("Lançamento de\nconcreto armado", "m", "2.500,75"),
                ("Serviço sem unidade", None, "2,00"),
            ],
        )
        self.assertTrue(any("alinhamento textual" in warning for warning in layout.warnings))

    def test_merged_header_and_empty_cell_preserve_associations(self) -> None:
        self._compare_to_expected_table_parser(
            "merged.pdf", _make_pdf(_draw_table(bordered=True, merged_header=True)),
            [
                ("Concreto fck 25 MPa", "m3", "12,50", 1),
                ("Lançamento de\nconcreto armado", "m", "2.500,75", 1),
                ("Serviço sem unidade", None, "2,00", 1),
            ],
        )

    def test_multiline_header_and_description_match_expected(self) -> None:
        content = _make_pdf(_draw_complex_table(
            title="PLANILHA DE SERVIÇOS",
            header_lines=[
                ["Item", "Descrição do", "Unidade de", "Quantidade"],
                ["", "serviço", "medida", ""],
            ],
            data_rows=[
                ["01", "Execução de", "m3", "1.234,50"],
                ["", "concreto armado", "", ""],
                ["02", "Serviço complementar", "un", "0,75"],
            ],
        ))
        self._compare_to_expected_table_parser(
            "multiline.pdf", content,
            [
                ("Execução de\nconcreto armado", "m3", "1.234,50", 1),
                ("Serviço complementar", "un", "0,75", 1),
            ],
        )

    def test_digital_geometric_groups_enable_fallback_but_missing_sidecar_abstains(self) -> None:
        path = self.root / "digital-no-table.pdf"
        path.write_bytes(_digital_pdf([["Descrição 2,00 un"]]))
        record = digital_page_records(path)[0]
        compatible = to_certificate_page(record)

        self.assertFalse(compatible.word_indices_available)
        self.assertTrue(compatible.derived_word_grouping_available)
        self.assertTrue(all(word.source_indices is None and word.indices_source is None
                            for word in record.words))
        self.assertTrue(all(word.derived_grouping is not None for word in record.words))
        report = extract_compatible_page(compatible)

        self.assertEqual([(item.service_description_original, item.unit_original,
                           item.quantity_original) for item in report.items],
                         [("Descrição", "un", "2,00")])
        self.assertTrue(any("agrupamento geométrico derivado" in warning
                            for warning in report.warnings))

        without_metadata = extract_layout_page(compatible.layout)
        self.assertEqual(without_metadata.items, ())
        self.assertTrue(any("não contém índices" in warning
                            for warning in without_metadata.warnings))

    def test_multiple_digital_lines_keep_description_unit_quantity_per_line(self) -> None:
        path = self.root / "digital-multiple-lines.pdf"
        path.write_bytes(_digital_pdf([[
            "Servico de concreto 12,50 m3",
            "Pintura externa 25,00 m2",
        ]]))
        record = digital_page_records(path)[0]
        compatible = to_certificate_page(record)
        report = extract_compatible_page(compatible)

        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original,
              item.page_number) for item in report.items],
            [("Servico de concreto", "m3", "12,50", 1),
             ("Pintura externa", "m2", "25,00", 1)],
        )
        line_groups = {word.derived_grouping.line_group for word in record.words}
        self.assertEqual(len(line_groups), 2)

    def test_column_sized_gap_is_ambiguous_and_fallback_abstains(self) -> None:
        path = self.root / "digital-columns.pdf"
        path.write_bytes(_digital_pdf([[
            "Servico de concreto                                Pintura externa 25,00 m2",
        ]]))
        record = digital_page_records(path)[0]
        compatible = to_certificate_page(record)
        report = extract_compatible_page(compatible)

        self.assertEqual(report.items, ())
        self.assertTrue(any("lacuna horizontal pode indicar colunas" in warning
                            for warning in compatible.layout.warnings))

    def test_real_tsv_line_indices_enable_synthetic_scanned_page_fallback(self) -> None:
        path = self.root / "scanned-lines.pdf"
        images = [_scan_page(("Servico de concreto 12,50 m3", "Pintura externa 25,00 m2"))]
        try:
            path.write_bytes(_synthetic_image_pdf(images))
        finally:
            for image in images:
                image.close()

        record = ocr_page_records(
            path,
            tesseract_exe=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            tessdata_dir=r"C:\Program Files\Tesseract-OCR\tessdata",
            language="por+eng", dpi=200, psm=6,
        )[0]
        self.assertTrue(record.words)
        self.assertTrue(all(word.indices_source == "tesseract_tsv"
                            and len(word.source_indices or ()) == 4
                            for word in record.words))
        compatible = to_certificate_page(record)
        self.assertTrue(compatible.word_indices_available)
        self.assertEqual(compatible.word_metadata, record.words)
        source_groups = {word.source_indices[:3] for word in compatible.word_metadata}
        self.assertEqual(len(source_groups), 2)
        report = extract_compatible_page(compatible)

        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original)
             for item in report.items],
            [("Servico de concreto", "m3", "12,50"),
             ("Pintura externa", "m2", "25,00")],
        )
        self.assertEqual([item.source_row_number for item in report.items], [1, 2])


def reference_table_extraction(layout, document_path=None):
    """Reference helper: re-extract each PageLayout grid with the prior bridge."""
    from certificate_extractor import ExtractionReport, extract_table

    reports = [extract_table(grid, page_number=layout.page_number,
                             source_method=layout.extraction_method,
                             document_path=document_path)
               for grid in layout.tables]
    return ExtractionReport(
        tuple(item for report in reports for item in report.items),
        tuple(row for report in reports for row in report.ignored_rows),
        tuple(row for report in reports for row in report.duplicate_rows),
        tuple(warning for report in reports for warning in report.warnings),
        tuple(row for report in reports for row in report.unassociated_rows),
    )


if __name__ == "__main__":
    unittest.main()
