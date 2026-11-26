"""Synthetic-only tests for the isolated pdfplumber text adapter."""

from pathlib import Path
import tempfile
import unittest

from pdf_digital_adapter import extract_digital_pages


def _pdf_literal(text: str) -> bytes:
    """Encode text for a minimal synthetic PDF using WinAnsi/CP1252."""
    encoded = text.encode("cp1252")
    escaped = bytearray()
    for byte in encoded:
        if byte in (ord("("), ord(")"), ord("\\")):
            escaped.extend(b"\\" + bytes((byte,)))
        elif byte < 32 or byte > 126:
            escaped.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            escaped.append(byte)
    return b"(" + bytes(escaped) + b")"


def _make_synthetic_pdf(pages: list[list[str]]) -> bytes:
    """Build a small text-only/blank PDF without external PDF tooling."""
    font_id = 3 + 2 * len(pages)
    objects: list[bytes] = [b""] * (font_id + 1)
    page_ids = [3 + 2 * index for index in range(len(pages))]
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(pages)).encode() + b" >>"
    for index, lines in enumerate(pages):
        page_id = page_ids[index]
        content_id = page_id + 1
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font_id).encode() + b" 0 R >> >> "
            b"/Contents " + str(content_id).encode() + b" 0 R >>"
        )
        commands = [b"BT", b"/F1 12 Tf", b"72 720 Td"]
        for line_index, line in enumerate(lines):
            if line_index:
                commands.append(b"0 -18 Td")
            commands.append(_pdf_literal(line) + b" Tj")
        commands.append(b"ET")
        stream = b"\n".join(commands)
        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
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


class PdfDigitalAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-pdf-adapter-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_pdf(self, name: str, pages: list[list[str]]) -> Path:
        path = self.root / name
        path.write_bytes(_make_synthetic_pdf(pages))
        return path

    def test_extracts_multiple_pages_text_line_breaks_accents_and_numbers(self) -> None:
        pdf_path = self._write_pdf(
            "synthetic-digital.pdf",
            [["Ação técnica: 25,5 m³", "Preço: R$ 1.234,56"], ["Página dois: São João"]],
        )

        pages = extract_digital_pages(pdf_path)

        self.assertEqual(len(pages), 2)
        self.assertIn("Ação técnica: 25,5 m³", pages[0]["text"])
        self.assertIn("Preço: R$ 1.234,56", pages[0]["text"])
        self.assertIn("\n", pages[0]["text"])
        self.assertIn("Página dois: São João", pages[1]["text"])
        self.assertEqual(pages[0]["native_text"], pages[0]["text"])
        self.assertEqual(pages[0]["ocr_text"], "")
        self.assertEqual(pages[0]["ocr_status"], "not_run")

    def test_blank_page_has_no_native_text_and_does_not_start_ocr(self) -> None:
        pdf_path = self._write_pdf("synthetic-no-text-layer.pdf", [[]])

        pages = extract_digital_pages(pdf_path)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["text"], "")
        self.assertEqual(pages[0]["native_text"], "")
        self.assertEqual(pages[0]["ocr_text"], "")
        self.assertEqual(pages[0]["ocr_status"], "not_run")
        self.assertIsNone(pages[0]["page_error"])


if __name__ == "__main__":
    unittest.main()
