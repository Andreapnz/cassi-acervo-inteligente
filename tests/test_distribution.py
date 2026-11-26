import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
import urllib.request

import config
from cassi_launcher import (SingleInstanceGuard, start_server, stop_server)


def _cleanup_tempdir_with_retry(tempdir: tempfile.TemporaryDirectory, timeout: float = 5) -> None:
    """Retry Windows sharing violations after a server process has exited."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            tempdir.cleanup()
            return
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


class DistributionPreparationTests(unittest.TestCase):
    def test_packaged_config_uses_programdata_and_configurable_archive(self):
        with tempfile.TemporaryDirectory(prefix="cassi-frozen-config-") as tmp:
            root = Path(tmp)
            env = os.environ.copy()
            for key in ("CASSI_DATABASE", "CASSI_CERTIFICATE_DATABASE", "CASSI_BACKUP_DIR",
                        "CASSI_PDF_FOLDER", "CASSI_SETTINGS_PATH", "CASSI_TESSERACT_EXE",
                        "CASSI_TESSDATA_DIR", "TESSDATA_PREFIX"):
                env.pop(key, None)
            env.update({"PROGRAMDATA": str(root), "CASSI_DATA_DIR": str(root / "CASSI"),
                        "CASSI_SETTINGS_PATH": str(root / "CASSI" / "settings.json")})
            code = (
                "import json,sys; sys.frozen=True; import config; "
                "print(json.dumps({'data':str(config.APP_DATA_DIR),'db':str(config.DATABASE_PATH),"
                "'certificate':str(config.CERTIFICATE_ANALYSIS_DATABASE_PATH),"
                "'backups':str(config.DATABASE_BACKUP_DIR),'pdf':str(config.DEFAULT_PDF_FOLDER),"
                "'program':str(config.BASE_DIR),'tesseract':str(config.TESSERACT_EXE),"
                "'tessdata':str(config.TESSDATA_DIR)}))"
            )
            result = subprocess.run([sys.executable, "-c", code], env=env,
                                    capture_output=True, text=True, check=True)
            paths = json.loads(result.stdout)
            expected_data = (root / "CASSI").resolve()
            self.assertEqual(Path(paths["data"]), expected_data)
            self.assertEqual(Path(paths["db"]), expected_data / "cassi.db")
            self.assertEqual(Path(paths["certificate"]), expected_data / "certificate_analysis.db")
            self.assertEqual(Path(paths["backups"]), expected_data / "Backups")
            self.assertEqual(Path(paths["pdf"]), expected_data / "Acervos")
            self.assertNotEqual(Path(paths["db"]).parent, Path(paths["program"]))
            self.assertEqual(Path(paths["tesseract"]), Path(paths["program"]) / "tesseract" / "tesseract.exe")
            self.assertEqual(Path(paths["tessdata"]), Path(paths["program"]) / "tesseract" / "tessdata")

    def test_archive_folder_setting_persists_without_touching_contents(self):
        with tempfile.TemporaryDirectory(prefix="cassi-archive-setting-") as tmp:
            root = Path(tmp)
            app_data = root / "data"
            settings = app_data / "settings.json"
            archive = root / "archive"
            archive.mkdir()
            original = archive / "keep.dat"
            original.write_bytes(b"unchanged")
            with patch.object(config, "APP_DATA_DIR", app_data), \
                    patch.object(config, "APP_SETTINGS_PATH", settings), \
                    patch.object(config, "DEFAULT_PDF_FOLDER", config.DEFAULT_PDF_FOLDER):
                selected = config.set_pdf_folder(archive)
                self.assertEqual(selected, archive.resolve())
                self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["pdf_folder"],
                                 str(archive.resolve()))
                self.assertEqual(original.read_bytes(), b"unchanged")

    @unittest.skipUnless(os.name == "nt", "Named mutex is a Windows launcher feature")
    def test_named_mutex_blocks_a_second_launcher_in_the_same_session(self):
        name = rf"Local\CASSI-Test-{uuid4().hex}"
        first = SingleInstanceGuard(name)
        try:
            self.assertTrue(first.acquired)
            second = SingleInstanceGuard(name)
            self.assertFalse(second.acquired)
        finally:
            first.close()

    @unittest.skipUnless(os.name == "nt", "Server process management targets Windows")
    def test_local_streamlit_child_starts_and_stops_with_temporary_paths(self):
        from database import begin_attempt, connect, initialize

        with tempfile.TemporaryDirectory(prefix="cassi-launcher-test-") as tmp:
            root = Path(tmp)
            archive = root / "pdfs"
            archive.mkdir()
            (archive / "approved fixture.pdf").write_bytes(b"%PDF synthetic local fixture")
            (root / "outside.pdf").write_bytes(b"%PDF must not be served")
            data_dir = root / "data"
            data_dir.mkdir()
            database_path = data_dir / "cassi.db"
            initialize(database_path)
            attempt = begin_attempt(database_path, str(archive / "interrupted.pdf"),
                                    "interrupted.pdf", "f" * 64)
            with connect(database_path) as conn:
                conn.execute("UPDATE processing_attempts SET owner_pid=? WHERE id=?",
                             (2147483647, attempt["attempt_id"]))
            overrides = {
                "CASSI_DATA_DIR": str(data_dir),
                "CASSI_DATABASE": str(database_path),
                "CASSI_CERTIFICATE_DATABASE": str(data_dir / "certificate_analysis.db"),
                "CASSI_BACKUP_DIR": str(data_dir / "backups"),
                "CASSI_SETTINGS_PATH": str(data_dir / "settings.json"),
                "CASSI_PDF_FOLDER": str(archive),
            }
            captured_environment = {}
            real_popen = subprocess.Popen

            def capture_child_environment(*args, **kwargs):
                captured_environment.update(kwargs.get("env", {}))
                return real_popen(*args, **kwargs)

            with patch("cassi_launcher.subprocess.Popen", side_effect=capture_child_environment):
                server = start_server(env_overrides=overrides, timeout=45)
            served_pdf_url = None
            try:
                with urllib.request.urlopen(server.url, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                self.assertTrue(server.url.startswith("http://127.0.0.1:"))
                self.assertIsNone(server.process.poll())
                self.assertIsNotNone(server.pdf_server)
                self.assertEqual(captured_environment["CASSI_PDF_SERVER_URL"],
                                 server.pdf_server.base_url)
                self.assertEqual(captured_environment["CASSI_PDF_SERVER_TOKEN"],
                                 server.pdf_server.token)
                pdf_url = server.pdf_server.url_for(archive / "approved fixture.pdf", 3)
                served_pdf_url = pdf_url.split("#", 1)[0]
                self.assertTrue(pdf_url.startswith(server.pdf_server.base_url + "/pdf/"))
                pdf_request = urllib.request.Request(pdf_url.split("#", 1)[0],
                                                     headers={"Range": "bytes=0-4"})
                with urllib.request.urlopen(pdf_request, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), b"%PDF ")
                outside_url = server.pdf_server.base_url + "/pdf/" + server.pdf_server.token + "/outside.pdf"
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(outside_url, timeout=5)
                self.assertEqual(denied.exception.code, 404)
                with connect(database_path) as conn:
                    recovered = conn.execute(
                        "SELECT status FROM processing_attempts WHERE id=?", (attempt["attempt_id"],)
                    ).fetchone()
                self.assertEqual(recovered["status"], "interrupted")
            finally:
                process = server.process
                stop_server(server)
            self.assertIsNotNone(process.poll())
            self.assertTrue((root / "data" / "logs" / "streamlit.log").is_file())
            with self.assertRaises((urllib.error.URLError, OSError)):
                urllib.request.urlopen(served_pdf_url, timeout=1)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object shutdown protection")
    def test_closing_launcher_job_kills_server_child(self):
        tmp = tempfile.TemporaryDirectory(prefix="cassi-job-close-test-")
        server = None
        try:
            root = Path(tmp.name)
            (root / "pdfs").mkdir()
            env = {
                "CASSI_DATA_DIR": str(root / "data"),
                "CASSI_DATABASE": str(root / "data" / "cassi.db"),
                "CASSI_CERTIFICATE_DATABASE": str(root / "data" / "certificate_analysis.db"),
                "CASSI_BACKUP_DIR": str(root / "data" / "backups"),
                "CASSI_SETTINGS_PATH": str(root / "data" / "settings.json"),
                "CASSI_PDF_FOLDER": str(root / "pdfs"),
            }
            server = start_server(env_overrides=env, timeout=45)
            process = server.process
            server.job.close()  # Simulates the parent launcher exiting unexpectedly.
            process.wait(timeout=10)
            self.assertIsNotNone(process.poll())
            self.assertTrue((root / "data" / "logs" / "streamlit.log").is_file())
        finally:
            if server is not None and server.process.poll() is None:
                stop_server(server)
            _cleanup_tempdir_with_retry(tmp)


if __name__ == "__main__":
    unittest.main()
