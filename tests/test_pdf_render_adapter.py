"""Synthetic-only tests for the isolated pypdfium2 rendering adapter."""

from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageChops

from pdf_render_adapter import render_pdf_pages


def _pdf_literal(text: str) -> bytes:
    escaped = bytearray()
    for byte in text.encode("cp1252"):
        if byte in (ord("("), ord(")"), ord("\\")):
            escaped.extend(b"\\" + bytes((byte,)))
        elif byte < 32 or byte > 126:
            escaped.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            escaped.append(byte)
    return b"(" + bytes(escaped) + b")"


def _synthetic_pdf(page_contents: list[bytes]) -> bytes:
    """Build a minimal PDF; content may be text or vector-only artwork."""
    font_id = 3 + 2 * len(page_contents)
    objects: list[bytes] = [b""] * (font_id + 1)
    page_ids = [3 + 2 * index for index in range(len(page_contents))]
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    for index, content in enumerate(page_contents):
        page_id = page_ids[index]
        content_id = page_id + 1
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 " + str(font_id).encode() + b" 0 R >> >> "
            b"/Contents " + str(content_id).encode() + b" 0 R >>"
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


def _text_page(*lines: str) -> bytes:
    commands = [b"BT", b"/F1 18 Tf", b"72 760 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append(b"0 -28 Td")
        commands.append(_pdf_literal(line) + b" Tj")
    commands.append(b"ET")
    return b"\n".join(commands)


class PdfRenderAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-pdfium-render-")
        self.pdf_path = Path(self.temp_dir.name) / "synthetic-multipage.pdf"
        self.pdf_path.write_bytes(_synthetic_pdf([
            _text_page("Página 1 — teste de renderização", "Ação, 25,5 m³"),
            b"0.1 0.3 0.8 rg 72 600 260 90 re f 0 0 0 rg 72 560 260 8 re f",
            _text_page("Página 3 — fim do documento"),
        ]))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _has_visible_marks(image) -> bool:
        grayscale = image.convert("L")
        difference = ImageChops.difference(grayscale, Image.new("L", image.size, 255))
        return difference.getbbox() is not None

    def test_renders_all_pages_at_200_dpi_with_expected_dimensions_and_visible_content(self) -> None:
        pages = render_pdf_pages(self.pdf_path, dpi=200)

        self.assertEqual(len(pages), 3)
        self.assertEqual([page.page_number for page in pages], [1, 2, 3])
        for page in pages:
            self.assertLessEqual(abs(page.width_px - round(595 * 200 / 72)), 1)
            self.assertLessEqual(abs(page.height_px - round(842 * 200 / 72)), 1)
            self.assertEqual(page.image.size, (page.width_px, page.height_px))
            self.assertTrue(self._has_visible_marks(page.image))

    def test_rejects_non_positive_dpi(self) -> None:
        with self.assertRaises(ValueError):
            render_pdf_pages(self.pdf_path, dpi=0)


if __name__ == "__main__":
    unittest.main()
