"""Comparative characterization of PDF backends on synthetic digital PDFs.

These tests do not declare the experimental backend equivalent to PyMuPDF.
They record exact matches and known divergences without normalizing results.
"""

from pathlib import Path
import tempfile
import unittest

import pymupdf

from certificate_extractor import extract_page as extract_certificate_page
from pdf_certificate_compat_adapter import to_certificate_page
from pdf_digital_adapter import extract_digital_pages
from pdf_page_adapter import digital_page_records, table_page_records
from pdf_page_extractor_compat import extract_compatible_page
from pdf_processor import extract_pages
from test_pdf_digital_adapter import _make_synthetic_pdf
from test_pdf_table_adapter import _draw_table, _make_pdf


def _item_signature(report):
    return [
        (item.service_description_original, item.unit_original,
         item.quantity_original, item.page_number, item.review_status)
        for item in report.items
    ]


class PdfBackendComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-backend-compare-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_digital_text_order_pages_words_and_coordinate_spaces(self) -> None:
        path = self.root / "digital-text.pdf"
        path.write_bytes(_make_synthetic_pdf([
            ["A\u00e7\u00e3o t\u00e9cnica: 25,5 m\u00b3", "Pre\u00e7o: R$ 1.234,56"],
            ["P\u00e1gina dois: S\u00e3o Jo\u00e3o"],
        ]))

        reference = extract_pages(path, ocr=False)
        alternative = extract_digital_pages(path)
        self.assertEqual(len(reference), 2)
        self.assertEqual(len(alternative), 2)
        for expected, actual in zip(reference, alternative, strict=True):
            self.assertEqual(expected["text"], actual["text"])
        self.assertIn("\u00e7", reference[0]["text"])
        self.assertIn("\u00b3", reference[0]["text"])

        record = digital_page_records(path)[0]
        with pymupdf.open(path) as document:
            page = document[0]
            pymupdf_words = page.get_text("words", sort=True)
            pymupdf_dimensions = (page.rect.width, page.rect.height)

        self.assertEqual(
            pymupdf_dimensions,
            (record.width_points, record.height_points),
        )
        page_words = record.words or ()
        self.assertEqual([word[4] for word in pymupdf_words],
                         [word.text for word in page_words])
        deltas = []
        for reference_word, alternative_word in zip(pymupdf_words, page_words, strict=True):
            box = alternative_word.coordinates
            self.assertIsNotNone(box)
            self.assertEqual(box.space.value, "pdf_points_top_left")
            deltas.extend(abs(float(reference_word[index]) - value)
                          for index, value in enumerate((box.x0, box.y0, box.x1, box.y1)))
        # This is a characterization of the fixture, not a production tolerance.
        self.assertGreater(max(deltas, default=0.0), 0.0)

    def test_table_adapter_preserves_values_but_is_not_equivalent_on_fixture(self) -> None:
        path = self.root / "bordered-table.pdf"
        path.write_bytes(_make_pdf(_draw_table(bordered=True)))

        with pymupdf.open(path) as document:
            page = document[0]
            reference_page_text = page.get_text("text")
            finder = page.find_tables()
            reference_cells = [table.extract() for table in finder.tables]
            reference_boxes = [tuple(table.bbox) for table in finder.tables]
            reference_report = extract_certificate_page(page, page_number=1)

        record = table_page_records(path)[0]
        alternative_report = extract_compatible_page(to_certificate_page(record))
        alternative_cells = [
            [[cell.text for cell in row.cells] for row in grid.rows]
            for grid in (record.tables or ())
        ]
        alternative_boxes = [grid.coordinates for grid in (record.tables or ())]

        self.assertEqual(alternative_cells[0][1:], [
            ["Concreto fck 25 MPa", "m3", "12,50"],
            ["Lan\u00e7amento de\nconcreto armado", "m", "2.500,75"],
            ["Servi\u00e7o sem unidade", "", "2,00"],
        ])
        self.assertEqual(reference_boxes, alternative_boxes)
        # The PDF text layer itself keeps the punctuation; fragmentation occurs
        # in PyMuPDF's table-cell extraction for this fixture.
        self.assertIn("12,50", reference_page_text)
        self.assertIn("2.500,75", reference_page_text)
        self.assertNotIn("12,50", reference_cells[0][1][2])
        self.assertNotEqual(reference_cells, alternative_cells)
        self.assertNotEqual(_item_signature(reference_report),
                            _item_signature(alternative_report))

    def test_ambiguous_unheaded_line_is_not_silently_resolved(self) -> None:
        path = self.root / "unheaded-certificate-line.pdf"
        path.write_bytes(_make_synthetic_pdf([
            ["Concreto fck 25 MPa | m3 | 12,50"],
        ]))

        with pymupdf.open(path) as document:
            reference = extract_certificate_page(document[0], page_number=1)
        record = digital_page_records(path)[0]
        alternative = extract_compatible_page(to_certificate_page(record))

        self.assertEqual(_item_signature(reference), _item_signature(alternative))
        self.assertEqual(len(alternative.items), 1)
        item = alternative.items[0]
        self.assertEqual(item.review_status, "pending")
        self.assertIsNone(item.quantity_normalized)
        self.assertTrue(any("mais de um n\u00famero" in reason
                            for reason in item.review_reasons))


if __name__ == "__main__":
    unittest.main()
