"""Round 10 offline image limits; real project assets are strictly read-only."""
import base64
import copy
import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, llm, media, pipeline, store
from tools.project_fixture import copy_media, load_project

ASSETS = ROOT / "projects/p1027195039-ccc0/assets"
PORTRAIT = ASSETS / "a1027195039-9e4e.png"
PRODUCT = ASSETS / "a1027195039-125e.png"
FRAME = ASSETS / "frame-C1-0.50.jpg"
LIMIT = 3_500_000


def image_data(part):
    head, data = part["image_url"]["url"].split(",", 1)
    return head, base64.b64decode(data)


def raw_part(path):
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {
        "url": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()}}


def png(path, width, height, transparent=False):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    # Hidden red under alpha=0 must become white, while opaque blue stays blue.
    row = b"\0" + bytes([255, 0, 0, 0 if transparent else 255]) * (width // 2)
    row += bytes([0, 0, 255, 255]) * (width - width // 2)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))
    return path


def dimensions(path):
    result = media._run([str(ROOT / "runtime/ffmpeg/ffprobe"), "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=width,height", "-of", "json", str(path)], 60)
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]


class LLMImageTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="llm-images-test-", dir=ROOT / "runtime/temp")))
        # Freeze the JSON used indirectly by pipeline/store/gateway as well as
        # every image input. Keep a separate read-only integrity check below.
        fixture_id = 'p1027195039-ccc0'
        real_directory = ROOT / 'projects' / fixture_id
        self.real_paths = [real_directory / relative for relative in (
            'project.json', 'assets/a1027195039-9e4e.png',
            'assets/a1027195039-125e.png', 'assets/frame-C1-0.50.jpg')]
        self.real_before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.real_paths}
        self.addCleanup(self.assert_real_inputs_unchanged)
        self.enterContext(patch.object(config, 'PROJECTS_DIR', self.temp))
        directory = self.temp / fixture_id
        copy_media(fixture_id, directory)
        store.atomic_write_json(directory / 'project.json', load_project(fixture_id))
        for name, value in {
            'ASSETS': directory / 'assets',
            'PORTRAIT': directory / 'assets/a1027195039-9e4e.png',
            'PRODUCT': directory / 'assets/a1027195039-125e.png',
            'FRAME': directory / 'assets/frame-C1-0.50.jpg',
        }.items():
            self.enterContext(patch.object(sys.modules[__name__], name, value))
        self.enterContext(patch.object(llm, "_IMAGE_CACHE", self.temp / "cache", create=True))
        self.enterContext(patch.object(config, "_parse", side_effect=AssertionError("禁止读取配置")))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(config, "provider_settings", return_value={
            "model": "offline-model", "base_url": "https://offline.invalid", "api_key": "offline-key", "timeout": 1}))
        self.enterContext(patch.object(llm, "_jimmy_dns_shim"))
        self.enterContext(patch.object(llm, "_ssl_context", return_value=None))
        self.requests = []
        self.payload = {"choices": [{"message": {"content": "{}"}}]}
        self.request = self.enterContext(patch("urllib.request.urlopen", side_effect=self.respond))

    def assert_real_inputs_unchanged(self):
        self.assertEqual(self.real_before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.real_paths})

    def respond(self, request, **kwargs):
        self.requests.append(request.data)
        return io.BytesIO(json.dumps(self.payload).encode())

    def messages(self, parts):
        return [{"role": "system", "content": "离线测试"}, {"role": "user", "content": parts}]

    def save_part(self, part, name="sent.jpg"):
        path = self.temp / name
        path.write_bytes(image_data(part)[1])
        return path

    def test_real_draft_three_images_under_one_mb_and_originals_unchanged(self):
        paths = [PORTRAIT, PRODUCT, FRAME, ASSETS.parent / "project.json"]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        # Exercise the actual draft message builder; suppress its project writes/frame extraction.
        with patch.object(media, "extract_frame", return_value=FRAME), patch.object(store, "update"):
            pipeline.draft_camera_image("p1027195039-ccc0", "jimmy", "C1", .5)
        self.assertEqual(len(self.requests), 1)
        self.assertLess(len(self.requests[0]), 1_000_000)
        parts = [p for p in json.loads(self.requests[0])["messages"][1]["content"] if p["type"] == "image_url"]
        self.assertEqual(len(parts), 3)
        for part in parts[:2]:
            self.assertEqual(image_data(part)[0], "data:image/jpeg;base64")
            self.assertLessEqual(max(dimensions(self.save_part(part))), 1536)
        self.assertEqual(image_data(parts[2])[1], FRAME.read_bytes())
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_small_jpeg_and_png_are_unchanged(self):
        for path in [FRAME, png(self.temp / "small.png", 64, 48)]:
            with self.subTest(path=path.name), patch.object(media, "_run", wraps=media._run) as run:
                part = llm.image_part(path, caption="参考图")
                self.assertEqual(part[0], {"type": "text", "text": "参考图"})
                self.assertEqual(image_data(part[1])[1], path.read_bytes())
                self.assertFalse(any(Path(c.args[0][0]).name == "ffmpeg" for c in run.call_args_list))

    def test_transparent_png_white_background_and_dimension_trigger(self):
        path = png(self.temp / "transparent.png", 1800, 1200, transparent=True)
        self.assertLess(path.stat().st_size, 1_000_000)
        part = llm.image_part(path)[0]
        self.assertEqual(image_data(part)[0], "data:image/jpeg;base64")
        sent = self.save_part(part)
        self.assertEqual(dimensions(sent), (1536, 1024))
        # Sample full-resolution interior pixels instead of interpolated boundary pixels.
        pixels = subprocess.run([str(ROOT / "runtime/ffmpeg/ffmpeg"), "-v", "error", "-i", str(sent),
                                 "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                                capture_output=True, check=True).stdout
        left, right = (512 * 1536 + 100) * 3, (512 * 1536 + 1400) * 3
        self.assertTrue(all(v >= 245 for v in pixels[left:left + 3]))
        self.assertLess(pixels[right], 15)
        self.assertGreater(pixels[right + 2], 240)

    def test_cached_second_call_does_not_run_ffmpeg(self):
        with patch.object(media, "_run", wraps=media._run) as run:
            first = llm.image_part(PORTRAIT)
            self.assertTrue(any(Path(c.args[0][0]).name == "ffmpeg" for c in run.call_args_list))
        with patch.object(media, "_run", side_effect=AssertionError("缓存命中后不应调用 ffmpeg/ffprobe")):
            self.assertEqual(first, llm.image_part(PORTRAIT))
        names = [p.name for p in (self.temp / "cache").glob("*.jpg")]
        self.assertTrue(any(hashlib.sha256(PORTRAIT.read_bytes()).hexdigest() in n and "1536" in n for n in names))

    def test_over_limit_reduces_largest_first_and_preserves_messages(self):
        small, large = raw_part(PRODUCT), raw_part(PORTRAIT)
        self.assertGreater(len(large["image_url"]["url"]), len(small["image_url"]["url"]))
        messages = self.messages([small, large])
        before = copy.deepcopy(messages)
        llm.chat("jimmy", messages)
        self.assertLessEqual(len(self.requests[0]), LIMIT)
        sent = json.loads(self.requests[0])["messages"][1]["content"]
        self.assertEqual(sent[0], small)
        self.assertLessEqual(max(dimensions(self.save_part(sent[1]))), 1536)
        self.assertEqual(messages, before)

    def test_still_over_limit_rejects_without_urlopen(self):
        messages = self.messages([raw_part(PORTRAIT)] * 20)
        with patch.object(llm, '_REQUEST_LIMIT', 256):
            with self.assertRaisesRegex(llm.LLMError, r"超过本地安全上限.*未发送到模型"):
                llm.chat("jimmy", messages)
        self.request.assert_not_called()
        cached = list((self.temp / "cache").glob("*.jpg"))
        self.assertTrue(cached)
        self.assertTrue(all(max(dimensions(p)) <= 1536 for p in cached))
        self.assertTrue(any('-s640-' in p.name for p in cached))

    def test_gemini_inline_images_are_also_reduced(self):
        self.payload = {"code": 0, "data": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}}
        llm.chat("jimmy", self.messages([raw_part(PORTRAIT), raw_part(PRODUCT)]), model="gemini-offline")
        self.assertLessEqual(len(self.requests[0]), LIMIT)
        parts = json.loads(self.requests[0])["contents"][0]["parts"]
        self.assertTrue(any(p.get("inline_data", {}).get("mime_type") == "image/jpeg" for p in parts))

    def test_gemini_video_only_keeps_existing_video_path(self):
        video = self.temp / "mock-video.mp4"
        video.write_bytes(b"offline mock video" * 160_000)
        self.payload = {"code": 0, "data": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}}
        result = llm.chat("jimmy", self.messages([llm.video_part(video)]), model="gemini-offline")
        self.assertEqual(result["content"], "ok")
        self.assertGreater(len(self.requests[0]), LIMIT)
        parts = json.loads(self.requests[0])["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[-1]["inline_data"]["data"]), video.read_bytes())

    def test_gemini_mixed_video_and_image_still_enforces_total_limit(self):
        video = self.temp / "mock-video.mp4"
        video.write_bytes(b"offline mock video" * 160_000)
        with self.assertRaisesRegex(llm.LLMError, "超过本地安全上限"):
            llm.chat("jimmy", self.messages([llm.video_part(video), raw_part(FRAME)]), model="gemini-offline")
        self.request.assert_not_called()

    def test_parameter_error_reports_sent_size_and_image_count(self):
        self.payload = {"code": -1, "msg": "参数格式错误"}
        for model in ["offline-model", "gemini-offline"]:
            with self.subTest(model=model), self.assertRaises(llm.LLMError) as error:
                llm.chat("jimmy", self.messages([raw_part(PORTRAIT), raw_part(PRODUCT)]), model=model)
            message = str(error.exception)
            self.assertIn("参数格式错误", message)
            self.assertIn(f"{len(self.requests[-1]) / 1_000_000:.2f} MB", message)
            self.assertIn("2 张", message)
            self.assertNotIn("base64", message)
            self.assertNotIn("offline-key", message)

    def test_small_request_is_sent_without_image_reencoding(self):
        messages = self.messages([raw_part(FRAME)])
        with patch.object(media, "_run", side_effect=AssertionError("小请求不应转换图片")):
            llm.chat("jimmy", messages)
        self.assertEqual(json.loads(self.requests[0])["messages"], messages)


if __name__ == "__main__":
    from tools.run_tests_offline import audit
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
