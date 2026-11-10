"""Offline script/ASR regressions. Real project evidence is read-only; all models are mocked."""
import copy
import json
import socket
import sys
import tempfile
import unittest
import wave
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Import before tests patch sys.modules: unloading/reimporting NumPy's native
# extension after NLTK's lazy import is unsupported in a shared test process.
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import asr, config, evidence, llm, pipeline, store


def segment(a, b, **fields):
    return {"sourceStart": a, "sourceEnd": b, "kind": "speaker", "dialogue": "Model rewrite!",
            "title": "段落", "action": "Act naturally.", "duration": 4, **fields}


ENGLISH = {"language": "en", "segments": [
    {"start": 0, "end": 5, "text": "Hello, world! I'm here.", "words": [
        {"word": "Hello,", "start": 0, "end": 1}, {"word": "world!", "start": 1, "end": 2},
        {"word": "I'm", "start": 2, "end": 3}, {"word": "here.", "start": 3, "end": 5}]}]}


class ScriptRulesTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp")))
        self.enterContext(patch.object(config, "PROJECTS_DIR", self.temp))
        self.enterContext(patch.object(config, "_parse", side_effect=AssertionError("不得读取配置")))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch("subprocess.run", side_effect=AssertionError("不得运行外部程序")))
        self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("不得联网")))
        self.model = self.enterContext(patch.object(llm, "chat_json"))
        self.pid = store.create("script rules offline")["id"]
        self.adir = store.project_dir(self.pid) / "analysis"
        store.update(self.pid, lambda d: d.update(
            source={"probe": {"duration": 60, "ratio_hint": "9:16"}},
            breakdown={"overview": {"language": "en"}},
            assets=[{"id": "person-a", "type": "image"}, {"id": "person-b", "type": "image"}]))

    def run_script(self, segments, transcript=ENGLISH, boundaries=None, mode="keep"):
        store.atomic_write_json(self.adir / "transcript.raw.json", transcript)
        store.atomic_write_json(self.adir / "boundaries.json", boundaries or {
            "merged_shots": [{"start": 0, "end": 60}]})
        self.model.return_value = ({"segments": copy.deepcopy(segments)}, {"model": "mock"})
        pipeline.run_script(self.pid, "jimmy", mode, "", None)
        return store.load(self.pid)["script"]

    def test_real_five_beats_become_one_fifteen_second_segment(self):
        # Fixed evidence: this regression must survive deletion of a user's old project.
        transcript = {"language": "en", "segments": [{"start": 0, "end": 15.167,
                      "text": "Hello world.", "words": [{"word": "Hello", "start": 0, "end": 1},
                                                           {"word": "world.", "start": 14, "end": 15.167}]}]}
        boundaries = {"merged_shots": [{"start": 0, "end": 15.167}]}
        end = boundaries["merged_shots"][-1]["end"]
        points = [0, 3.2, 6.4, 9.6, 12.9, end]
        segments = [segment(a, b, action=f"Action {i}", overlays=[{"text": str(i)}])
                    for i, (a, b) in enumerate(zip(points, points[1:]))]
        script = self.run_script(segments[::-1], transcript, boundaries)
        self.assertEqual(len(script["tasks"]), 1)
        task = script["tasks"][0]
        self.assertEqual(task["duration"], 15)
        self.assertEqual(task["source_end"], end)
        self.assertEqual(task["dialogue"], "".join(s["text"] for s in transcript["segments"]))
        self.assertEqual(task["action"], "\n".join(f"Action {i}" for i in range(5)))
        self.assertEqual(len(task["overlays"]), 5)
        self.assertEqual(script["dialogue"], [{"segment": "seg-01", "text": task["dialogue"]}])

    def test_real_cut_is_retained_even_when_model_spans_it(self):
        boundaries = {"merged_shots": [{"start": 0, "end": 5}, {"start": 5, "end": 10}]}
        for segments in ([segment(0, 5), segment(5, 10)], [segment(0, 10)]):
            with self.subTest(segments=segments):
                tasks = self.run_script(segments, boundaries=boundaries)["tasks"]
                self.assertEqual([(t["source_start"], t["source_end"]) for t in tasks], [(0, 5), (5, 10)])

    def test_long_continuous_shot_is_split_without_short_tail(self):
        tasks = self.run_script([segment(0, 32)])["tasks"]
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["source_start"], 0)
        self.assertEqual(tasks[-1]["source_end"], 32)
        for a, b in zip(tasks, tasks[1:]):
            self.assertEqual(a["source_end"], b["source_start"])
        self.assertTrue(all(4 <= t["duration"] <= 15 and 4 <= t["source_end"] - t["source_start"] <= 15 for t in tasks))

    def test_references_kind_footage_and_scene_changes_are_kept(self):
        variants = [({"imageIds": ["person-a"]}, {"imageIds": ["person-b"]}),
                    ({"kind": "speaker"}, {"kind": "broll"}),
                    ({"useSourceFootage": True}, {"useSourceFootage": False}),
                    ({}, {"sceneChange": True, "splitReason": "场景变化：切换到室外"})]
        for a, b in variants:
            with self.subTest(a=a, b=b):
                tasks = self.run_script([segment(0, 5, **a), segment(5, 10, **b)])["tasks"]
                self.assertEqual(len(tasks), 2)
                if "imageIds" in a:
                    self.assertEqual([t["image_ids"] for t in tasks], [a["imageIds"], b["imageIds"]])

    def test_same_reference_set_merges_and_short_segments_merge_first(self):
        tasks = self.run_script([segment(0, 2, imageIds=["person-a", "person-b"]),
                                 segment(2, 9, imageIds=["person-b", "person-a"])])["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["duration"], 9)

    def test_merge_chooses_fewest_legal_segments(self):
        tasks = self.run_script([segment(a, b) for a, b in [(0, 6), (6, 11), (11, 16), (16, 22)]])["tasks"]
        self.assertEqual([(t["source_start"], t["source_end"]) for t in tasks], [(0, 11), (11, 22)])

    def test_merge_rebases_reference_numbers_without_translating_direction(self):
        tasks = self.run_script([
            segment(0, 5, kind="broll", imageIds=["person-a", "person-b"],
                    story="@image1 turns left; @image2 stays."),
            segment(5, 10, kind="broll", imageIds=["person-b", "person-a"],
                    story="@image1 waves; image 2 smiles.")])["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["image_ids"], ["person-a", "person-b"])
        self.assertEqual(tasks[0]["story"], "@image1 turns left; @image2 stays.\n@image2 waves; image 1 smiles.")

    def test_keep_overrides_model_and_rewrite_preserves_it(self):
        for mode, expected in (("keep", ENGLISH["segments"][0]["text"]), ("rewrite", "Model rewrite!")):
            with self.subTest(mode=mode):
                script = self.run_script([segment(0, 5)], mode=mode)
                task = script["tasks"][0]
                self.assertEqual(task["dialogue"], expected)
                self.assertEqual(task["estimate"], round(evidence.estimate_seconds(expected, "en"), 2))
                self.assertEqual(script["dialogue"][0]["text"], expected)

    def test_rewrite_merge_concatenates_in_source_order(self):
        tasks = self.run_script([segment(5, 10, dialogue="Second."), segment(0, 5, dialogue="First.")], mode="rewrite")["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["dialogue"], "First.\nSecond.")

    def test_keep_majority_overlap_empty_range_and_original_punctuation(self):
        transcript = {"language": "en", "segments": [{"start": 0, "end": 4, "text": "Hello, world!", "words": [
            {"word": "Hello", "start": 0, "end": 2}, {"word": "world", "start": 2, "end": 4}]}]}
        for a, b, expected in [(0, 1, ""), (0, 1.1, "Hello,"), (1, 4, "world!"), (4, 5, "")]:
            with self.subTest(a=a, b=b):
                task = self.run_script([segment(a, b)], transcript)["tasks"][0]
                self.assertEqual(task["dialogue"], expected)
        empty = {"segments": [{"start": 0, "end": 5, "text": "Sentence without words", "words": []}]}
        self.assertEqual(self.run_script([segment(0, 5)], empty)["tasks"][0]["dialogue"], "")

    def test_duration_rounding_and_estimate_threshold(self):
        for span, est, expected in [(6.5, 3, 7), (8.4, 9.4, 8), (8.4, 9.5, 10), (2, 0, 4)]:
            with self.subTest(span=span, est=est), patch.object(evidence, "estimate_seconds", return_value=est):
                task = self.run_script([segment(0, span)])["tasks"][0]
                self.assertEqual(task["duration"], expected)

    def test_language_warning_runs_after_overview(self):
        store.update(self.pid, lambda d: d["source"].update(file="unused.mp4"))
        for obj, name, value in [(config, "load", dict(config.DEFAULTS)),
                                 (config, "provider_settings", {"model": "mock"}),
                                 (pipeline.media, "probe", {"duration": 10, "audio_stream_count": 1}),
                                 (pipeline.media, "extract_audio_wav", self.adir / "unused.wav"),
                                 (pipeline.media, "hypit_boundaries", []),
                                 (pipeline.media, "merge_boundaries", []),
                                 (pipeline, "_grids", []), (asr, "available", True),
                                 (asr, "transcribe_best", {"engine": "mock", **ENGLISH})]:
            self.enterContext(patch.object(obj, name, return_value=value))
        with patch.object(pipeline, "_clean_passages", side_effect=RuntimeError("stop after overview")):
            for language, count in [("zh", 1), ("English", 0), ("en-US", 0), (None, 0)]:
                with self.subTest(language=language), patch.object(store, "log") as log:
                    self.model.return_value = ({"overview": {"language": language}}, {})
                    with self.assertRaisesRegex(RuntimeError, "stop after overview"):
                        pipeline._run_analysis(self.pid, "jimmy")
                    self.assertEqual(log.call_count, count)
                    if count:
                        self.assertEqual(log.call_args.args[-1], "warn")
                        self.assertIn("不一致", log.call_args.args[1])

    def test_zero_length_word_is_owned_by_half_open_interval(self):
        transcript = {"language": "en", "segments": [{"start": 0, "end": 2, "text": "Hi!", "words": [
            {"word": "Hi", "start": 0, "end": 1}, {"word": "!", "start": 1, "end": 1}]}]}
        self.assertEqual(self.run_script([segment(0, 1)], transcript)["tasks"][0]["dialogue"], "Hi")
        self.assertEqual(self.run_script([segment(1, 2)], transcript)["tasks"][0]["dialogue"], "!")

    def test_rewrite_long_split_does_not_repeat_or_drop_words(self):
        text = "One two three four five six seven eight nine ten eleven twelve."
        tasks = self.run_script([segment(0, 32, dialogue=text)], mode="rewrite")["tasks"]
        self.assertEqual(" ".join(t["dialogue"] for t in tasks), text)

    def test_model_receives_shots_and_reference_ids(self):
        boundaries = {"merged_shots": [{"start": 0, "end": 5}]}
        self.run_script([segment(0, 5)], boundaries=boundaries)
        content = json.loads(self.model.call_args.args[1][1]["content"])
        self.assertEqual(content["merged_shots"], boundaries["merged_shots"])
        self.assertEqual([a["id"] for a in content["替换设定"]["可用参考图"]], ["person-a", "person-b"])


class AsrLanguageTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp")))
        self.wav = self.temp / "mock.wav"
        with wave.open(str(self.wav), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00\x00" * 160)
        self.model = Mock()
        self.factory = Mock(return_value=self.model)
        self.enterContext(patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=self.factory)}))
        self.enterContext(patch.object(asr, "_model", None))
        self.enterContext(patch.object(asr, "_model_key", None))

    def test_automatic_language_and_only_detected_chinese_gets_prompt(self):
        self.assertEqual(config.DEFAULTS["ASR_LANGUAGE"], "")
        for requested, detected in [("", "en"), (None, "zh"), ("zh", "en"), ("en", "zh")]:
            with self.subTest(requested=requested, detected=detected):
                self.model.reset_mock()
                self.model.transcribe.side_effect = lambda *a, **k: (iter([]), SimpleNamespace(language=detected))
                result = asr.transcribe(self.wav, language=requested)
                calls = self.model.transcribe.call_args_list
                self.assertEqual(calls[0].kwargs["language"], requested or None)
                self.assertFalse(calls[0].kwargs.get("initial_prompt"))
                self.assertEqual(result["language"], detected)
                self.assertEqual(len(calls), 2 if detected == "zh" else 1)
                if detected == "zh":
                    self.assertIn("普通话", calls[1].kwargs["initial_prompt"])
                    self.assertEqual(calls[1].kwargs["language"], "zh")

    def test_whisperx_uses_detected_language_and_missing_model_falls_back(self):
        wx = SimpleNamespace(load_audio=Mock(return_value="mock audio"),
                             load_align_model=Mock(return_value=("mock model", {})), align=Mock())
        nltk = SimpleNamespace(data=SimpleNamespace(find=Mock(), load=Mock()))
        transcript = {"engine": "mock", "language": "ja", "segments": []}
        with patch.dict(sys.modules, {"whisperx": wx, "whisperx.utils": SimpleNamespace(PUNKT_LANGUAGES={}),
                                      "nltk": nltk}), \
                patch.object(asr, "engines", return_value=["whisperx", "faster-whisper"]), \
                patch.object(asr.align_models, "check_align_model", return_value={
                    "language": "ja", "model": "offline-ja", "type": "huggingface", "status": "ok",
                    "path": "runtime/models/offline-ja", "download_url": "offline-ja"}), \
                patch.object(asr.align_models, "block_torch_downloads", side_effect=nullcontext), \
                patch.object(asr, "transcribe", return_value=transcript):
            result = asr.transcribe_best(self.wav, "medium", "int8", None)
            self.assertEqual(result["language"], "ja")
            wx.load_align_model.assert_called_once_with(language_code="ja", device="cpu", model_cache_only=True)
            wx.load_align_model.side_effect = ValueError("unsupported language")
            result = asr.transcribe_best(self.wav, "medium", "int8", None)
            self.assertEqual(result["language"], "ja")
            self.assertIn("unsupported language", result["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
