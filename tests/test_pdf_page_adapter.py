"""Synthetic-only tests for the unified experimental PDF page model."""

from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parent))

from test_pdf_digital_adapter import _make_synthetic_pdf as _digital_pdf
from test_pdf_mixed_adapter import _pdf as _mixed_pdf, _scan as _scan_image
from test_pdf_ocr_adapter import _scan_page, _synthetic_image_pdf
from test_pdf_table_adapter import _draw_table, _make_pdf as _table_pdf

from pdf_page_adapter import (
    CoordinateSpace,
    DataAvailability,
    CoordinateBox,
    PageWord,
    TextOrigin,
    digital_page_records,
    mixed_page_records,
    ocr_page_records,
    render_page_records,
    raster_box_to_pdf_points,
    table_page_records,
    _states,
)


class UnifiedPdfPageAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-unified-page-")
        self.root = Path(self.temp_dir.name)
        self.digital_pdf = self.root / "synthetic-digital.pdf"
        self.digital_pdf.write_bytes(_digital_pdf([
            ["Página um: Ação 25,50", "Unidade m3"],
            ["Página dois: São João 12,75"],
        ]))

        self.scanned_pdf = self.root / "synthetic-scanned.pdf"
        scan_pages = [_scan_page(("Ação técnica", "VALOR 125,50")), _scan_page(())]
        try:
            self.scanned_pdf.write_bytes(_synthetic_image_pdf(scan_pages))
        finally:
            for image in scan_pages:
                image.close()

        self.mixed_pdf = self.root / "synthetic-mixed.pdf"
        full_scan = _scan_image(("ESCANEADO 23,75 kg", "Ação técnica"), size=(1654, 2339))
        crop_scan = _scan_image(("DUPLICADO 123", "IMAGEM 8,25 m3"), size=(1264, 436))
        try:
            self.mixed_pdf.write_bytes(_mixed_pdf([
                {"native": "TEXTO NATIVO Ação 10,00"},
                {"image": full_scan},
                {"native": "DUPLICADO 123", "image": crop_scan,
                 "image_origin_pt": (70, 260), "image_size_pt": (455, 157)},
            ]))
        finally:
            full_scan.close()
            crop_scan.close()

        self.table_pdf = self.root / "synthetic-table.pdf"
        self.table_pdf.write_bytes(_table_pdf(_draw_table(bordered=True)))
        self.tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        self.tessdata = self.tesseract.parent / "tessdata"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_digital_records_include_page_dims_native_text_and_word_boxes(self) -> None:
        records = digital_page_records(self.digital_pdf)

        self.assertEqual([record.page_number for record in records], [1, 2])
        self.assertGreater(records[0].width_points, 0)
        self.assertGreater(records[0].height_points, 0)
        self.assertIn("Ação 25,50", records[0].native_text)
        self.assertIsNone(records[0].ocr_text)
        self.assertEqual(records[0].availability_for("ocr_text"), DataAvailability.NOT_COLLECTED)
        self.assertEqual(records[0].availability_for("word_coordinates"), DataAvailability.AVAILABLE)
        self.assertTrue(records[0].words)
        self.assertEqual(records[0].words[0].origin, TextOrigin.NATIVE)
        self.assertEqual(records[0].words[0].coordinates.space, CoordinateSpace.PDF_POINTS_TOP_LEFT)
        self.assertIsNone(records[0].tables)
        self.assertEqual(records[0].availability_for("tables"), DataAvailability.NOT_COLLECTED)

    def test_render_records_mark_uncollected_text_and_keep_raster_dimensions(self) -> None:
        records = render_page_records(self.digital_pdf, dpi=200)

        self.assertEqual(len(records), 2)
        self.assertGreater(records[0].render_width_px, 0)
        self.assertGreater(records[0].render_height_px, 0)
        self.assertIsNone(records[0].native_text)
        self.assertIsNone(records[0].ocr_text)
        self.assertIsNone(records[0].words)
        self.assertEqual(records[0].availability_for("native_text"), DataAvailability.NOT_COLLECTED)
        self.assertEqual(records[0].availability_for("render"), DataAvailability.AVAILABLE)

    def test_ocr_records_distinguish_scanned_text_from_blank_page(self) -> None:
        records = ocr_page_records(
            self.scanned_pdf, tesseract_exe=self.tesseract, tessdata_dir=self.tessdata,
            language="por+eng", dpi=200, psm=6,
        )

        self.assertEqual([record.page_number for record in records], [1, 2])
        self.assertIn("Ação", records[0].ocr_text)
        self.assertIn("125,50", records[0].ocr_text)
        self.assertEqual(records[0].ocr_status, "recognized")
        self.assertTrue(records[0].words)
        self.assertEqual(records[0].words[0].origin, TextOrigin.OCR)
        self.assertEqual(records[0].words[0].coordinates.space, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        self.assertEqual(records[0].words[0].pdf_coordinates.space, CoordinateSpace.PDF_POINTS_TOP_LEFT)
        self.assertEqual(records[0].words[0].pdf_coordinates.derived_from,
                         CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        self.assertEqual(records[0].availability_for("word_pdf_coordinates"),
                         DataAvailability.AVAILABLE)
        self.assertAlmostEqual(
            records[0].words[0].pdf_coordinates.x0,
            records[0].words[0].coordinates.x0 * records[0].width_points / records[0].render_width_px,
        )
        self.assertIsNone(records[0].native_text)
        self.assertEqual(records[1].ocr_status, "no_text")
        self.assertEqual(records[1].ocr_text, "")
        self.assertEqual(records[1].availability_for("native_text"), DataAvailability.NOT_COLLECTED)

    def test_mixed_records_preserve_sources_and_coordinate_spaces(self) -> None:
        records = mixed_page_records(
            self.mixed_pdf, tesseract_exe=self.tesseract, tessdata_dir=self.tessdata,
            language="por+eng", dpi=200, psm=6,
        )

        self.assertEqual([record.page_number for record in records], [1, 2, 3])
        self.assertEqual(records[0].extraction_method, "text")
        self.assertIsNone(records[0].ocr_text)
        self.assertEqual(records[0].text_segments[0].origin, TextOrigin.NATIVE)
        self.assertEqual(records[1].extraction_method, "ocr")
        self.assertIn("23,75", records[1].combined_text)
        self.assertTrue(any(word.origin is TextOrigin.OCR
                            and word.coordinates.space is CoordinateSpace.RASTER_PIXELS_TOP_LEFT
                            for word in records[1].words))
        self.assertEqual(records[2].extraction_method, "hybrid")
        self.assertEqual(records[2].text_segments[0].origin, TextOrigin.NATIVE)
        self.assertEqual(records[2].text_segments[1].origin, TextOrigin.OCR)
        self.assertTrue(any(word.origin is TextOrigin.NATIVE
                            and word.coordinates.space is CoordinateSpace.PDF_POINTS_TOP_LEFT
                            for word in records[2].words))
        self.assertTrue(any(word.origin is TextOrigin.OCR
                            and word.coordinates.space is CoordinateSpace.RASTER_PIXELS_TOP_LEFT
                            for word in records[2].words))
        ocr_word = next(word for word in records[2].words if word.origin is TextOrigin.OCR)
        self.assertIsNotNone(ocr_word.pdf_coordinates)
        self.assertEqual(ocr_word.coordinates.space, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        self.assertEqual(ocr_word.pdf_coordinates.space, CoordinateSpace.PDF_POINTS_TOP_LEFT)

    def test_raster_to_pdf_conversion_uses_independent_measured_scales(self) -> None:
        raster = CoordinateBox(100, 50, 300, 150, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        converted = raster_box_to_pdf_points(
            raster, page_width_points=600, page_height_points=300,
            image_width_px=1200, image_height_px=900,
        )

        expected = (50, 300 / 18, 150, 50)
        actual = (converted.x0, converted.y0, converted.x1, converted.y1)
        for actual_value, expected_value in zip(actual, expected, strict=True):
            self.assertAlmostEqual(actual_value, expected_value)
        self.assertEqual(raster.space, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        self.assertEqual(converted.space, CoordinateSpace.PDF_POINTS_TOP_LEFT)
        self.assertEqual(converted.derived_from, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)

    def test_invalid_dimensions_or_boxes_do_not_produce_pdf_coordinates(self) -> None:
        raster = CoordinateBox(10, 10, 20, 20, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        invalid_cases = (
            dict(page_width_points=0, page_height_points=842, image_width_px=1000, image_height_px=1200),
            dict(page_width_points=595, page_height_points=float("nan"), image_width_px=1000, image_height_px=1200),
            dict(page_width_points=595, page_height_points=842, image_width_px=None, image_height_px=1200),
            dict(page_width_points=595, page_height_points=842, image_width_px=1000, image_height_px=-1),
        )
        for dimensions in invalid_cases:
            with self.subTest(dimensions=dimensions):
                self.assertIsNone(raster_box_to_pdf_points(raster, **dimensions))
        outside = CoordinateBox(10, 10, 101, 20, CoordinateSpace.RASTER_PIXELS_TOP_LEFT)
        self.assertIsNone(raster_box_to_pdf_points(
            outside, page_width_points=595, page_height_points=842,
            image_width_px=100, image_height_px=100,
        ))
        self.assertIsNone(raster_box_to_pdf_points(
            CoordinateBox(1, 1, 2, 2, CoordinateSpace.PDF_POINTS_TOP_LEFT),
            page_width_points=595, page_height_points=842,
            image_width_px=100, image_height_px=100,
        ))
        no_pdf_box = PageWord("TOKEN", TextOrigin.OCR, raster, pdf_coordinates=None)
        state = dict(_states(
            width=595, height=842, native_text=None, ocr_text="TOKEN",
            words=(no_pdf_box,), tables=None, text_segments=(), rendered=True,
        ))
        self.assertEqual(state["word_pdf_coordinates"], DataAvailability.UNAVAILABLE)

    def test_table_records_carry_grids_and_mark_text_words_uncollected(self) -> None:
        records = table_page_records(self.table_pdf)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertTrue(record.tables)
        self.assertEqual(record.column_mappings, ({"service": 0, "unit": 1, "quantity": 2},))
        self.assertEqual(record.tables[0].rows[1].cells[2].text, "12,50")
        self.assertIsNone(record.native_text)
        self.assertIsNone(record.words)
        self.assertEqual(record.availability_for("tables"), DataAvailability.AVAILABLE)
        self.assertEqual(record.availability_for("words"), DataAvailability.NOT_COLLECTED)
        self.assertEqual(record.table_coordinate_space, CoordinateSpace.PDF_POINTS_TOP_LEFT)
        self.assertEqual(record.availability_for("text_segments"), DataAvailability.NOT_COLLECTED)


if __name__ == "__main__":
    unittest.main()
