"""Round 11: real incident evidence is read-only; remote model responses are mocked."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge, jobs, llm, media, motion, replacement_guard as guard, store

INCIDENT = ROOT / "projects/p1027195039-ccc0"


def incident():
    data = json.loads((ROOT / "tools/fixtures/round13/incident-project.json").read_text())
    # Route preference is explicit in the copied fixture; provenance alone no longer selects a channel.
    data["routes"] = {"script": {"channel": data["script"]["provider"],
                                "model": data["script"].get("request_model") or data["script"]["model"]}}
    task = data["script"]["tasks"][0]
    task["action"] = json.loads((ROOT / "tools/fixtures/round13/incident-action.json").read_text())["action"]
    return data, task


class MotionFidelityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, "_parse", side_effect=AssertionError("禁止读取配置")))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, "chat_json", side_effect=AssertionError("必须 mock 模型")))
        read_transcript = motion.read_transcript
        self.enterContext(patch.object(motion, "read_transcript", side_effect=lambda directory:
            json.loads((ROOT / "tools/fixtures/round13/incident-transcript.json").read_text())
            if directory == INCIDENT else read_transcript(directory)))

    def test_real_action_anchor_order(self):
        data, task = incident()
        errors = guard.check(data, task)["errors"]
        self.assertTrue(any("‘yeah’ 写在 ‘the slit’ 前面" in e for e in errors), errors)

    def test_model_additions_and_failure_warning(self):
        data, task = incident()
        quote = "keep walking in until the framing tightens to a mid-shot"
        answer = {"additions": [{"field": "action", "quote": quote, "reason": "额外走近到 mid-shot"}]}
        with patch.object(llm, "chat_json", return_value=(answer, {})) as call:
            result = guard.check(data, task, compare_motion=True)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0], data["script"]["provider"])
        self.assertEqual(call.call_args.kwargs["model"], data["script"]["request_model"])
        self.assertTrue(any(quote in e and "原片" in e for e in result["errors"]))
        with patch.object(llm, "chat_json", side_effect=RuntimeError("mock unavailable")):
            failed = guard.check(data, task, compare_motion=True)
        self.assertTrue(any("动作对比" in w and "mock unavailable" in w for w in failed["warnings"]))
        self.assertEqual(failed["errors"], guard.check(data, task)["errors"])

    def test_timeline_sort_offset_and_mapping(self):
        from console import motion
        data, task = incident()
        data["breakdown"]["shots"].reverse()
        task.update(source_start=4.7, source_end=9.5)
        rows = motion.source_timeline(data, task)
        self.assertEqual([(r["start"], r["end"]) for r in rows], [(0, 1.45), (1.45, 3.4), (3.4, 3.95), (3.95, 4.8)])
        block = motion.timeline_block(rows)
        self.assertIn("[1.45–3.4 s]", block)
        self.assertIn("the outfit from @image2", block)
        for old in ("金发", "黑色礼服", "开衩", "裙身"):
            self.assertNotIn(old, block)
        self.assertIn("the slit", block)  # dialogue anchors remain verbatim
        self.assertIn("Perform every movement once", block)

    def test_timeline_preserves_durations_and_kept_outfit(self):
        from console import motion
        data = {"assets": [{"id": "bag"}], "replacements": {"product": {"action": "replace", "asset_id": "bag"}},
                "breakdown": {"productMoments": [{"product": "红色手提包"}], "shots": [
                    {"start": 5, "end": 9, "action": "金发人物穿黑色长裙，手沿裙身下滑，拎起红色手提包，在开衩附近停顿2秒。转身耗时约2秒，转身用2–3秒。"}]}}
        task = {"mode": "video", "source_start": 5, "source_end": 9, "image_ids": ["bag"]}
        text = motion.source_timeline(data, task)[0]["action"]
        self.assertIn("停顿2秒", text)
        self.assertIn("耗时约2秒", text)
        self.assertIn("用2–3秒", text)
        self.assertIn("黑色长裙", text)
        self.assertIn("裙身", text)
        self.assertIn("开衩附近", text)
        self.assertIn("the product from @image1", text)
        self.assertNotIn("红色手提包", text)

    def test_hair_appearance_mapping_preserves_motion_object(self):
        from console import motion
        data, task = incident()
        data["breakdown"]["characters"][0]["appearance"] = "金发"
        data["breakdown"]["shots"] = [{"start": 0, "end": 1, "action": "抬手撩金发。"}]
        self.assertIn("撩头发", motion.source_timeline(data, task)[0]["action"])

    def test_duration_prefix_is_not_an_absolute_timestamp(self):
        from console import motion
        data, task = incident()
        data["breakdown"]["shots"] = [{"start": 5, "end": 9, "action": "2秒后转身；约2秒内完成转身。"}]
        task.update(source_start=5, source_end=9)
        self.assertEqual(motion.source_timeline(data, task)[0]["action"], "2秒后转身；约2秒内完成转身。")

    def test_anchor_order_across_fields(self):
        from console import motion
        task = {"mode": "video", "dialogue": "The back, the slit, yeah.", "action": "On 'yeah' smile.",
                "story": "On 'the slit' use the recorded movement."}
        self.assertTrue(any("‘yeah’ 写在 ‘the slit’ 前面" in e for e in motion.anchor_errors(task)))

    def test_video_keep_windows_and_no_generic_recipe(self):
        data, task = incident()
        prompt = jimmy_bridge.compose_prompt(data, task, INCIDENT)
        self.assertIn("@image1 defines the speaker's identity, face, outfit and the look of the setting", prompt)
        self.assertIn("SOURCE MOTION TIMELINE", prompt)
        self.assertIn("[5.07–7.608] The back, the slit, yeah.", prompt)
        self.assertIn("Speak each line inside its time window", prompt)
        for label in ("GESTURE:", "PERFORMANCE:", "COMPOSITION:", "CAMERA:", "EDIT RHYTHM:"):
            self.assertNotIn(label, prompt)
        self.assertIn("MOTION: body motion", prompt)
        self.assertIn("FRAMING: the camera matches @video1", prompt)

    def test_image_prompts_equal_unmodified_baseline(self):
        cases = json.loads((ROOT / "tools/fixtures/round11/image-prompts.json").read_text())
        for case in cases:
            with self.subTest(kind=case["task"]["kind"], recipe=case["task"]["recipe"]):
                self.assertEqual(jimmy_bridge.compose_prompt(case["data"], case["task"]), case["prompt"])

    def test_preview_fingerprint_includes_motion_mapping_and_dialogue_mode(self):
        data, task = incident()
        before = jimmy_bridge.preflight_fingerprint(data, task, INCIDENT)
        data["script"]["dialogue_mode"] = "rewrite"
        self.assertNotEqual(before, jimmy_bridge.preflight_fingerprint(data, task, INCIDENT))
        data["script"]["dialogue_mode"] = "keep"
        data["breakdown"]["characters"][0]["appearance"] = "新的拆解证据"
        self.assertNotEqual(before, jimmy_bridge.preflight_fingerprint(data, task, INCIDENT))

    def test_real_accident_and_self_alignment(self):
        from tools.test_motion_alignment import real_sample_checks
        reports = real_sample_checks()
        self.assertEqual(reports["incident"]["status"], "misaligned")
        self.assertEqual(reports["self"]["status"], "aligned")
        self.assertAlmostEqual(reports["incident"]["speech_lags"][1]["lag_seconds"], -1.03, delta=.15)

    def test_rewrite_windows_count_and_clipping(self):
        from console import motion
        data, task = incident()
        data["script"]["dialogue_mode"] = "rewrite"
        task.update(source_start=4.7, source_end=10, dialogue="It looks good. I like it.")
        rows, warnings = motion.dialogue_windows(data, task, INCIDENT)
        self.assertEqual([(r["start"], r["text"]) for r in rows], [(.37, "It looks good."), (3.57, "I like it.")])
        self.assertFalse(warnings)
        task["dialogue"] = "Hello\nGoodbye"
        rows, warnings = motion.dialogue_windows(data, task, INCIDENT)
        self.assertEqual([r["text"] for r in rows], ["Hello", "Goodbye"])
        self.assertFalse(warnings)
        task["dialogue"] = "One line."
        rows, warnings = motion.dialogue_windows(data, task, INCIDENT)
        self.assertEqual(rows, [])
        self.assertIn("改写后的台词没有原片时间对照", warnings[0])

    def test_caption_occlusion_percentages(self):
        data, task = incident()
        result = guard.check(data, task)
        self.assertTrue(result["masks"])
        for mask in result["masks"]:
            self.assertNotIn('motion_warning', mask, '这些独立字幕都不是大框，不应提示遮挡腿部')
        data['breakdown']['overlays'] = [{'kind': 'caption', 'start': 1, 'end': 2, 'bbox': [0, .4, 1, .9]}]
        mask = guard.check(data, task)['masks'][0]
        self.assertEqual(mask['bbox'], [0, .36, 1, .94])
        self.assertEqual(mask['motion_warning'], '这段字幕很大（覆盖画面高度 36–94%），这段时间里下半身动作的参考会变弱')

    def test_refresh_schedules_once_without_running_media_in_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp") as temp, patch.object(config, "PROJECTS_DIR", Path(temp)):
            pid = store.create("后台对齐检查测试")["id"]
            directory = store.project_dir(pid)
            source = directory / "source/source.mp4"
            source.write_bytes(b"mock source")
            result = directory / "result.mp4"
            result.write_bytes(b"mock output")
            task = {"id": "seg-01", "mode": "video", "source_start": 99, "source_end": 100}
            run = {"rev": "r1", "state": "running", "mode": "video", "run_dir": "jimmy-runs/job",
                   "source_range": [2, 7], "copy_check": {"checked": True},
                   "motion_source": {"file": "source/source.mp4", "sha256": store.sha256_file(source),
                                     "transcript": {"segments": []}}}
            store.update(pid, lambda d: d.update(source={"file": "source/source.mp4"}, script={"tasks": [task]},
                                                runs={"seg-01": [run]}))
            callbacks = []
            def start(p, key, callback, label):
                callbacks.append(callback)
                return {"state": "running"}
            with patch.object(jimmy_bridge, "_video_child", return_value={"phase": "completed", "local_path": str(result)}), \
                    patch.object(jobs, "start", side_effect=start) as enqueue, \
                    patch.object(jobs, "snapshot", return_value={"motion:seg-01:r1": {"state": "running"}}), \
                    patch.object(media, "motion_alignment", return_value={"status": "misaligned", "warnings": ["提前"]}) as calculate:
                jimmy_bridge.refresh(pid, "seg-01", "r1")
                jimmy_bridge.refresh(pid, "seg-01", "r1")
                calculate.assert_not_called()
                self.assertEqual(enqueue.call_count, 1)
                self.assertEqual(store.load(pid)["runs"]["seg-01"][0]["motion_check"]["status"], "running")
                callbacks[0]()
                self.assertEqual(calculate.call_args.args[2:4], (2.0, 7.0))
                self.assertEqual(store.load(pid)["runs"]["seg-01"][0]["motion_check"]["status"], "misaligned")
                jimmy_bridge.refresh(pid, "seg-01", "r1")
                self.assertEqual(enqueue.call_count, 1)


if __name__ == "__main__":
    unittest.main()
