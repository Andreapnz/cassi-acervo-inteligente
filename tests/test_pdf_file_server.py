import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unittest.mock import patch

from pdf_file_server import AuthorizedPdfServer, build_pdf_url


class AuthorizedPdfServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cassi-pdf-link-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "archive"
        self.root.mkdir()
        self.outside = Path(self.temporary.name) / "outside.pdf"
        self.outside.write_bytes(b"%PDF outside archive")
        self.pdf = self.root / "projeto ficticio.pdf"
        self.payload = b"%PDF-1.4 synthetic-only fixture\n" + bytes(range(64))
        self.pdf.write_bytes(self.payload)
        self.server = AuthorizedPdfServer(self.root).start()
        self.addCleanup(self.server.close)

    def test_inline_response_supports_ranges_and_filename_page_fragment(self):
        url = self.server.url_for(self.pdf, 4)
        self.assertIsNotNone(url)
        self.assertIn("#page=4", url)
        self.assertIn("projeto%20ficticio.pdf", url)
        request = Request(url.split("#", 1)[0], headers={"Range": "bytes=0-4"})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Type"], "application/pdf")
            self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
            self.assertEqual(response.headers["Content-Range"], f"bytes 0-4/{len(self.payload)}")
            self.assertEqual(response.read(), self.payload[:5])

    def test_full_pdf_and_head_requests_are_supported(self):
        url = self.server.url_for(self.pdf)
        with urlopen(url, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), self.payload)
            self.assertNotIn("#page=", response.url)
        with urlopen(Request(url, method="HEAD"), timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(int(response.headers["Content-Length"]), len(self.payload))
            self.assertEqual(response.read(), b"")

    def test_rejects_bad_token_path_traversal_and_non_pdf(self):
        base = self.server.base_url + "/pdf/"
        invalid_urls = (
            base + "wrong-token/projeto%20ficticio.pdf",
            base + self.server.token + "/%2E%2E%2Foutside.pdf",
            base + self.server.token + "/outside.txt",
            base + self.server.token + "/outside.pdf",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(HTTPError) as denied:
                urlopen(url, timeout=3)
            self.assertEqual(denied.exception.code, 404)

    def test_link_builder_needs_server_credentials_and_only_adds_valid_page(self):
        with patch.dict(os.environ, {"CASSI_PDF_SERVER_URL": "",
                                    "CASSI_PDF_SERVER_TOKEN": ""}):
            self.assertIsNone(build_pdf_url(self.pdf))
        url = build_pdf_url(self.pdf, "not-a-page", base_url=self.server.base_url,
                           token=self.server.token)
        self.assertIsNotNone(url)
        self.assertNotIn("#page=", url)

    def test_link_builder_rejects_paths_outside_the_authorized_archive(self):
        url = build_pdf_url(self.outside, 1, base_url=self.server.base_url,
                            token=self.server.token, archive_root=self.root)
        self.assertIsNone(url)
        valid = build_pdf_url(self.pdf, 1, base_url=self.server.base_url,
                              token=self.server.token, archive_root=self.root)
        self.assertTrue(valid.endswith("#page=1"))

    def test_stale_absolute_index_path_resolves_by_name_inside_authorized_archive(self):
        stale_path = Path(self.temporary.name) / "old-location" / self.pdf.name
        url = build_pdf_url(stale_path, 6, base_url=self.server.base_url,
                            token=self.server.token, archive_root=self.root)
        self.assertTrue(url.endswith("#page=6"))
        with urlopen(url.split("#", 1)[0], timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), self.payload)

    def test_link_builder_never_targets_an_external_host(self):
        url = build_pdf_url(self.pdf, 1, base_url="https://example.invalid",
                            token=self.server.token, archive_root=self.root)
        self.assertIsNone(url)

    def test_server_shutdown_closes_the_listener(self):
        url = self.server.url_for(self.pdf)
        self.server.close()
        with self.assertRaises((URLError, OSError)):
            urlopen(url, timeout=1)


if __name__ == "__main__":
    unittest.main()
