"""Synthetic-only tests for pdfplumber table conversion to CASSI grids."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from certificate_extractor import extract_table
from pdf_table_adapter import extract_page_tables


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


def _make_pdf(content: bytes) -> bytes:
    objects = [
        b"",
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * len(objects)
    for object_id in range(1, len(objects)):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id] + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _draw_table(*, bordered: bool, merged_header: bool = False,
                ambiguous_row: bool = False) -> bytes:
    commands: list[bytes] = []
    if bordered:
        commands.extend((b"0.7 w", b"70 520 m 520 520 l S", b"70 570 m 520 570 l S",
                         b"70 630 m 520 630 l S", b"70 680 m 520 680 l S",
                         b"70 720 m 520 720 l S", b"70 520 m 70 720 l S",
                         b"340 520 m 340 720 l S", b"430 520 m 430 720 l S",
                         b"520 520 m 520 720 l S"))
    if merged_header:
        commands.extend((b"BT /F1 14 Tf 220 700 Td " + _literal("ITENS DA PLANILHA") + b" Tj ET",))
    else:
        commands.extend((b"BT /F1 11 Tf 80 697 Td " + _literal("Descrição") + b" Tj ET",
                         b"BT /F1 11 Tf 350 697 Td " + _literal("Unidade") + b" Tj ET",
                         b"BT /F1 11 Tf 440 697 Td " + _literal("Quantidade") + b" Tj ET"))
    if merged_header:
        commands.extend((b"BT /F1 11 Tf 80 650 Td " + _literal("Descrição") + b" Tj ET",
                         b"BT /F1 11 Tf 350 650 Td " + _literal("Unidade") + b" Tj ET",
                         b"BT /F1 11 Tf 440 650 Td " + _literal("Quantidade") + b" Tj ET"))
    rows = [
        ("Concreto fck 25 MPa", "m3", "12,50", 660),
        ("Lançamento de", "m", "2.500,75", 615),
        ("concreto armado", "", "", 600),
        ("Serviço sem unidade", "", "2,00", 550),
    ]
    for description, unit, quantity, y in rows:
        commands.extend((b"BT /F1 10 Tf 80 " + str(y).encode() + b" Td " + _literal(description) + b" Tj ET",
                         b"BT /F1 10 Tf 350 " + str(y).encode() + b" Td " + _literal(unit) + b" Tj ET",
                         b"BT /F1 10 Tf 440 " + str(y).encode() + b" Td " + _literal(quantity) + b" Tj ET"))
    if ambiguous_row:
        commands.extend((b"BT /F1 10 Tf 80 490 Td " + _literal("Descrição independente") + b" Tj ET",
                         b"BT /F1 10 Tf 350 490 Td " + _literal("") + b" Tj ET",
                         b"BT /F1 10 Tf 440 490 Td " + _literal("") + b" Tj ET"))
    return b"\n".join(commands)


def _draw_complex_table(*, title: str, header_lines: list[list[str]],
                        data_rows: list[list[str]],
                        separate_header_rows: bool = False) -> bytes:
    column_count = len(header_lines[0])
    x_positions = [55, 120, 350, 435, 530] if column_count == 4 else [70, 340, 430, 520]
    commands: list[bytes] = [b"0.7 w"]
    if title:
        commands.extend((b"55 730 m 530 730 l S", b"55 700 m 530 700 l S"))
        commands.append(b"BT /F1 14 Tf 170 711 Td " + _literal(title) + b" Tj ET")
        vertical_top = 700
    else:
        vertical_top = 730
    bottom = 650 - 50 * len(data_rows)
    horizontal_lines = [vertical_top, 650, *[600 - 50 * index for index in range(len(data_rows))]]
    if separate_header_rows:
        horizontal_lines.append(vertical_top - 30)
    for y in sorted(set(horizontal_lines), reverse=True):
        commands.append(f"55 {y} m 530 {y} l S".encode("ascii"))
    for x in x_positions:
        commands.append(f"{x} {vertical_top} m {x} {bottom} l S".encode("ascii"))

    if separate_header_rows:
        header_ys = [vertical_top - 15 - 30 * index for index in range(len(header_lines))]
    else:
        header_ys = [690 - 17 * index for index in range(len(header_lines))]
    for line_index, values in enumerate(header_lines):
        for column_index, value in enumerate(values):
            if value:
                commands.append(
                    b"BT /F1 10 Tf " + str(x_positions[column_index] + 8).encode()
                    + b" " + str(header_ys[line_index]).encode() + b" Td "
                    + _literal(value) + b" Tj ET"
                )
    for row_index, values in enumerate(data_rows):
        y = 630 - 50 * row_index
        for column_index, value in enumerate(values):
            if value:
                commands.append(
                    b"BT /F1 10 Tf " + str(x_positions[column_index] + 8).encode()
                    + b" " + str(y).encode() + b" Td " + _literal(value) + b" Tj ET"
                )
    return b"\n".join(commands)


class PdfTableAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cassi-table-adapter-")
        self.root = Path(self.temp_dir.name)
        self.bordered_pdf = self.root / "synthetic-bordered.pdf"
        self.bordered_pdf.write_bytes(_make_pdf(_draw_table(bordered=True)))
        self.unbordered_pdf = self.root / "synthetic-unbordered.pdf"
        self.unbordered_pdf.write_bytes(_make_pdf(_draw_table(bordered=False)))
        self.merged_pdf = self.root / "synthetic-merged-header.pdf"
        self.merged_pdf.write_bytes(_make_pdf(_draw_table(bordered=True, merged_header=True)))
        self.ambiguous_pdf = self.root / "synthetic-ambiguous-continuation.pdf"
        self.ambiguous_pdf.write_bytes(_make_pdf(_draw_table(bordered=False, ambiguous_row=True)))
        self.two_line_header_pdf = self.root / "synthetic-merged-title-two-line-header-extra-column.pdf"
        self.two_line_header_pdf.write_bytes(_make_pdf(_draw_complex_table(
            title="PLANILHA DE SERVIÇOS",
            header_lines=[
                ["Item", "Descrição do", "Unidade de", "Quantidade"],
                ["", "serviço", "medida", ""],
            ],
            data_rows=[
                ["01", "Concreto fck 30 MPa", "m3", "1.234,50"],
                ["02", "Serviço sem unidade", "", "0,75"],
            ],
        )))
        self.ambiguous_header_pdf = self.root / "synthetic-ambiguous-header.pdf"
        self.ambiguous_header_pdf.write_bytes(_make_pdf(_draw_complex_table(
            title="",
            header_lines=[["Descrição", "Unidade", "Quantidade", "Qtd."]],
            data_rows=[["Concreto", "m3", "1,00", "1,00"]],
        )))
        self.physical_two_row_header_pdf = self.root / "synthetic-physical-two-row-header.pdf"
        self.physical_two_row_header_pdf.write_bytes(_make_pdf(_draw_complex_table(
            title="RESUMO DE MEDIÇÕES",
            header_lines=[
                ["Item", "Descrição", "Unidade", ""],
                ["", "", "de medida", "Quantidade"],
            ],
            data_rows=[
                ["01", "Concreto fck 40 MPa", "m3", "0,75"],
            ],
            separate_header_rows=True,
        )))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bordered_table_cells_coordinates_and_certificate_fields(self) -> None:
        result = extract_page_tables(self.bordered_pdf)

        self.assertEqual(result.page_number, 1)
        self.assertEqual(result.detector, "lines")
        self.assertEqual(len(result.tables), 1)
        grid = result.tables[0]
        self.assertEqual(len(grid.rows), 4)
        self.assertEqual(len(grid.rows[0].cells), 3)
        self.assertEqual(grid.rows[0].cells[0].text, "Descrição")
        self.assertIsNotNone(grid.coordinates)
        self.assertIsNotNone(grid.rows[1].cells[0].coordinates)
        self.assertGreater(grid.rows[1].cells[0].coordinates[2], grid.rows[1].cells[0].coordinates[0])
        self.assertEqual(grid.rows[3].cells[1].text, "")

        report = extract_table(grid, page_number=1, source_method="native")
        self.assertEqual(len(report.items), 3)
        concrete, launch, no_unit = report.items
        self.assertEqual(concrete.service_description_original, "Concreto fck 25 MPa")
        self.assertEqual(concrete.unit_original, "m3")
        self.assertEqual(concrete.quantity_original, "12,50")
        self.assertEqual(concrete.quantity_normalized, "12.50")
        self.assertEqual(launch.service_description_original, "Lançamento de\nconcreto armado")
        self.assertEqual(launch.quantity_original, "2.500,75")
        self.assertEqual(launch.quantity_normalized, "2500.75")
        self.assertIsNone(no_unit.unit_original)
        self.assertEqual(no_unit.quantity_original, "2,00")

    def test_unbordered_table_uses_text_alignment_or_reports_limitation(self) -> None:
        result = extract_page_tables(self.unbordered_pdf)

        self.assertEqual(result.detector, "text")
        self.assertEqual(len(result.tables), 1)
        self.assertTrue(any("alinhamento textual" in warning for warning in result.warnings))
        grid = result.tables[0]
        expected_rows = [
            ["Descrição", "Unidade", "Quantidade"],
            ["Concreto fck 25 MPa", "m3", "12,50"],
            ["Lançamento de\nconcreto armado", "m", "2.500,75"],
            ["Serviço sem unidade", "", "2,00"],
        ]
        self.assertEqual([[cell.text for cell in row.cells] for row in grid.rows], expected_rows)
        report = extract_table(grid, page_number=1, source_method="native")
        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original,
              item.quantity_normalized) for item in report.items],
            [
                ("Concreto fck 25 MPa", "m3", "12,50", "12.50"),
                ("Lançamento de\nconcreto armado", "m", "2.500,75", "2500.75"),
                ("Serviço sem unidade", None, "2,00", "2.00"),
            ],
        )

    def test_ambiguous_description_is_kept_separate_and_warned(self) -> None:
        result = extract_page_tables(self.ambiguous_pdf)

        self.assertEqual(result.detector, "text")
        self.assertTrue(result.tables)
        self.assertTrue(any("descrição" in warning and "ambígua" in warning
                            for warning in result.warnings))
        rows = [[cell.text for cell in row.cells] for row in result.tables[0].rows]
        self.assertIn(["Descrição independente", "", ""], rows)
        report = extract_table(result.tables[0], page_number=1, source_method="native")
        independent = next(item for item in report.items
                           if item.service_description_original == "Descrição independente")
        self.assertIsNone(independent.unit_original)
        self.assertIsNone(independent.quantity_original)

    def test_merged_general_title_is_separated_from_column_headers(self) -> None:
        result = extract_page_tables(self.merged_pdf)

        self.assertEqual(len(result.tables), 1)
        self.assertEqual(result.column_mappings, ({"service": 0, "unit": 1, "quantity": 2},))
        rows = [[cell.text for cell in row.cells] for row in result.tables[0].rows]
        self.assertEqual(rows[0], ["Descrição", "Unidade", "Quantidade"])
        self.assertEqual(rows[1], ["Concreto fck 25 MPa", "m3", "12,50"])
        self.assertFalse(any("ITENS DA PLANILHA" in cell for row in rows for cell in row))

    def test_two_line_headers_extra_column_and_all_data_rows(self) -> None:
        result = extract_page_tables(self.two_line_header_pdf)

        self.assertEqual(result.column_mappings, ({"service": 1, "unit": 2, "quantity": 3},))
        rows = [[cell.text for cell in row.cells] for row in result.tables[0].rows]
        self.assertEqual(rows, [
            ["Item", "Descrição", "Unidade", "Quantidade"],
            ["01", "Concreto fck 30 MPa", "m3", "1.234,50"],
            ["02", "Serviço sem unidade", "", "0,75"],
        ])
        report = extract_table(result.tables[0], page_number=1, source_method="native")
        self.assertEqual(
            [(item.service_description_original, item.unit_original, item.quantity_original,
              item.quantity_normalized) for item in report.items],
            [
                ("Concreto fck 30 MPa", "m3", "1.234,50", "1234.50"),
                ("Serviço sem unidade", None, "0,75", "0.75"),
            ],
        )

    def test_headers_split_across_physical_rows_are_combined(self) -> None:
        result = extract_page_tables(self.physical_two_row_header_pdf)

        self.assertEqual(result.column_mappings, ({"service": 1, "unit": 2, "quantity": 3},))
        rows = [[cell.text for cell in row.cells] for row in result.tables[0].rows]
        self.assertEqual(rows, [
            ["Item", "Descrição", "Unidade", "Quantidade"],
            ["01", "Concreto fck 40 MPa", "m3", "0,75"],
        ])
        report = extract_table(result.tables[0], page_number=1, source_method="native")
        self.assertEqual(len(report.items), 1)
        self.assertEqual(report.items[0].quantity_original, "0,75")
        self.assertEqual(report.items[0].quantity_normalized, "0.75")

    def test_conflicting_header_mapping_warns_and_is_not_exposed(self) -> None:
        result = extract_page_tables(self.ambiguous_header_pdf)

        self.assertEqual(len(result.tables), 1)
        self.assertEqual(result.column_mappings, (None,))
        self.assertTrue(any("rótulos conflitantes" in warning for warning in result.warnings))
        rows = [[cell.text for cell in row.cells] for row in result.tables[0].rows]
        self.assertEqual(rows[0], ["Descrição", "Unidade", "Quantidade", "Qtd."])


if __name__ == "__main__":
    unittest.main()
