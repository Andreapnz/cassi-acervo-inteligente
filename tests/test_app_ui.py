import json
import os
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class CassiInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cassi-app-ui-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.database = root / "temporary-cassi.db"
        self.certificate_database = root / "temporary-certificate-analysis.db"
        self.pdf_root = root / "fictional-archive"
        self.backup_root = root / "fictional-backups"
        self.pdf_root.mkdir()
        self.backup_root.mkdir()
        for name in ("fixture.pdf", "Relatorio-Compactacao.PDF", "aterro.pdf"):
            (self.pdf_root / name).write_bytes(b"%PDF synthetic-only UI link fixture")
        # app.py imports these values from the already-loaded config module.
        # Patch before AppTest evaluates the script so every read/write remains temporary.
        for name, value in (
            ("DATABASE_PATH", self.database),
            ("CERTIFICATE_ANALYSIS_DATABASE_PATH", self.certificate_database),
            ("DEFAULT_PDF_FOLDER", self.pdf_root),
            ("DATABASE_BACKUP_DIR", self.backup_root),
            ("APP_DATA_DIR", root / "application-data"),
            ("APP_SETTINGS_PATH", root / "settings.json"),
        ):
            patcher = patch(f"config.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_uncertain_related_match_has_distinct_label_and_count(self):
        result = {
            "query": "Terraplenagem", "pdf": "aterro.pdf", "page": 1,
            "snippet": "Manutencao do aterro sanitario.",
            "matched_text": "aterro", "match_type": "related_uncertain",
            "relation": "menção relacionada; contexto técnico não confirmado",
            "match_reason": "Menção de aterro em contexto aterro sanitário; relação com terraplenagem não confirmada.",
            "relevance": 65.0, "extraction_method": "text", "source": "native",
            "path": str(self.pdf_root / "aterro.pdf"),
        }
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        pdf_link_env = {
            "CASSI_PDF_SERVER_URL": "http://127.0.0.1:53123",
            "CASSI_PDF_SERVER_TOKEN": "synthetic-test-token",
        }
        with patch.dict(os.environ, pdf_link_env), patch("search_engine.search", return_value=[result]):
            app = AppTest.from_file(str(app_path)).run(timeout=60)
            app.sidebar.radio[0].set_value("Pesquisar no acervo").run(timeout=60)
            app.text_input[0].set_value("Terraplenagem").run(timeout=60)
        markup = "\n".join(element.value for element in app.markdown)
        captions = "\n".join(element.value for element in app.caption)
        self.assertIn("Contexto incerto", markup)
        self.assertIn("Contexto incerto 1", captions)
        self.assertIn("relação com terraplenagem não confirmada", markup)
        self.assertFalse(app.exception)

    def test_navigation_search_table_links_without_exports(self):
        result = {
            "query": "bomba",
            "pdf": "fixture.pdf",
            "page": 2,
            "snippet": "Vazao 346,00 m3/h e quantidade 2,00",
            "matched_text": "346,00 m3/h",
            "match_type": "exact",
            "relation": "",
            "match_reason": "Correspondência exata para a consulta 'bomba'.",
            "relevance": 100.0,
            "extraction_method": "hybrid",
            "source": "ocr",
            "path": str(Path(self.temporary.name) / "previous-archive" / "fixture.pdf"),
        }
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app_source = app_path.read_text(encoding="utf-8")
        assets = app_path.parent / "assets"
        self.assertTrue((assets / "logo-cassi.png").is_file())
        self.assertTrue((assets / "banner-terraplenagem.png").is_file())
        pdf_link_env = {
            "CASSI_PDF_SERVER_URL": "http://127.0.0.1:53123",
            "CASSI_PDF_SERVER_TOKEN": "synthetic-test-token",
        }
        with patch.dict(os.environ, pdf_link_env), patch("search_engine.search", return_value=[result]):
            app = AppTest.from_file(str(app_path)).run(timeout=60)
            self.assertFalse(app.exception)
            page_markup = "\n".join(element.value for element in app.markdown)
            self.assertIn("#b8202e", page_markup)
            self.assertIn("CASSI – ACERVO INTELIGENTE", page_markup)
            self.assertIn("Inteligência e gestão do conhecimento técnico", page_markup)
            self.assertIn("width:63.75% !important; height:auto", page_markup)
            self.assertIn("object-fit:contain", page_markup)
            self.assertIn("white-space:nowrap", page_markup)
            self.assertIn("overflow-x:auto !important; white-space:pre !important", page_markup)
            self.assertIn("max-height:100dvh; overflow-y:auto", page_markup)
            self.assertIn("font-size:clamp(.7rem, .8vw, .84rem)", page_markup)
            self.assertIn("font-weight:750 !important", page_markup)
            self.assertIn("max-width: 780px", page_markup)
            self.assertIn("grid-template-columns:repeat(6,minmax(0,1fr))", page_markup)
            self.assertIn("margin-bottom:.55rem", page_markup)
            self.assertIn("white-space:nowrap", page_markup)
            self.assertIn("min-width:1120px", page_markup)
            self.assertIn(".col-type { width:15%; }", page_markup)
            self.assertIn(".col-method { width:11%; }", page_markup)
            self.assertEqual(page_markup.count("<div class='metric-card "), 6)
            brand_title = "CASSI \u2013 ACERVO INTELIGENTE"
            self.assertNotIn("header_image, header_identity", app_source)
            self.assertIn(brand_title, page_markup)
            self.assertEqual(
                app.sidebar.radio[0].options,
                ["Visão geral", "Pesquisar no acervo", "Atualizar índice",
                 "Ferramentas de OCR", "Backup e recuperação"],
            )

            app.sidebar.radio[0].set_value("Pesquisar no acervo").run(timeout=60)
            search_markup = "\n".join(element.value for element in app.markdown)
            self.assertNotIn(brand_title, search_markup)
            self.assertIn("search-hint", search_markup)
            self.assertIn("Digite uma palavra, expressão ou valor", search_markup)
            self.assertEqual(
                {checkbox.label for checkbox in app.checkbox},
                {"Incluir correspondências aproximadas",
                 "Incluir termos relacionados (não equivalentes)"},
            )
            app.text_input[0].set_value("bomba").run(timeout=60)
            rendered_markdown = "\n".join(element.value for element in app.markdown)
            rendered_captions = "\n".join(element.value for element in app.caption)
            self.assertIn("fixture.pdf", rendered_markdown)
            self.assertIn("Página 2", rendered_markdown)
            self.assertIn("<mark>346,00 m3/h</mark>", rendered_markdown)
            self.assertIn("Correspondência exata para a consulta &#x27;bomba&#x27;.", rendered_markdown)
            self.assertIn("Exatas 1", rendered_captions)
            self.assertIn("http://127.0.0.1:53123/pdf/synthetic-test-token/fixture.pdf#page=2",
                          rendered_markdown)
            self.assertIn(".pdf-link { color:#155f91 !important; font-weight:700; text-decoration:underline; }",
                          page_markup)
            self.assertEqual(len(app.get("download_button")), 0)

            app.sidebar.radio[0].set_value("Atualizar índice").run(timeout=60)
            self.assertNotIn(brand_title, "\n".join(element.value for element in app.markdown))
            self.assertIn("Atualizar índice dos acervos", [button.label for button in app.button])
            app.sidebar.radio[0].set_value("Ferramentas de OCR").run(timeout=60)
            self.assertNotIn(brand_title, "\n".join(element.value for element in app.markdown))
            self.assertIn("Preparar candidata OCR híbrida", [button.label for button in app.button])
            app.sidebar.radio[0].set_value("Backup e recuperação").run(timeout=60)
            self.assertNotIn(brand_title, "\n".join(element.value for element in app.markdown))
            backup_info = "\n".join(element.value for element in app.info)
            self.assertIn("janela de controle do CASSI", backup_info)
            self.assertFalse(app.exception)

    def test_filename_result_is_labeled_without_a_specific_page(self):
        result = {
            "query": "compactacao",
            "pdf": "Relatorio-Compactacao.PDF",
            "page": None,
            "snippet": "Correspondência localizada no nome do PDF; não há página específica.",
            "matched_text": "",
            "match_type": "filename",
            "relation": "",
            "match_reason": "Correspondência parcial no nome do arquivo; sem página específica.",
            "relevance": 100.0,
            "extraction_method": "metadata",
            "source": "filename",
            "path": str(Path(self.temporary.name) / "previous-archive" / "Relatorio-Compactacao.PDF"),
        }
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        pdf_link_env = {
            "CASSI_PDF_SERVER_URL": "http://127.0.0.1:53123",
            "CASSI_PDF_SERVER_TOKEN": "synthetic-test-token",
        }
        with patch.dict(os.environ, pdf_link_env), patch("search_engine.search", return_value=[result]):
            app = AppTest.from_file(str(app_path)).run(timeout=60)
            app.sidebar.radio[0].set_value("Pesquisar no acervo").run(timeout=60)
            app.text_input[0].set_value("compactacao").run(timeout=60)
        markup = "\n".join(element.value for element in app.markdown)
        captions = "\n".join(element.value for element in app.caption)
        self.assertIn("Relatorio-Compactacao.PDF", markup)
        self.assertIn("Sem página específica", markup)
        self.assertIn("Correspondência localizada no nome do PDF", markup)
        self.assertIn("Nome do arquivo", markup)
        self.assertIn("http://127.0.0.1:53123/pdf/synthetic-test-token/Relatorio-Compactacao.PDF", markup)
        self.assertNotIn("#page=", markup)
        self.assertIn("Nomes de arquivo 1", captions)
        self.assertEqual(len(app.get("download_button")), 0)
        self.assertFalse(app.exception)


    def test_archive_folder_can_be_saved_to_temporary_settings(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        selected = Path(self.temporary.name) / "selected-archive"
        selected.mkdir()
        marker = selected / "keep.txt"
        marker.write_text("fictional", encoding="utf-8")
        app = AppTest.from_file(str(app_path)).run(timeout=60)
        app.sidebar.radio[0].set_value("Atualizar \u00edndice").run(timeout=60)
        folder_widget = next(item for item in app.get("text_input")
                             if item.key == "archive_folder_setting")
        folder_widget.set_value(str(selected)).run(timeout=60)
        save_button = next(item for item in app.button if item.label == "Salvar pasta do acervo")
        save_button.click().run(timeout=60)
        settings_path = Path(self.temporary.name) / "settings.json"
        self.assertTrue(settings_path.is_file())
        self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8"))["pdf_folder"],
                         str(selected.resolve()))
        self.assertEqual(marker.read_text(encoding="utf-8"), "fictional")
        self.assertFalse(app.exception)

    def test_agpl_legal_notice_and_source_location_are_visible(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path)).run(timeout=60)
        markup = "\n".join(element.value for element in app.markdown)
        self.assertIn("GNU AGPL-3.0", markup)
        self.assertIn("No warranty is provided", markup)
        self.assertIn("corresponding source", markup)
        self.assertFalse(app.exception)

if __name__ == "__main__":
    unittest.main()
