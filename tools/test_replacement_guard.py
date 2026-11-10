"""Round 8 offline regressions; models mocked, project evidence read-only."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge, llm, media, pipeline, store


def fixture():
    return {
        "routes": {"script": {"channel": "jimmy", "model": "mock-model"}},
        "assets": [{"id": aid, "type": "image", "label": aid, "roles": [role], "sha256": aid}
                   for aid, role in [("person", "person_identity"), ("camera", "target_state"),
                                     ("product", "product_truth"), ("other", "person_identity")]],
        "replacements": {"characters": [{"source_id": "C1", "action": "replace", "asset_id": "person",
                                            "camera_asset_id": "camera"}],
                         "product": {"action": "replace", "asset_id": "product"}},
        "breakdown": {"characters": [{"id": "C1", "appearance": "金色长发 blonde hair",
                                       "outfit": "黑色无袖高领连衣裙 black high-neck sleeveless dress with the front slit"}],
                      "overlays": [], "productMoments": [{"product": "黑色连衣裙"}]},
        "script": {"provider": "jimmy", "model": "mock-model", "tasks": []},
    }


def task(**kw):
    return {"id": "seg-01", "kind": "speaker", "mode": "video", "dialogue": "Hello!",
            "action": "The person from @image1 smiles.", "source_start": 0, "source_end": 3,
            "image_ids": ["camera", "product"], "duration": 4, "ratio": "9:16", "mute_clip": False,
            "blank_captions": True, **kw}


class ReplacementGuardTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(config, "_parse", side_effect=AssertionError("禁止读取配置")))
        # Gateway routing reads explicit offline settings; credential parsing stays forbidden.
        self.enterContext(patch.object(config, "load", return_value=dict(config.DEFAULTS)))
        self.enterContext(patch.object(llm, "chat_json", side_effect=AssertionError("必须显式 mock 模型")))

    def test_accident_keep_with_references_is_blocked_before_media(self):
        data = fixture()
        data["replacements"]["characters"][0]["action"] = "keep"
        data["replacements"]["product"]["action"] = "keep"
        with self.assertRaisesRegex(jimmy_bridge.BridgeError, "第 03 步人物处理是.*保持"):
            jimmy_bridge._check_kind(task(image_ids=["person", "product"]), data)

    def test_readonly_real_accident_project_is_rejected(self):
        # Round 9 permits reading only the copy. Recreate round-8 invalid settings in memory.
        directory = ROOT / "projects/p1027195039-ccc0"
        original = (directory / "project.json").read_bytes()
        data = json.loads((ROOT / "tools/fixtures/round13/incident-project.json").read_text())
        for c in data["replacements"]["characters"]:
            c["action"] = "keep"
        data["replacements"]["product"]["action"] = "keep"
        data["script"].pop("replacements_fingerprint", None)
        for overlay in data["breakdown"]["overlays"]:
            overlay.pop("bbox", None)
        t = data["script"]["tasks"][0]
        errors = jimmy_bridge.replacement_check(data, t, directory)["errors"]
        self.assertTrue(any("人物处理是‘保持’" in e for e in errors))
        self.assertTrue(any("商品处理" in e for e in errors))
        self.assertTrue(any("请重新生成新脚本" in e for e in errors))
        self.assertTrue(any("没有记录字幕位置" in e for e in errors))
        self.assertEqual(original, (directory / "project.json").read_bytes())

    def test_dialogue_quotes_are_exempt_but_warn(self):
        from console import replacement_guard as guard
        data = self.ready()
        for field in guard.TEXT_FIELDS:
            for left, right in [("'", "'"), ('"', '"'), ('“', '”'), ('‘', '’'), ('「', '」')]:
                with self.subTest(field=field, quote=left):
                    t = task(dialogue="The back, THE SLIT, yeah.", **{field: f"Smile on {left}the slit{right}."})
                    check = guard.check(data, t)
                    self.assertEqual(check["conflicts"], [])
                    self.assertEqual(check["errors"], [])
                    self.assertIn("提到原片的服装特征", " ".join(check["warnings"]))
                    self.assertIn("slit", " ".join(check["warnings"]))
                    self.assertIn("改写冲突台词", " ".join(check["warnings"]))
                    self.assertNotIn("改为 rewrite", " ".join(check["warnings"]))
        self.assertFalse(guard.conflicts(data, task(dialogue="I'm in the slit.", action="On 'I'm in the slit' turn.")))
        self.assertFalse(guard.conflicts(data, task(dialogue="THE, SLIT!", action='On “the-slit” turn.')))

    def test_unquoted_or_unmatched_appearance_still_blocks(self):
        from console import replacement_guard as guard
        for action in ["a black dress with a slit", "On 'blonde' turn.", "On 'the slit' show the slit."]:
            t = task(dialogue="The back, the slit, yeah.", action=action)
            self.assertTrue(guard.conflicts(self.ready(), t))
            with self.assertRaisesRegex(jimmy_bridge.BridgeError, "原片外观冲突"):
                jimmy_bridge._check_kind(t, self.ready())

    def test_dialogue_mismatches_bilingual_people_and_product(self):
        from console import replacement_guard as guard
        data = self.ready()
        for dialogue in ["The slit.", "开衩", "The BLACK dress."]:
            self.assertTrue(guard.dialogue_mismatches(data, task(dialogue=dialogue)))
        data["replacements"]["characters"][0]["action"] = "keep"
        self.assertTrue(guard.dialogue_mismatches(data, task(dialogue="black dress")))
        data["replacements"]["product"]["action"] = "keep"
        self.assertFalse(guard.dialogue_mismatches(data, task(dialogue="black dress")))

    def test_real_system_uses_only_three_captions_overlapping_the_cut(self):
        from console import replacement_guard as guard
        data = json.loads((ROOT / "tools/fixtures/round13/incident-project.json").read_text())
        masks, missing = guard.caption_masks(data, task(source_start=6, source_end=7))
        self.assertFalse(missing)
        self.assertEqual(len(masks), 3)
        for mask, overlay, box, times in zip(masks, data['breakdown']['overlays'][1:4],
                ([.36, .72, .65, .85], [.36, .72, .65, .85], [.4, .72, .61, .85]),
                ((0, .25), (0, 1), (.95, 1))):
            self.assertEqual(mask['system'], '底部英文口播字幕')
            self.assertEqual(mask['source_bboxes'], [overlay['bbox']])
            self.assertEqual(mask['bbox'], box)
            self.assertEqual(mask['padding'], {'x': .03, 'y': .04, 'time': .15})
            self.assertEqual((mask['start'], mask['end']), times)

    def test_different_positions_and_times_remain_separate_without_height_cap(self):
        from console import replacement_guard as guard
        data = self.ready()
        data["breakdown"]["overlays"] = [
            {"kind": "caption", "start": 1, "end": 2, "bbox": [.2, .1, .8, .2]},
            {"kind": "caption", "start": 2.5, "end": 3, "bbox": [.1, .7, .9, .8]},
            {"kind": "sticker", "start": 2, "end": 3, "bbox": [0, .9, 1, 1]},
            {"kind": "caption", "start": 4, "end": 5, "bbox": [.2, .1, .8, .2]}]
        masks, missing = guard.caption_masks(data, task(source_end=5))
        self.assertFalse(missing)
        captions = [m for m in masks if m["system"] == "caption"]
        self.assertEqual(len(captions), 3)
        self.assertEqual([(m['start'], m['end']) for m in captions], [(.85, 2.15), (2.35, 3.15), (3.85, 5)])
        self.assertEqual([m['bbox'] for m in captions], [[.17, .06, .83, .24], [.07, .66, .93, .84], [.17, .06, .83, .24]])
        self.assertTrue(all('height_limit_reason' not in m and 'motion_warning' not in m for m in masks))
        self.assertEqual(next(m['bbox'] for m in masks if m['kind'] == 'sticker'), [0, .86, 1, 1])

    def ready(self):
        from console import replacement_guard as guard
        data = fixture()
        data["script"]["replacements_fingerprint"] = guard.fingerprint(data)
        return data

    def test_fingerprint_old_changed_and_asset_bytes(self):
        from console import replacement_guard as guard
        data = self.ready()
        self.assertFalse(guard.check(data, task())["errors"])
        data["replacements"]["characters"][0]["description"] = "新要求"
        self.assertIn("替换设定在生成脚本之后改过", "".join(guard.check(data, task())["errors"]))
        data["script"].pop("replacements_fingerprint")
        self.assertIn("重新生成新脚本", "".join(guard.check(data, task())["errors"]))
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp") as tmp:
            path = Path(tmp) / "person.png"
            path.write_bytes(b"one")
            data["assets"][0]["file"] = "person.png"
            a = guard.fingerprint(data, Path(tmp))
            path.write_bytes(b"two")
            self.assertNotEqual(a, guard.fingerprint(data, Path(tmp)))

    def test_references_and_target_state(self):
        from console import replacement_guard as guard
        data = self.ready()
        for t, expected in [(task(image_ids=[]), "这一段还没选参考图"),
                            (task(image_ids=["product"]), "缺少替换人物"),
                            (task(image_ids=["person", "product"]), "需要先生成主镜头图"),
                            (task(image_ids=["other", "product"]), "不属于")]:
            self.assertIn(expected, "".join(guard.check(data, t)["errors"]))
        data["replacements"]["product"]["action"] = "keep"
        self.assertIn("商品处理", "".join(guard.check(data, task())["errors"]))

    def test_mapping_rewrite_and_keyword_fallback(self):
        from console import replacement_guard as guard
        data = self.ready()
        original = "Keep blonde hair and the black high-neck sleeveless dress with the front slit. Turn slowly."
        rewritten = "Keep the person from @image1 and the outfit from @image2. Turn slowly."
        t = task(action=original)
        script = {**data["script"], "tasks": [t]}
        with patch.object(llm, "chat_json", return_value=({"tasks": [{"id": t["id"], "action": rewritten,
                      "story": "", "direction": "", "changes": [{"field": "action", "before": original, "after": rewritten}]}]}, {})) as model:
            pipeline.apply_replacement_safety(data, script)
        self.assertEqual(t["action"], rewritten)
        self.assertEqual(t["replacement_safety"][0]["before"], original)
        self.assertEqual(t["replacement_safety"][0]["after"], rewritten)
        self.assertEqual(model.call_args.kwargs["model"], "mock-model")
        self.assertFalse(guard.conflicts(data, t))
        t["action"] = rewritten + " blonde"
        self.assertIn("blonde", " ".join(guard.check(data, t)["errors"]))
        t["action"] = "金发人物转身，黑色高领无袖裙。"
        self.assertTrue(guard.conflicts(data, t))

    def test_bbox_normalization_missing_and_mute(self):
        from console import replacement_guard as guard
        data = self.ready()
        data["breakdown"]["overlays"] = [{"kind": "caption", "start": 1, "end": 5}]
        self.assertIn("没有记录字幕位置", "".join(guard.check(data, task())["errors"]))
        self.assertFalse(guard.check(data, task(blank_captions=False))["errors"])
        data["breakdown"]["overlays"][0]["bbox"] = [.2, .6, .8, .85]
        check = guard.check(data, task(source_start=2, source_end=5))
        self.assertEqual(check["masks"][0]["start"], 0)
        self.assertEqual(check["masks"][0]["end"], 3)
        self.assertAlmostEqual(check["masks"][0]["bbox"][0], .17)
        self.assertTrue(check["force_mute"])
        self.assertTrue(check["mute_clip"])
        pack = pipeline.normalize_breakdown({"overlays": [{"bbox": [-.2, .1, 1.2, .9]},
                     {"bbox": [1, .1, .2, .9]}, {"bbox": [0, 0, float('nan'), 1]}]}, 5)
        self.assertEqual(pack["overlays"][0]["bbox"], [0, .1, 1, .9])
        self.assertIsNone(pack["overlays"][1].get("bbox"))
        self.assertIsNone(pack["overlays"][2].get("bbox"))

    def project(self):
        from console import replacement_guard as guard
        temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp")))
        self.enterContext(patch.object(config, "PROJECTS_DIR", temp))
        pid = store.create("第八轮离线检查")["id"]
        directory = store.project_dir(pid)
        data = fixture()
        for a in data["assets"]:
            a["file"] = "assets/" + a["id"] + ".png"
            (directory / a["file"]).write_bytes(a["id"].encode())
            a["sha256"] = store.sha256_file(directory / a["file"])
        data["source"] = {"file": "source/source.mp4", "probe": {"duration": 15, "ratio_hint": "9:16"}}
        (directory / data["source"]["file"]).write_bytes(b"offline source fixture")
        data["script"]["tasks"] = [task()]
        data["script"]["replacements_fingerprint"] = guard.fingerprint(data, directory)
        store.update(pid, lambda d: d.update(data))
        return pid, directory, store.load(pid)

    def test_manual_save_runs_safety_same_model_and_never_renews_fingerprint(self):
        from console import replacement_guard as guard
        pid, directory, data = self.project()
        original = "She has blonde hair. Smile gently."
        rewritten = "The person from @image1. Smile gently."
        script = copy.deepcopy(data["script"])
        script["tasks"][0]["action"] = original
        script["model"] = "forged-model"
        script["replacements_fingerprint"] = "forged-fingerprint"
        with patch.object(llm, "chat_json", return_value=({"tasks": [{"id": "seg-01", "action": rewritten,
                "story": "", "direction": "", "changes": [{"field": "action", "before": original, "after": rewritten}]}]}, {})) as model:
            saved = pipeline.save_script(pid, script)
            self.assertEqual(model.call_args.kwargs["model"], "mock-model")
            self.assertEqual(saved["script"]["replacements_fingerprint"], data["script"]["replacements_fingerprint"])
            self.assertTrue(saved["script"]["tasks"][0]["mute_clip"])
            pipeline.save_script(pid, copy.deepcopy(saved["script"]))
            self.assertEqual(model.call_count, 1, "未改文字/映射的保存应复用服务端检查")
        store.update(pid, lambda d: d["replacements"]["characters"][0].update(description="更换要求"))
        fresh = store.load(pid)
        self.assertIn(guard.STALE_SCRIPT, guard.check(fresh, saved["script"]["tasks"][0], directory)["errors"])

    def test_generation_stamps_and_rewrites(self):
        from console import replacement_guard as guard
        pid, directory, data = self.project()
        original = "The blonde person smiles."
        safe = "The person from @image1 smiles."
        with patch.object(llm, "chat_json", side_effect=[
            ({"segments": [{"kind": "motion", "sourceStart": 0, "sourceEnd": 5, "direction": original}]},
             {"model": "reported-version", "requested_model": "requested-model"}),
            ({"tasks": [{"id": "seg-01", "action": "", "story": "", "direction": safe,
                         "changes": [{"field": "direction", "before": original, "after": safe}]}]}, {}),
        ]) as model:
            pipeline.run_script(pid, "jimmy", "keep", "", None)
        saved = store.load(pid)
        self.assertEqual(saved["script"]["replacements_fingerprint"], guard.fingerprint(data, directory))
        self.assertEqual(saved["script"]["tasks"][0]["direction"], safe)
        self.assertEqual(saved["script"]["tasks"][0]["image_ids"][0], "camera")
        # Validation follows the explicit project route, not the response model provenance.
        self.assertEqual(model.call_args.kwargs["model"], data["routes"]["script"]["model"])

    def test_camera_product_references_and_prompt_order(self):
        pid, directory, _ = self.project()

        def frame(src, at, out, **kw):
            out.write_bytes(b"mock frame")
            return out

        with patch.object(media, "extract_frame", side_effect=frame), \
                patch.object(llm, "image_part", side_effect=lambda path, **kw: [{"type": "text", "text": path.name}]) as images, \
                patch.object(llm, "chat_json", return_value=({"person": "the person from reference image 1",
                          "shot": "wear the outfit from reference image 2", "setting": "reference image 3"}, {})):
            draft = pipeline.draft_camera_image(pid, "jimmy", "C1", 1)
        self.assertEqual(draft["image_ids"][:2], ["person", "product"])
        self.assertEqual([c.args[0].name for c in images.call_args_list], ["person.png", "product.png", "frame-C1-1.00.jpg"])
        self.assertIn("图1确定人物身份，图2确定服装/商品的真实外观，图3为原片参考帧", draft["prompt"])

    def test_refresh_checks_once_and_uses_run_replacements(self):
        pid, directory, data = self.project()
        result = directory / "jimmy-runs/job/results/video.mp4"
        result.parent.mkdir(parents=True)
        result.write_bytes(b"mock result")
        store.atomic_write_json(directory / "jimmy-runs/job/state.json", {"tasks": {"seg-01": {
            "phase": "completed", "local_path": str(result), "jimmy_task_id": "offline-id"}}})
        store.update(pid, lambda d: d["runs"].update({"seg-01": [{"rev": "r1", "state": "running",
                     "run_dir": "jimmy-runs/job", "person_replaced": True, "source_range": [2, 7]}]}))
        store.update(pid, lambda d: d["replacements"]["characters"][0].update(action="keep"))
        with patch.object(media, "copy_similarity", return_value={"copy_suspected": True, "similarity": .96}) as check:
            jimmy_bridge.refresh(pid, "seg-01", "r1")
            jimmy_bridge.refresh(pid, "seg-01", "r1")
        self.assertEqual(check.call_count, 1)
        self.assertEqual(check.call_args.args[2:], (2, 7))
        run = store.load(pid)["runs"]["seg-01"][0]
        self.assertTrue(run["copy_suspected"])
        self.assertEqual(run["state"], "completed")

    def test_readonly_endpoint_and_preview_use_same_rules(self):
        from console import runtime_env
        with patch.object(runtime_env, "apply"):
            import server
        pid, directory, data = self.project()
        body = {"tasks": [task(image_ids=["person", "product"])]}
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {'X-Client-Build': server.build_status()['build']}
        handler._body_json = lambda: body
        checks = handler._api("POST", ["projects", pid, "replacement-check"], {})
        self.assertEqual(checks["seg-01"], jimmy_bridge.replacement_check(data, body["tasks"][0], directory))
        self.assertEqual(store.load(pid), data)

    def test_preview_stops_before_creating_files_or_cutting(self):
        pid, directory, data = self.project()
        store.update(pid, lambda d: d["replacements"]["characters"][0].update(action="keep"))
        with patch.object(media, "cut_clip", side_effect=AssertionError("不应裁剪")), \
                patch.object(jimmy_bridge, "_call", side_effect=AssertionError("不应调用打包器")):
            with self.assertRaisesRegex(jimmy_bridge.BridgeError, "第 03 步人物处理"):
                jimmy_bridge.preview(pid, "seg-01")
        self.assertFalse(list((directory / "packages").iterdir()))

    def test_preview_residue_and_dialogue_warnings_never_block(self):
        pid, directory, data = self.project()
        store.update(pid, lambda d: d["script"]["tasks"][0].update(
            dialogue="The back, the slit, yeah.", action="Smile on 'the slit'."))

        def cut(src, start, length, out, **kwargs):
            out.write_bytes(b"mock processed clip")

        def package(args, cwd):
            store.atomic_write_json(cwd / "review/jimmy-job.preview.json", {
                "tasks": [{"id": "seg-01", "model": jimmy_bridge.MINI_VIDEO}]})
            store.atomic_write_json(cwd / "review/jimmy-handoff.json", {"job_sha256": "mock"})

        def residue(clip, source, masks, overlays, outdir, **kwargs):
            thumb = outdir / "suspect.jpg"
            thumb.parent.mkdir(parents=True)
            thumb.write_bytes(b"mock thumbnail")
            sample = {"time": 1, "source_time": 1, "score": .02, "thumbnail": str(thumb)}
            return {"status": "checked", "samples": [sample], "suspected": [sample]}

        for outcome in [residue, media.MediaError("mock failure")]:
            with self.subTest(outcome=str(outcome)), patch.object(media, "cut_clip", side_effect=cut), \
                    patch.object(media, "probe", return_value={"duration": 3}), \
                    patch.object(media, "caption_residue", side_effect=outcome), \
                    patch.object(jimmy_bridge, "_call", side_effect=package):
                record = jimmy_bridge.preview(pid, "seg-01")
            self.assertEqual(record["state"], "pending_confirmation")
            self.assertIn("提到原片的服装特征", " ".join(record["warnings"]))
            result = record["caption_residue"]
            if callable(outcome):
                thumbnail = result["suspected"][0]["thumbnail"]
                self.assertFalse(Path(thumbnail).is_absolute())
                self.assertTrue((directory / thumbnail).is_file())
                self.assertEqual(store.load(pid)["runs"]["seg-01"][-1]["caption_residue"], result)
            else:
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("人工检查", result["reason"])

    def test_missing_safety_audit_cannot_silently_change_actions(self):
        data = self.ready()
        script = {**data["script"], "tasks": [task()]}
        with patch.object(llm, "chat_json", return_value=({"tasks": [{"id": "seg-01", "action": "Jump instead.",
                "story": "", "direction": "", "changes": []}]}, {})):
            with self.assertLogs('console.pipeline', level='WARNING') as logs:
                pipeline.apply_replacement_safety(data, script)
            self.assertIn("丢弃了清单外的改动", logs.output[0])
        self.assertEqual(script["tasks"][0]["action"], task()["action"])

    def test_leaked_keyword_after_mock_rewrite_blocks_preview(self):
        data = self.ready()
        script = {**data["script"], "tasks": [task(action="The blonde person turns.")]}
        with patch.object(llm, "chat_json", return_value=({"tasks": [{"id": "seg-01", "action": "The blonde person turns.",
                "story": "", "direction": "", "changes": []}]}, {})):
            pipeline.apply_replacement_safety(data, script)
        with self.assertRaisesRegex(jimmy_bridge.BridgeError, "blonde"):
            jimmy_bridge._check_kind(script["tasks"][0], data)

    def test_kept_people_and_user_explicit_appearance(self):
        from console import replacement_guard as guard
        data = fixture()
        data["replacements"]["characters"][0]["description"] = "保留金发"
        self.assertFalse(guard.conflicts(data, task(action="Keep blonde hair.")))
        data["replacements"]["characters"][0]["action"] = "keep"
        data["replacements"]["product"]["action"] = "keep"
        self.assertFalse(guard.check(data, task())["force_mute"])
        data["replacements"]["characters"][0]["action"] = "replace"
        data["assets"].append({"id": "voice", "type": "audio"})
        self.assertFalse(guard.check(data, task(audio_id="voice", voice_approved=True))["force_mute"])
        self.assertTrue(guard.check(data, task(audio_id="missing", voice_approved=True))["force_mute"])

    def test_manual_reference_reorder_rebases_only_unchanged_text(self):
        old = task(image_ids=["camera", "product"], action="The person from @image1 holds the product from @image2.")
        changed = {**old, "image_ids": ["product", "camera"]}
        pipeline.rebase_task_references(changed, old)
        self.assertEqual(changed["action"], "The person from @image2 holds the product from @image1.")
        self.assertEqual(changed["replacement_safety"][0]["before"], old["action"])
        authored = {**old, "image_ids": ["product", "camera"], "action": "The person from @image2 waves."}
        pipeline.rebase_task_references(authored, old)
        self.assertEqual(authored["action"], "The person from @image2 waves.")

    def test_removed_reference_remains_blocked_after_other_references_rebase(self):
        from console import replacement_guard as guard
        data = self.ready()
        old = task(image_ids=["other", "camera", "product"],
                   action="The person from @image2 turns toward @image1 while holding @image3.")
        changed = {**old, "image_ids": ["camera", "product"]}
        pipeline.rebase_task_references(changed, old)
        self.assertIn("已移除", " ".join(guard.check(data, changed)["errors"]))

    def test_rebase_does_not_mutate_previous_error_snapshot(self):
        old = task(image_ids=["camera", "product"], action="The person from @image1 holds @image2.")
        old["reference_mapping_errors"] = [{"field": "action", "text": old["action"], "message": "已移除"}]
        snapshot = copy.deepcopy(old)
        changed = {**old, "image_ids": ["product", "camera"]}
        pipeline.rebase_task_references(changed, old)
        self.assertEqual(old, snapshot)

    def test_confirmation_holds_project_lock_through_freezing(self):
        pid, directory, data = self.project()
        t = data["script"]["tasks"][0]
        review = directory / "packages/seg-01-r1/review"
        store.atomic_write_json(review / "jimmy-job.preview.json", {"tasks": [{"id": "seg-01"}]})
        sha = store.sha256_file(review / "jimmy-job.preview.json")
        store.atomic_write_json(review / "jimmy-handoff.json", {"state": "ready_for_confirmation"})
        store.update(pid, lambda d: d["runs"].update({"seg-01": [{"rev": "r1", "state": "pending_confirmation",
             "package": "packages/seg-01-r1", "job_sha256": sha, "job_id": "offline-job",
             "model": "mock", "billing_seconds": 4,
             "preflight_fingerprint": jimmy_bridge.preflight_fingerprint(data, t, directory)}]}))
        entered, changed = threading.Event(), threading.Event()
        workers = []

        def verify(*args, **kw):
            def edit():
                entered.set()
                store.update(pid, lambda d: d["replacements"].update(scene="changed"))
                changed.set()
            worker = threading.Thread(target=edit)
            workers.append(worker)
            worker.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(changed.wait(.05), "确认冻结期间不能插入替换设置修改")

        try:
            with patch.object(jimmy_bridge, "_call", side_effect=verify), \
                    patch.object(config, "jimmy_configured", return_value=True), \
                    patch.object(jimmy_bridge, "_start_generate"):
                jimmy_bridge.confirm(pid, "seg-01", "r1", sha)
        finally:
            for worker in workers:
                worker.join(2)
        self.assertTrue(changed.is_set())

    def test_preview_provenance_rejects_later_task_changes(self):
        pid, directory, data = self.project()
        t = data["script"]["tasks"][0]
        record = {"preflight_fingerprint": jimmy_bridge.preflight_fingerprint(data, t, directory)}
        jimmy_bridge.verify_preview_current(data, t, record, directory)
        t["blank_captions"] = False
        with self.assertRaisesRegex(jimmy_bridge.BridgeError, "预览.*变化"):
            jimmy_bridge.verify_preview_current(data, t, record, directory)

    def test_product_target_state_is_not_mistaken_for_a_person(self):
        from console import replacement_guard as guard
        data = fixture()
        data["assets"][2]["roles"] = ["target_state"]
        data["replacements"]["characters"][0]["action"] = "keep"
        data["script"]["replacements_fingerprint"] = guard.fingerprint(data)
        self.assertFalse(guard.check(data, task(kind="broll", mode="image", image_ids=["product"], action=""))["errors"])

    def test_missing_product_reference_blocks_unbound_camera(self):
        from console import replacement_guard as guard
        data = self.ready()
        errors = guard.check(data, task(image_ids=["camera"]))["errors"]
        self.assertIn("缺少替换商品", " ".join(errors))

    def test_camera_binding_goes_stale_when_person_or_product_changes(self):
        from console import replacement_guard as guard
        data = fixture()
        data["assets"][1]["camera_binding"] = guard.camera_binding(data, "C1")
        data["assets"][1]["camera_character"] = "C1"
        for section in ("person", "product"):
            changed = copy.deepcopy(data)
            if section == "person":
                changed["replacements"]["characters"][0]["asset_id"] = "other"
            else:
                changed["replacements"]["product"]["description"] = "新商品要求"
            changed["script"]["replacements_fingerprint"] = guard.fingerprint(changed)
            self.assertIn("主镜头图的替换依据已变化", " ".join(guard.check(changed, task())["errors"]))
        data["images"] = {"old": {"for_task": "camera:C1", "state": "completed", "asset_id": "camera"}}
        data["replacements"]["characters"][0]["camera_asset_id"] = None
        # Empty selections now fall back to a valid completed image. Invalidation
        # must be represented by the changed replacement binding itself.
        data["replacements"]["product"]["description"] = "新的商品要求"
        self.assertNotIn("C1", guard.camera_images(data))

    def test_saving_changed_identity_clears_old_camera(self):
        pid, directory, data = self.project()
        rep = copy.deepcopy(data["replacements"])
        rep["characters"][0]["asset_id"] = "other"
        saved = pipeline.save_replacements(pid, rep)
        self.assertIsNone(saved["replacements"]["characters"][0]["camera_asset_id"])


class ReplacementMediaTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"PATH": str(ROOT / "runtime/ffmpeg") + os.pathsep + os.environ.get("PATH", "")}))
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp")))
        self.source = ROOT / "projects/p1027195039-ccc0/source/source.mp4"

    def test_real_caption_residue_old_and_new(self):
        from console import replacement_guard as guard
        data = json.loads((ROOT / "tools/fixtures/round13/incident-project.json").read_text())
        overlays = data["breakdown"]["overlays"]
        old = []
        for overlay in overlays:
            x0, y0, x1, y1 = overlay["bbox"]
            old.append({**overlay, "bbox": [max(0, x0 - .02), max(0, y0 - .02), min(1, x1 + .02), min(1, y1 + .02)],
                        "end": min(15, overlay["end"])})
        new, missing = guard.caption_masks(data, task(source_end=15))
        self.assertFalse(missing)
        times = [2.5, 6.5, 9.0, 12.0, 14.3]
        for name, masks, expected in [("old", old, [False, False, True, False, True]),
                                      ("new", new, [False, False, False, False, True])]:
            clip = self.temp / f"{name}.mp4"
            media.cut_clip(self.source, 0, 15, clip, mute=True, masks=masks)
            result = media.caption_residue(clip, self.source, masks, overlays, self.temp / name, sample_times=times)
            self.assertEqual(result["status"], "checked")
            selected = [next(s for s in result["samples"] if s["time"] == t) for t in times]
            print("第九轮字幕残留 " + name + "：" + json.dumps(selected, ensure_ascii=False))
            self.assertEqual([s["suspected"] for s in selected], expected)
            self.assertEqual(len(result["baseline_times"]), 3)
            self.assertTrue(all(0 < t < 1 for t in result["baseline_times"]))
            for suspect in result["suspected"]:
                self.assertTrue(Path(suspect["thumbnail"]).is_file())
                self.assertGreater(suspect["score"], result["threshold"])
            if name == "new":
                # The recorded last bbox ends at .73. Per-overlay removal reaches
                # .77 rather than the old union band's .85; preserve the advisory
                # detector's real 14.3s residue instead of enlarging all masks.
                self.assertEqual(len(new), 7)
                self.assertEqual(new[-1]['bbox'], [.24, .65, .76, .77])
                self.assertTrue(any(s['time'] == 14.3 for s in result['suspected']))

    def test_caption_residue_empty_or_no_baseline_is_explicit(self):
        self.assertTrue(callable(getattr(media, "caption_residue", None)), "缺少本地字幕残留检查")
        with patch.object(media, "small_frame", side_effect=AssertionError("不应取帧")):
            result = media.caption_residue(self.source, self.source, [], [], self.temp)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("没有字幕", result["reason"])
        self.assertEqual(result["suspected"], [])
        full = [{"kind": "caption", "start": 0, "end": 16, "bbox": [.1, .7, .9, .8]}]
        result = media.caption_residue(self.source, self.source, full, full, self.temp)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("无字幕基准", result["reason"])

    def test_caption_residue_uses_source_clock_outside_clip(self):
        self.assertTrue(callable(getattr(media, "caption_residue", None)), "缺少本地字幕残留检查")
        full = [{"kind": "caption", "start": 5, "end": 8, "bbox": [.1, .7, .9, .8]}]
        masks = [{**full[0], "start": 0, "end": 3}]
        with patch.object(media, "probe", side_effect=[{"duration": 3, "width": 360, "height": 640},
                                                      {"duration": 10}]), \
                patch.object(media, "small_frame", return_value=bytes(360 * 640 * 3)):
            result = media.caption_residue(self.temp / "clip.mp4", self.source, masks, full, self.temp, source_start=5)
        self.assertEqual(len(result["samples"]), 3)
        self.assertTrue(all(s["source_time"] == s["time"] + 5 for s in result["samples"]))
        self.assertTrue(all(t < 5 for t in result["baseline_times"]))

    def test_real_caption_mask_and_audio_removed(self):
        import numpy as np
        out, plain = self.temp / "masked.mp4", self.temp / "plain.mp4"
        media.cut_clip(self.source, 1, 3, out, mute=True,
                       masks=[{"bbox": [.2, .65, .8, .82], "start": .5, "end": 2.5}])
        media.cut_clip(self.source, 1, 3, plain, mute=False)
        info = media.probe(out)
        self.assertTrue(info["has_video"])
        self.assertEqual(info["audio_stream_count"], 0)
        self.assertGreater(media.probe(plain)["audio_stream_count"], 0)
        self.assertAlmostEqual(info["duration"], 3, delta=.15)

        def frame(path, t):
            return np.frombuffer(media.small_frame(path, t, width=180, height=320), dtype=np.uint8).astype(float).reshape(320, 180, 3)

        diff = abs(frame(out, 1) - frame(plain, 1)).mean(axis=2)
        inside = diff[210:260, 38:142].mean()
        outside = diff[:190, :].mean()
        inactive = abs(frame(out, .1) - frame(plain, .1)).mean()
        print(f"字幕处理像素差：区域内={inside:.4f}，区域外={outside:.4f}，未生效时={inactive:.4f}")
        self.assertGreater(inside, 3)
        self.assertLess(outside, 2)
        self.assertLess(inactive, 2)

    def test_real_accident_similarity_and_negative(self):
        # Keep the real near-copy positive control without reading the forbidden accident project.
        near_copy = self.temp / "near-copy.mp4"
        media.cut_clip(self.source, 0, 15, near_copy, mute=True)
        result = media.copy_similarity(near_copy, self.source, 0, 15)
        different = self.temp / "different.mp4"
        media._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=180x320:r=24:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(different)])
        negative = media.copy_similarity(different, self.source, 0, 15)
        print("事故相似度：" + json.dumps(result, ensure_ascii=False))
        print("负例相似度：" + json.dumps(negative, ensure_ascii=False))
        self.assertTrue(result["copy_suspected"])
        self.assertFalse(negative["copy_suspected"])
        self.assertEqual(len(result["frame_similarities"]), 8)

    def test_preview_uploads_processed_bytes_and_records_treatment(self):
        from console import replacement_guard as guard
        self.enterContext(patch.object(config, "PROJECTS_DIR", self.temp))
        pid = store.create("processed preview offline")["id"]
        directory = store.project_dir(pid)
        data = fixture()
        for a in data["assets"]:
            a["file"] = "assets/" + a["id"] + ".png"
            (directory / a["file"]).write_bytes(b"mock image")
            a["sha256"] = store.sha256_file(directory / a["file"])
        data["source"] = {"file": str(self.source), "probe": media.probe(self.source)}
        data["breakdown"]["overlays"] = [{"kind": "caption", "start": 1.5, "end": 3.5,
                                           "bbox": [.22, .67, .78, .8], "text": "test"}]
        data["script"]["tasks"] = [task(source_start=1, source_end=4)]
        data["script"]["replacements_fingerprint"] = guard.fingerprint(data, directory)
        store.update(pid, lambda d: d.update(data))
        observed = {}

        def package(args, cwd, **kw):
            self.assertEqual(args[1], "prepare")
            plan = json.loads((cwd / "plan.json").read_text())
            observed.update(plan=plan)
            clip = next(m for m in plan["media"] if m["type"] == "video")
            self.assertEqual(clip["sha256"], store.sha256_file(Path(clip["path"])))
            preview = {"tasks": [{"id": "seg-01", "model": jimmy_bridge.MINI_VIDEO}]}
            store.atomic_write_json(cwd / "review/jimmy-job.preview.json", preview)
            store.atomic_write_json(cwd / "review/jimmy-handoff.json", {"job_sha256": "offline-hash"})
            return ""

        with patch.object(jimmy_bridge, "_call", side_effect=package), \
                patch.object(config, "_parse", side_effect=AssertionError("禁止配置读取")):
            record = jimmy_bridge.preview(pid, "seg-01")
        processed = directory / record["clip"]
        self.assertEqual(media.probe(processed)["audio_stream_count"], 0)
        self.assertTrue(record["clip_processing"]["muted"])
        self.assertEqual(record["clip_processing"]["masks"][0]["start"], .35)
        self.assertEqual(record["clip_processing"]["masks"][0]["end"], 2.65)
        self.assertIn("caption_residue", record)
        self.assertTrue(record["person_replaced"])
        self.assertEqual(observed["plan"]["tasks"][0]["reference_video_ids"], ["source-clip"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
