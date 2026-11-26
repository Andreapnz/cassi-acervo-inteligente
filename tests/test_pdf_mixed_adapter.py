"""Synthetic-only tests for combining native and image-layer PDF text."""

from pathlib import Path
import tempfile
import unittest
import zlib
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from pdf_mixed_adapter import extract_mixed_pages
from pdf_ocr_adapter import recognize_image_data


def _literal(text: str) -> bytes:
    escaped = bytearray()
    for byte in text.encode("cp1252"):
        if byte in (ord("("), ord(")"), ord("\\")):
            escaped.extend(b"\\" + bytes((byte,)))
        elif byte < 32 or byte > 126:
            escaped.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            escaped.append(byte)
    return b"(" + bytes(escaped) + b")"


def _pdf(pages: list[dict]) -> bytes:
    count = len(pages)
    page_ids = [3 + 3 * index for index in range(count)]
    font_id = 3 + 3 * count
    objects: list[bytes] = [b""] * (font_id + 1)
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(count).encode() + b" >>"

    for index, entry in enumerate(pages):
        page_id = page_ids[index]
        image_id, content_id = page_id + 1, page_id + 2
        image = entry.get("image")
        native_text = entry.get("native", "")
        resources = b"/Font << /F1 " + str(font_id).encode() + b" 0 R >>"
        commands = []
        if image is not None:
            alias = f"Im{index + 1}"
            resources += b" /XObject << /" + alias.encode() + b" " + str(image_id).encode() + b" 0 R >>"
            width_pt, height_pt = entry.get("image_size_pt", (595, 842))
            x_pt, y_pt = entry.get("image_origin_pt", (0, 0))
            commands.append(
                f"q {width_pt} 0 0 {height_pt} {x_pt} {y_pt} cm /{alias} Do Q".encode("ascii")
            )
            pixels = zlib.compress(image.convert("RGB").tobytes())
            objects[image_id] = (
                b"<< /Type /XObject /Subtype /Image /Width " + str(image.width).encode()
                + b" /Height " + str(image.height).encode()
                + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
                + str(len(pixels)).encode() + b" >>\nstream\n" + pixels + b"\nendstream"
            )
        else:
            objects[image_id] = b"null"
        if native_text:
            commands.insert(0, b"BT /F1 26 Tf 70 780 Td " + _literal(native_text) + b" Tj ET")
        content = b"\n".join(commands)
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << "
            + resources + b" >> /Contents " + str(content_id).encode() + b" 0 R >>"
        )
        objects[content_id] = (
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        )
    objects[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"

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


def _scan(lines: tuple[str, ...], *, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, "white")
    if lines:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 70)
        draw = ImageDraw.Draw(image)
        y = 75
        for line in lines:
            draw.text((35, y), line, fill="black", font=font)
            y += 100
    return image


class PdfMixedAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-mixed-poc-")
        self.pdf_path = Path(self.temp_dir.name) / "synthetic-mixed.pdf"
        full_scan = _scan(("ESCANEADO 23,75 kg", "Ação técnica"), size=(1654, 2339))
        mixed_image = _scan(("DUPLICADO 123", "IMAGEM 8,25 m3"), size=(1264, 436))
        try:
            self.pdf_path.write_bytes(_pdf([
                {"native": "TEXTO NATIVO Ação 10,00"},
                {"image": full_scan},
                {"native": "DUPLICADO 123", "image": mixed_image,
                 "image_origin_pt": (70, 260), "image_size_pt": (455, 157)},
            ]))
        finally:
            full_scan.close()
            mixed_image.close()
        self.tesseract = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        self.tessdata = self.tesseract.parent / "tessdata"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_native_scanned_and_mixed_pages_keep_source_page_and_order(self) -> None:
        with patch("pdf_mixed_adapter.recognize_image_data", wraps=recognize_image_data) as ocr_call:
            pages = extract_mixed_pages(
                self.pdf_path,
                tesseract_exe=self.tesseract,
                tessdata_dir=self.tessdata,
                language="por+eng",
                dpi=200,
                psm=6,
            )

        self.assertEqual(len(pages), 3)
        self.assertEqual([page["page_number"] for page in pages], [1, 2, 3])
        self.assertEqual(ocr_call.call_count, 2, "OCR deve rodar apenas nas páginas com imagem")

        native_page, scanned_page, mixed_page = pages
        self.assertEqual(native_page["extraction_method"], "text")
        self.assertEqual(native_page["ocr_status"], "not_needed")
        self.assertIn("Ação", native_page["native_text"])
        self.assertEqual(native_page["ocr_text"], "")
        self.assertEqual(native_page["segments"][0]["source"], "native")

        self.assertEqual(scanned_page["extraction_method"], "ocr")
        self.assertEqual(scanned_page["ocr_status"], "recognized")
        self.assertIn("23,75", scanned_page["text"])
        self.assertIn("Ação", scanned_page["text"])
        self.assertEqual(scanned_page["segments"][0]["source"], "ocr")

        self.assertEqual(mixed_page["extraction_method"], "hybrid")
        self.assertEqual(mixed_page["ocr_status"], "recognized")
        self.assertIn("DUPLICADO 123", mixed_page["native_text"])
        self.assertIn("8,25", mixed_page["text"])
        self.assertEqual(mixed_page["text"].count("DUPLICADO 123"), 1)
        self.assertLess(mixed_page["text"].index("DUPLICADO 123"), mixed_page["text"].index("8,25"))
        self.assertEqual([part["source"] for part in mixed_page["segments"]], ["native", "ocr"])


if __name__ == "__main__":
    unittest.main()
