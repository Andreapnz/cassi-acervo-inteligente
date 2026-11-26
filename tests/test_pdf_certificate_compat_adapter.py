"""Synthetic contract tests for the experimental certificate bridge."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))

from test_pdf_digital_adapter import _make_synthetic_pdf as _digital_pdf
from test_pdf_table_adapter import _draw_table, _make_pdf, _draw_complex_table
from pdf_page_adapter import digital_page_records, table_page_records
from pdf_certificate_compat_adapter import extract_certificate_page, to_certificate_page


class CertificateCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-cert-compat-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _record(self, name: str, content: bytes):
        path = self.root / name
        path.write_bytes(content)
        return table_page_records(path)[0]

    def _extract(self, name: str, content: bytes):
        record = self._record(name, content)
        compatible = to_certificate_page(record)
        return record, compatible, extract_certificate_page(
            compatible, document_path="synthetic://" + name,
        )

    def test_bordered_table_contract_and_coordinates(self) -> None:
        record, compatible, report = self._extract(
            "bordered.pdf", _make_pdf(_draw_table(bordered=True)),
        )

        self.assertEqual(compatible.layout.page_number, 1)
        self.assertEqual(compatible.layout.extraction_method, "native")
        self.assertEqual((compatible.width_points, compatible.height_points),
                         (record.width_points, record.height_points))
        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original,
              item.page_number) for item in report.items],
            [
                ("Concreto fck 25 MPa", "m3", "12,50", 1),
                ("Lançamento de\nconcreto armado", "m", "2.500,75", 1),
                ("Serviço sem unidade", None, "2,00", 1),
            ],
        )
        self.assertTrue(all(item.evidence_coordinates for item in report.items))
        self.assertTrue(all(item.evidence_coordinates["x1"] > item.evidence_coordinates["x0"]
                            for item in report.items))

    def test_word_spans_and_page_dimensions_map_without_inference(self) -> None:
        path = self.root / "digital-page.pdf"
        path.write_bytes(_digital_pdf([["Concreto 25,50 MPa"]]))
        record = digital_page_records(path)[0]
        compatible = to_certificate_page(record)

        self.assertEqual(compatible.width_points, record.width_points)
        self.assertEqual(compatible.height_points, record.height_points)
        self.assertTrue(compatible.layout.native_spans)
        span = next(item for item in compatible.layout.native_spans if item.text == "Concreto")
        self.assertEqual(span.source, "native")
        self.assertEqual(len(span.coordinates), 4)
        self.assertGreater(span.coordinates[2], span.coordinates[0])
        self.assertEqual(compatible.layout.ocr_spans, ())

    def test_unbordered_table_fields_and_detector_warning(self) -> None:
        _, compatible, report = self._extract(
            "unbordered.pdf", _make_pdf(_draw_table(bordered=False)),
        )

        self.assertTrue(any("alinhamento textual" in warning
                            for warning in compatible.layout.warnings))
        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original)
             for item in report.items],
            [
                ("Concreto fck 25 MPa", "m3", "12,50"),
                ("Lançamento de\nconcreto armado", "m", "2.500,75"),
                ("Serviço sem unidade", None, "2,00"),
            ],
        )

    def test_merged_title_and_empty_unit_cells_are_not_fabricated(self) -> None:
        _, compatible, report = self._extract(
            "merged-header.pdf", _make_pdf(_draw_table(bordered=True, merged_header=True)),
        )

        self.assertEqual(compatible.layout.tables[0].rows[0].cells[0].text, "Descrição")
        empty_unit_cell = next(item for item in report.items
                               if item.service_description_original == "Serviço sem unidade")
        self.assertIsNone(empty_unit_cell.unit_original)
        self.assertEqual(empty_unit_cell.quantity_original, "2,00")
        self.assertTrue(all("ITENS DA PLANILHA" not in cell.text
                            for row in compatible.layout.tables[0].rows for cell in row.cells))

    def test_multiline_header_and_multiline_description_keep_values(self) -> None:
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
        _, compatible, report = self._extract("multiline.pdf", content)

        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original)
             for item in report.items],
            [
                ("Execução de\nconcreto armado", "m3", "1.234,50"),
                ("Serviço complementar", "un", "0,75"),
            ],
        )
        self.assertEqual([item.page_number for item in report.items], [1, 1])
        self.assertTrue(all(item.evidence_coordinates for item in report.items))


if __name__ == "__main__":
    unittest.main()
