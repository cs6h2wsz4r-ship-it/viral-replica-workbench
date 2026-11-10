"""Offline tests for archive safety, retries, verification and cleanup."""
import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.fetch_punkt_tab"), "共享下载器尚未实现")
        from tools import fetch_punkt_tab
        self.fetch = fetch_punkt_tab
        (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix="punkt-test-", dir=ROOT / "runtime/temp"))
        self.dest = Path(self.temp) / "nltk"

    def archive(self, names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in names:
                archive.writestr(name, data)
        buf.seek(0)
        return buf

    def test_rejects_unsafe_archive_before_writing(self):
        for name in ("../evil.txt", "punkt_tab/../evil.txt", "/punkt_tab/evil.txt", "other/evil.txt",
                     "punkt_tab/evil.py", "punkt_tab/..\\evil.txt", "C:/punkt_tab/evil.txt"):
            with self.subTest(name=name), zipfile.ZipFile(self.archive([
                    ("punkt_tab/english/good.tab", "good"), (name, "bad")])) as archive:
                with self.assertRaises(ValueError):
                    self.fetch.extract_archive(archive, self.dest)
                self.assertFalse(self.dest.exists())

    def test_extracts_allowed_files(self):
        with zipfile.ZipFile(self.archive([("punkt_tab/", ""), ("punkt_tab/english/", ""),
                                         ("punkt_tab/README", "readme"), ("punkt_tab/english/a.tab", "tab"),
                                         ("punkt_tab/english/a.txt", "text")])) as archive:
            self.fetch.extract_archive(archive, self.dest)
        self.assertEqual((self.dest / "tokenizers/punkt_tab/english/a.tab").read_text(), "tab")

    def test_rejects_oversized_archive(self):
        with zipfile.ZipFile(self.archive([("punkt_tab/a.txt", "large")])) as archive:
            archive.infolist()[0].file_size = 50 * 1024 * 1024 + 1
            with self.assertRaisesRegex(ValueError, "50"):
                self.fetch.extract_archive(archive, self.dest)
        self.assertFalse(self.dest.exists())

    def test_rejects_symlink(self):
        entry = zipfile.ZipInfo("punkt_tab/link.txt")
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        with zipfile.ZipFile(self.archive([(entry, "outside")])) as archive:
            with self.assertRaises(ValueError):
                self.fetch.extract_archive(archive, self.dest)

    def test_download_retries_twice_and_cleans_temp(self):
        before = set((ROOT / "runtime/temp").iterdir())
        with patch.object(self.fetch.urllib.request, "urlopen", side_effect=OSError("offline")) as request:
            with self.assertRaisesRegex(RuntimeError, "下载失败"):
                self.fetch.fetch(self.dest)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(request.call_args.kwargs["timeout"], 120)
        self.assertEqual(set((ROOT / "runtime/temp").iterdir()), before)

    def test_existing_valid_data_skips_network(self):
        with patch.object(self.fetch, "verify", return_value=None), \
                patch.object(self.fetch.urllib.request, "urlopen", side_effect=AssertionError("不应下载")) as request:
            self.fetch.fetch(self.dest)
            request.assert_not_called()

    def test_rejects_existing_destination_symlink(self):
        outside = Path(self.temp) / "outside"
        outside.mkdir()
        self.dest.mkdir()
        (self.dest / "tokenizers").symlink_to(outside, target_is_directory=True)
        with zipfile.ZipFile(self.archive([("punkt_tab/evil.txt", "bad")])) as archive:
            with self.assertRaisesRegex(ValueError, "符号链接"):
                self.fetch.extract_archive(archive, self.dest)
        self.assertEqual(list(outside.iterdir()), [])

    def test_bad_download_never_reaches_destination(self):
        before = set((ROOT / "runtime/temp").iterdir())
        with patch.object(self.fetch.urllib.request, "urlopen", return_value=self.archive([
                ("punkt_tab/english/good.tab", "good"), ("../evil.txt", "bad")])):
            with self.assertRaisesRegex(ValueError, "不安全路径"):
                self.fetch.fetch(self.dest)
        self.assertFalse(self.dest.exists())
        self.assertEqual(set((ROOT / "runtime/temp").iterdir()), before)

    def test_cli_failure_is_nonzero_with_chinese_reason(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["fetch_punkt_tab.py", "--dest", str(self.dest)]), \
                patch.object(self.fetch, "fetch", side_effect=RuntimeError("下载失败")), \
                patch.object(sys, "stderr", stderr):
            self.assertEqual(self.fetch.main(), 1)
        self.assertIn("punkt_tab 准备失败：下载失败", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
