"""Alignment availability and download prevention, entirely offline."""
import copy
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from console import asr

EN_FILE = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
EN_URL = "https://download.pytorch.org/torchaudio/models/" + EN_FILE
ZH_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
NETWORK_EVENTS = []


def audit(event, args):
    if event.startswith("socket."):
        NETWORK_EVENTS.append(event)
        raise AssertionError(f"离线测试禁止：{event}")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if Path(os.fsdecode(args[0])).name == "console.env":
            raise AssertionError("禁止读取或修改 console.env")


sys.addaudithook(audit)


class AlignModelTests(unittest.TestCase):
    def setUp(self):
        NETWORK_EVENTS.clear()
        (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp")))
        self.checkpoints = self.temp / "torch/hub/checkpoints"
        self.checkpoints.mkdir(parents=True)
        self.hub = self.temp / "huggingface/hub"
        self.source = {"engine": "faster-whisper medium int8", "language": "en", "timing": "asr_estimate",
                       "segments": [{"start": 0, "end": 1, "text": "Hello.", "words": []}]}
        self.wx = SimpleNamespace(load_audio=Mock(return_value="audio"),
                                  load_align_model=Mock(return_value=("model", {})),
                                  align=Mock(return_value={"segments": [{"words": [
                                      {"word": "Hello.", "start": 0.1, "end": 0.9}]}]}))
        self.download = Mock(side_effect=AssertionError("不得调用原始下载函数"))
        self.torch_hub = SimpleNamespace(download_url_to_file=self.download)
        self.maps = SimpleNamespace(DEFAULT_ALIGN_MODELS_TORCH={"en": "WAV2VEC2_ASR_BASE_960H",
                                                               "fr": "FRENCH_PIPELINE"},
                                    DEFAULT_ALIGN_MODELS_HF={"zh": ZH_MODEL})
        modules = {"whisperx": self.wx, "whisperx.alignment": self.maps,
                   "whisperx.utils": SimpleNamespace(PUNKT_LANGUAGES={}),
                   "torch": SimpleNamespace(hub=self.torch_hub), "torch.hub": self.torch_hub,
                   "torchaudio": SimpleNamespace(pipelines=SimpleNamespace(
                       WAV2VEC2_ASR_BASE_960H=SimpleNamespace(_path=EN_FILE),
                       FRENCH_PIPELINE=SimpleNamespace(_path="french.pth")))}
        # Import nltk before replacing torch (some dependencies inspect module specs).
        self.enterContext(patch("nltk.data.find", return_value="punkt"))
        self.enterContext(patch("nltk.data.load", return_value=object()))
        self.nltk_download = self.enterContext(patch("nltk.download", side_effect=AssertionError("不得下载")))
        self.enterContext(patch.dict(sys.modules, modules))
        self.enterContext(patch.object(asr, "engines", return_value=["whisperx", "faster-whisper"]))
        self.enterContext(patch.object(asr, "transcribe", side_effect=lambda *a: copy.deepcopy(self.source)))
        self.checker = asr.align_models
        self.enterContext(patch.object(self.checker, "CHECKPOINTS_DIR", self.checkpoints))
        self.enterContext(patch.object(self.checker, "HF_HUB", self.hub))

    def tearDown(self):
        self.assertEqual(NETWORK_EVENTS, [])
        self.download.assert_not_called()
        self.nltk_download.assert_not_called()
        self.assertIs(self.torch_hub.download_url_to_file, self.download)

    def run_asr(self):
        return asr.transcribe_best(Path("unused.wav"), "medium", "int8", None, "auto")

    def valid_en(self):
        path = self.checkpoints / EN_FILE
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("data.pkl", "offline fixture")
        self.enterContext(patch.object(self.checker, "EN_MODEL_SIZE", path.stat().st_size))
        return path

    def valid_zh(self):
        repo = self.hub / ("models--" + ZH_MODEL.replace("/", "--"))
        snapshot = repo / "snapshots/test-revision"
        snapshot.mkdir(parents=True)
        (repo / "refs").mkdir()
        (repo / "refs/main").write_text("test-revision")
        for name in ("config.json", "vocab.json", "preprocessor_config.json"):
            (snapshot / name).write_text('{"fixture": true}')
        (snapshot / "model.safetensors").write_bytes(b"offline weights")
        return repo

    def test_missing_en_falls_back_before_loader_with_install_details(self):
        result = self.run_asr()
        self.wx.load_align_model.assert_not_called()
        self.assertEqual(result["timing"], "asr_estimate")
        reason = result["fallback_reason"]
        for value in ("en", "WAV2VEC2_ASR_BASE_960H", str((self.checkpoints / EN_FILE).relative_to(ROOT)),
                      EN_URL, "377664473"):
            self.assertIn(value, reason)

    def test_nonzip_and_wrong_size_are_broken(self):
        path = self.checkpoints / EN_FILE
        for zipped in (False, True):
            with self.subTest(zipped=zipped):
                path.write_bytes(b"incomplete")
                if zipped:
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("data.pkl", "small")
                # Non-zip data must fail even when its byte count is correct.
                size = 377_664_473 if zipped else path.stat().st_size
                with patch.object(self.checker, "EN_MODEL_SIZE", size):
                    result = self.run_asr()
                    self.assertEqual(self.checker.check_align_model("en")["status"], "broken")
                self.wx.load_align_model.assert_not_called()
                self.assertIn("文件不完整或已损坏，请重新下载后替换", result.get("fallback_reason", ""))

    def test_valid_en_passes_checkpoints_directory(self):
        self.valid_en()
        result = self.run_asr()
        self.assertEqual(result["timing"], "forced_alignment")
        self.wx.load_align_model.assert_called_once_with(language_code="en", device="cpu", model_cache_only=True,
                                                        model_dir=str(self.checkpoints))

    def test_zh_keeps_cache_only_and_never_retries_online(self):
        self.valid_zh()
        self.source["language"] = "zh"
        self.assertEqual(self.run_asr()["timing"], "forced_alignment")
        self.wx.load_align_model.assert_called_once_with(language_code="zh", device="cpu", model_cache_only=True)
        self.wx.load_align_model.reset_mock(side_effect=True)
        self.wx.load_align_model.side_effect = OSError("cache miss")
        result = self.run_asr()
        self.assertEqual(result["timing"], "asr_estimate")
        self.assertIn(ZH_MODEL, result["fallback_reason"])
        self.assertEqual(self.wx.load_align_model.call_count, 1)

    def test_torch_download_is_blocked_and_restored(self):
        self.valid_en()
        self.wx.load_align_model.side_effect = lambda **kw: self.torch_hub.download_url_to_file(EN_URL, "unused")
        result = self.run_asr()
        self.assertEqual(result["timing"], "asr_estimate")
        self.assertIn("禁止下载", result["fallback_reason"])
        self.assertEqual(self.wx.load_align_model.call_count, 1)

    def test_unmapped_language_skips_loader(self):
        self.source["language"] = "xx"
        result = self.run_asr()
        self.wx.load_align_model.assert_not_called()
        for value in ("xx", "映射", "runtime/models"):
            self.assertIn(value, result["fallback_reason"])

    def test_other_torch_languages_do_not_require_english_size(self):
        self.source["language"] = "fr"
        with zipfile.ZipFile(self.checkpoints / "french.pth", "w") as archive:
            archive.writestr("data.pkl", "small")
        self.assertEqual(self.run_asr()["timing"], "forced_alignment")
        self.assertEqual(self.wx.load_align_model.call_args.kwargs.get("model_dir"), str(self.checkpoints))

    def test_hf_missing_and_incomplete_snapshots(self):
        self.assertEqual(self.checker.check_align_model("zh")["status"], "missing")
        repo = self.valid_zh()
        self.assertEqual(self.checker.check_align_model("zh")["status"], "ok")
        (repo / "snapshots/test-revision/config.json").unlink()
        self.assertEqual(self.checker.check_align_model("zh")["status"], "broken")
        self.source["language"] = "zh"
        self.assertIn("请重新下载后替换", self.run_asr()["fallback_reason"])
        self.wx.load_align_model.assert_not_called()

    def test_report_and_cli_share_model_status(self):
        import io
        from contextlib import redirect_stdout
        from console import runtime_env
        from tools import check_align_models
        self.valid_zh()
        with patch.object(runtime_env, "_size", return_value=0):
            models = runtime_env.report()["align_models"]
        self.assertEqual([(m["language"], m["status"]) for m in models], [("zh", "ok"), ("en", "missing")])
        for model in models:
            self.assertTrue(all(model[k] for k in ("model", "type", "path", "download_url")))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(check_align_models.main(), 0)
        for value in ("zh = ok", "en = missing", EN_URL, "377664473"):
            self.assertIn(value, output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
