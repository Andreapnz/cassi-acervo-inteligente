"""Synthetic-only OCR tests for the isolated PDFium/Tesseract adapter."""

from pathlib import Path
import tempfile
import unittest
import zlib

from PIL import Image, ImageDraw, ImageFont

from pdf_ocr_adapter import extract_ocr_pages


def _synthetic_image_pdf(images: list[Image.Image]) -> bytes:
    """Wrap RGB raster images in PDF pages without adding a text layer."""
    count = len(images)
    page_ids = [3 + 3 * index for index in range(count)]
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
    objects: list[bytes] = [b""] * (3 + 3 * count)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(count).encode() + b" >>"

    for index, image in enumerate(images):
        page_id = page_ids[index]
        image_id = page_id + 1
        content_id = page_id + 2
        width, height = image.size
        pixels = zlib.compress(image.convert("RGB").tobytes())
        content = f"q 595 0 0 842 0 0 cm /Im{index + 1} Do Q".encode("ascii")
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /XObject << /Im" + str(index + 1).encode() + b" "
            + str(image_id).encode() + b" 0 R >> >> /Contents "
            + str(content_id).encode() + b" 0 R >>"
        )
        objects[image_id] = (
            b"<< /Type /XObject /Subtype /Image /Width " + str(width).encode()
            + b" /Height " + str(height).encode()
            + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
            + str(len(pixels)).encode() + b" >>\nstream\n" + pixels + b"\nendstream"
        )
        objects[content_id] = (
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * len(objects)
    for object_id in range(1, len(objects)):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _scan_page(text_lines: tuple[str, ...]) -> Image.Image:
    image = Image.new("RGB", (1654, 2339), "white")
    if text_lines:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 74)
        draw = ImageDraw.Draw(image)
        y = 650
        for line in text_lines:
            draw.text((130, y), line, fill="black", font=font)
            y += 125
    return image


class PdfOcrAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-tesseract-poc-")
        self.pdf_path = Path(self.temp_dir.name) / "synthetic-scanned.pdf"
        pages = [
            _scan_page(("Ação técnica", "VALOR 125,50")),
            _scan_page(()),
        ]
        try:
            self.pdf_path.write_bytes(_synthetic_image_pdf(pages))
        finally:
            for image in pages:
                image.close()
        self.tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        self.tessdata = self.tesseract.parent / "tessdata"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ocr_reads_synthetic_scan_and_marks_blank_page(self) -> None:
        results = extract_ocr_pages(
            self.pdf_path,
            tesseract_exe=self.tesseract,
            tessdata_dir=self.tessdata,
            language="por+eng",
            dpi=200,
            psm=6,
        )

        self.assertEqual([item.page_number for item in results], [1, 2])
        self.assertEqual(results[0].status, "recognized", repr(results[0]))
        self.assertIn("Ação", results[0].text)
        self.assertIn("125,50", results[0].text)
        self.assertEqual(results[1].status, "no_text")
        self.assertEqual(results[1].text, "")


if __name__ == "__main__":
    unittest.main()
