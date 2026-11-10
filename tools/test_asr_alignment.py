"""Offline regression tests; never load recognition/alignment models."""
import copy
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import asr


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.source = {"engine": "faster-whisper medium int8", "language": "zh", "timing": "asr_estimate",
                       "segments": [
                           {"start": 0, "end": 2, "text": "你好。再见！", "words": [
                               {"word": "你好。再见！", "start": 0, "end": 2}]},
                           {"start": 2, "end": 4, "text": "第二句。", "words": [
                               {"word": "第二句。", "start": 2, "end": 4}]}]}
        self.wx = SimpleNamespace(load_audio=Mock(return_value="audio"),
                                  load_model=Mock(side_effect=AssertionError("不得调用 WhisperX ASR")),
                                  load_align_model=Mock(return_value=("model", {})),
                                  align=Mock(side_effect=[
                                      {"segments": [
                                          {"start": 1.2, "end": 1.9, "text": "再见！", "words": [
                                              {"word": "再见！", "start": 1.2, "end": 1.9}]},
                                          {"start": 0.1, "end": 1, "text": "你好。", "words": [
                                              {"word": "你好。", "start": 0.1, "end": 1}]}]},
                                      {"segments": [{"start": 2.1, "end": 3.9, "text": "第二句。", "words": [
                                          {"word": "第二句。", "start": 2.1, "end": 3.9}]}]}]))
        patches = [patch.dict(sys.modules, {"whisperx": self.wx,
                                           "whisperx.utils": SimpleNamespace(PUNKT_LANGUAGES={})}),
                   patch.object(asr, "engines", return_value=["whisperx", "faster-whisper"]),
                   patch.object(asr, "transcribe", side_effect=lambda *a: copy.deepcopy(self.source)),
                   patch("nltk.data.find", return_value="local-punkt"),
                   patch("nltk.data.load", return_value=object()),
                   patch("nltk.download", side_effect=AssertionError("不得下载"))]
        self.mocks = [self.enterContext(p) for p in patches]
        self.enterContext(patch.object(asr.align_models, "check_align_model", return_value={
            "language": "zh", "model": "offline-zh", "type": "huggingface", "status": "ok",
            "path": "runtime/models/offline-zh", "download_url": "offline-zh"}))
        self.enterContext(patch.object(asr.align_models, "block_torch_downloads", side_effect=nullcontext))

    def run_asr(self):
        return asr.transcribe_best(Path("unused.wav"), "medium", "int8", "zh", "auto")

    def test_preserves_sentences_and_merges_split_words_by_time(self):
        result = self.run_asr()
        self.assertNotIn("fallback_reason", result)
        self.assertEqual(result["engine"], "faster-whisper medium int8 + wav2vec2 alignment")
        self.assertEqual(result["timing"], "forced_alignment")
        self.assertEqual([s["text"] for s in result["segments"]], [s["text"] for s in self.source["segments"]])
        self.assertEqual(result["segments"][0]["start"], 0.1)
        self.assertEqual(result["segments"][0]["end"], 1.9)
        self.assertEqual([w["start"] for w in result["segments"][0]["words"]], [0.1, 1.2])
        self.assertNotIn("aligned_ratio", result)
        self.wx.load_model.assert_not_called()
        self.wx.load_align_model.assert_called_once_with(language_code="zh", device="cpu", model_cache_only=True)
        self.assertEqual(self.wx.align.call_args_list[0].args[0], [
            {k: self.source["segments"][0][k] for k in ("start", "end", "text")}])

    def test_missing_punkt_falls_back_before_alignment(self):
        self.mocks[3].side_effect = LookupError("missing")
        result = self.run_asr()
        self.assertIn("缺少 NLTK punkt_tab，请重新运行安装脚本的 WhisperX 步骤", result["fallback_reason"])
        self.assertEqual(result["segments"], self.source["segments"])
        self.wx.load_align_model.assert_not_called()
        self.wx.align.assert_not_called()

    def test_alignment_exception_falls_back(self):
        self.wx.align.side_effect = RuntimeError("alignment exploded")
        result = self.run_asr()
        self.assertIn("alignment exploded", result["fallback_reason"])
        self.assertEqual(result["engine"], self.source["engine"])
        self.assertEqual(result["segments"], self.source["segments"])

    def test_incomplete_tokenizer_falls_back_without_downloading(self):
        self.mocks[4].side_effect = LookupError("tokenizer files missing")
        result = self.run_asr()
        self.assertIn("缺少 NLTK punkt_tab，请重新运行安装脚本的 WhisperX 步骤", result["fallback_reason"])
        self.wx.align.assert_not_called()
        self.mocks[5].assert_not_called()

    def test_empty_split_part_keeps_entire_original_sentence(self):
        self.wx.align.side_effect = [
            {"segments": [{"words": [{"word": "你好。", "start": 0.1, "end": 1}]}, {"words": []}]},
            {"segments": []}]
        result = self.run_asr()
        self.assertEqual(result["segments"], self.source["segments"])
        self.assertEqual(result["aligned_ratio"], 0)
        self.assertNotIn("fallback_reason", result)

    def test_failed_sentence_keeps_original_timing(self):
        for words in ([], [{"word": "坏", "end": 3.7}], [{"word": "坏", "start": None, "end": 3.7}],
                      [{"word": "坏", "start": 2.1}], [{"word": "坏", "start": float("nan"), "end": 3.7}]):
            with self.subTest(words=words):
                self.wx.align.side_effect = [
                    {"segments": [{"words": [{"word": "好", "start": 0.1, "end": 1.9}]}]},
                    {"segments": [{"words": words}]}]
                result = self.run_asr()
                self.assertNotIn("fallback_reason", result)
                self.assertEqual(result["aligned_ratio"], 0.5)
                self.assertEqual(result["segments"][1], self.source["segments"][1])
                self.assertEqual(result["segments"][0]["start"], 0.1)

    def test_cache_miss_falls_back_without_online_retry(self):
        self.wx.load_align_model.side_effect = [OSError("cache miss"), ("model", {})]
        result = self.run_asr()
        self.assertIn("cache miss", result["fallback_reason"])
        self.assertEqual(result["timing"], "asr_estimate")
        self.wx.load_align_model.assert_called_once_with(language_code="zh", device="cpu", model_cache_only=True)


if __name__ == "__main__":
    unittest.main()
