"""Offline upload recovery tests. All CLI calls and waits are mocked; no live service is used."""
import copy
import json
import socket
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from console import config, jimmy_bridge as bridge, jobs, runtime_env, store

with patch.object(runtime_env, "apply"):
    import server


SAFE = {"phase": "failed", "request": None, "jimmy_task_id": None,
        "error": {"message": "upload transport failed\nsecond line"}}
UNSAFE = [
    {**SAFE, "phase": "create_started"},
    {**SAFE, "phase": "submission_outcome_unknown"},
    {**SAFE, "jimmy_task_id": "remote-id"},
    {**SAFE, "request": {"prompt": "already prepared"}},
    {**SAFE, "request": {}},
    {**SAFE, "phase": "submitted"},
    {**SAFE, "phase": "running", "jimmy_task_id": "poll-failed"},
]
DONE = {"phase": "completed", "request": {"prompt": "approved"}, "jimmy_task_id": "remote-id"}


class UploadRetryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="upload-test-", dir=ROOT / "runtime/temp")))
        self.enterContext(patch.object(config, "PROJECTS_DIR", self.temp))
        self.enterContext(patch.object(config, "jimmy_configured", return_value=True))
        self.enterContext(patch.object(config, "jimmy_env_file", return_value=self.temp / "mock.env"))
        self.enterContext(patch.object(config, "_parse", side_effect=AssertionError("不得读取配置")))
        self.enterContext(patch("subprocess.run", side_effect=AssertionError("不得启动真实 CLI")))
        self.enterContext(patch.object(socket, "create_connection", side_effect=AssertionError("不得联网")))
        self.sleep = self.enterContext(patch.object(bridge.time, "sleep"))
        self.cli = self.enterContext(patch.object(bridge, "_call"))
        # Round-8 replacement preflight has its own suite; this suite isolates upload recovery.
        self.enterContext(patch.object(bridge, "verify_preview_current"))
        self.queue = []
        self.active = {}
        self.enterContext(patch.object(jobs, "snapshot", side_effect=lambda pid: dict(self.active)))
        self.start = self.enterContext(patch.object(jobs, "start", side_effect=self.enqueue))
        self.pid = store.create("offline retry test")["id"]
        self.d = store.project_dir(self.pid)
        self.tid, self.rev = "t1", "r1"
        self.job_id = f"{self.pid}-t1-r1"
        self.review = self.d / "packages/t1-r1/review"
        self.review.mkdir(parents=True)
        self.raw = json.dumps({"job_id": self.job_id, "tasks": [{"id": self.tid}]}, indent=2).encode()
        (self.review / "jimmy-job.preview.json").write_bytes(self.raw)
        self.frozen = self.review / "jimmy-job.json"
        self.frozen.write_bytes(self.raw)
        self.sha = store.sha256_file(self.frozen)
        store.atomic_write_json(self.review / "jimmy-handoff.json", {"state": "ready_for_confirmation"})
        self.run_dir = self.d / "jimmy-runs" / self.job_id
        store.update(self.pid, lambda x: x["runs"].update({self.tid: [{
            "rev": self.rev, "job_id": self.job_id, "state": "pending_confirmation",
            "package": "packages/t1-r1", "job_sha256": self.sha, "model": "mock", "billing_seconds": 5,
            "run_dir": store.rel(self.pid, self.run_dir)}]}))

    def enqueue(self, pid, key, fn, label):
        if self.active.get(key, {}).get("state") == "running":
            raise RuntimeError("already running")
        self.active[key] = {"state": "running"}
        self.queue.append((key, fn))
        return self.active[key]

    def drain(self):
        errors = []
        while self.queue:
            key, fn = self.queue.pop(0)
            try:
                fn()
            except Exception as exc:
                errors.append(exc)
            finally:
                self.active[key] = {"state": "done"}
        return errors

    def record(self):
        return bridge._find_run(store.load(self.pid), self.tid, self.rev)

    def record_update(self, **values):
        store.update(self.pid, lambda x: bridge._find_run(x, self.tid, self.rev).update(values))

    def write_state(self, child):
        # A synthetic CLI write belongs to a job admitted in this process.
        # Cold history is intentionally passive in V3 (covered by test_v3_chain5).
        self.active.setdefault(f"gen:{self.tid}:{self.rev}", {"state": "done"})
        store.atomic_write_json(self.run_dir / "state.json", {"job_id": self.job_id, "tasks": {self.tid: child}})

    def outcomes(self, states, raises=False):
        pending = iter(states)
        self.generates = []

        def fake(args, cwd, timeout=600):
            if args[1] == "verify":
                return ""
            self.assertEqual(args[1], "generate")
            self.assertEqual(Path(args[args.index("--job") + 1]), self.frozen)
            self.assertEqual(Path(args[args.index("--output-root") + 1]), self.run_dir.parent)
            self.assertEqual(self.frozen.read_bytes(), self.raw)
            self.assertEqual(store.sha256_file(self.frozen), self.sha)
            child = next(pending)
            self.generates.append(copy.deepcopy(child))
            self.write_state(child)
            if raises and child["phase"] != "completed":
                raise RuntimeError("simulated CLI exception")
            return ""
        self.cli.side_effect = fake

    def confirm(self):
        self.assertEqual(bridge.confirm(self.pid, self.tid, self.rev, self.sha), {"ok": True})
        return self.drain()

    def request(self, method="POST"):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {'X-Client-Build': server.build_status()['build']}
        handler.path = f"/api/projects/{self.pid}/tasks/{self.tid}/resubmit"
        handler._body_json = Mock(return_value={"rev": self.rev})
        responses = []
        handler._json = lambda body, status=200: responses.append((status, body))
        handler._dispatch(method)
        return responses[0]

    def test_safe_failure_retries_even_when_cli_returns_normally(self):
        self.outcomes([SAFE, DONE])
        self.assertEqual(self.confirm(), [])
        self.assertEqual(len(self.generates), 2)
        self.assertEqual(self.record()["state"], "completed")
        self.assertEqual(self.record().get("upload_retries"), 1)
        archive = self.run_dir.with_name(self.job_id + ".upload-failed-1")
        self.assertEqual(json.loads((archive / "state.json").read_text())["tasks"][self.tid], SAFE)
        self.assertEqual(self.sleep.call_args_list, [call(10)])
        self.assertIn("upload transport failed", self.record()["previous_errors"][0])
        logs = [x for x in store.load(self.pid)["log"] if x["level"] == "warn"]
        self.assertEqual(len(logs), 1)
        self.assertIn("还没创建远端任务，没有产生费用", logs[0]["msg"])
        self.assertNotIn("second line", logs[0]["msg"])

    def test_exception_then_success_and_refresh_during_wait(self):
        self.outcomes([SAFE, DONE], raises=True)
        def during_wait(seconds):
            bridge.refresh(self.pid, self.tid, self.rev)
            self.assertEqual(self.record()["state"], "running")
            self.assertFalse(self.record().get("retryable"))
            self.assertEqual(self.request()[0], 400)
        self.sleep.side_effect = during_wait
        self.assertEqual(self.confirm(), [])
        self.assertEqual(self.record()["state"], "completed")

    def test_exhausts_three_retries_then_retryable(self):
        self.outcomes([SAFE] * 4, raises=True)
        self.confirm()
        self.assertEqual(len(self.generates), 4)
        self.assertEqual(self.sleep.call_args_list, [call(10), call(30), call(60)])
        self.assertEqual(self.record()["state"], "failed")
        self.assertTrue(self.record().get("retryable"))
        self.assertEqual(self.record()["upload_retries"], 3)
        self.assertEqual(len(self.record()["previous_errors"]), 3)
        self.assertEqual(len(list(self.run_dir.parent.glob("*.upload-failed-*"))), 3)

    def test_all_unsafe_states_never_generate_twice(self):
        for child in UNSAFE:
            with self.subTest(child=child):
                self.record_update(state="pending_confirmation")
                store.atomic_write_json(self.review / "jimmy-handoff.json", {"state": "ready_for_confirmation"})
                self.outcomes([child], raises=True)
                self.confirm()
                self.assertEqual(len(self.generates), 1)
                self.assertFalse(self.record().get("retryable"))
        self.sleep.assert_not_called()

    def test_api_safe_resubmit_preserves_archive_and_frozen_job(self):
        self.record_update(state="failed")
        self.write_state(SAFE)
        self.outcomes([SAFE, DONE])
        self.assertEqual(self.request(), (200, {"ok": True}))
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(self.request()[0], 400)
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.record()["state"], "completed")
        self.assertEqual(len(list(self.run_dir.parent.glob("*.upload-failed-*"))), 2)
        self.assertEqual(self.frozen.read_bytes(), self.raw)
        self.assertEqual(store.sha256_file(self.frozen), self.sha)

    def test_api_rechecks_disk_and_rejects_unsafe_and_completed(self):
        for child in UNSAFE:
            with self.subTest(child=child):
                self.record_update(state="failed", retryable=True)
                self.write_state(child)
                status, body = self.request()
                self.assertEqual(status, 400)
                self.assertIn("可能已经扣费", body["error"])
                self.assertIn("刷新/恢复", body["error"])
        self.write_state(SAFE)
        for state in ("completed", "running", "submitting", "unknown", "pending_confirmation"):
            with self.subTest(state=state):
                self.record_update(state=state)
                self.assertEqual(self.request()[0], 400)
        self.start.assert_not_called()
        self.cli.assert_not_called()

    def test_api_rejects_changed_frozen_job_and_non_post(self):
        self.record_update(state="failed")
        self.write_state(SAFE)
        self.frozen.write_bytes(b"changed")
        self.assertEqual(self.request()[0], 400)
        self.assertNotEqual(self.request("GET")[0], 200)
        self.start.assert_not_called()
        self.assertTrue((self.run_dir / "state.json").exists())

    def test_missing_or_malformed_state_never_retries(self):
        for raw in (None, "{", "[]", '{"tasks": {}}'):
            with self.subTest(raw=raw):
                self.record_update(state="failed", retryable=True)
                path = self.run_dir / "state.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(raw)
                self.assertEqual(self.request()[0], 400)
        self.start.assert_not_called()

    def add_run(self, rev, state, child=None, tid=None):
        tid = tid or self.tid
        self.active.setdefault(f"gen:{tid}:{rev}", {"state": "done"})
        run_dir = self.d / "jimmy-runs" / f"{self.pid}-{tid}-{rev}"
        run = {"rev": rev, "state": state, "run_dir": store.rel(self.pid, run_dir)}
        store.update(self.pid, lambda x: x["runs"].setdefault(tid, []).append(run))
        if child is not None:
            store.atomic_write_json(run_dir / "state.json", {"tasks": {tid: child}})

    def test_later_completed_run_supersedes_safe_failure(self):
        self.record_update(state="failed", retryable=True)
        self.write_state(SAFE)
        self.add_run("r2", "completed")
        bridge.refresh(self.pid, self.tid, self.rev)
        self.assertFalse(self.record()["retryable"])
        self.assertEqual(self.record().get("superseded_by"), "r2")
        self.assertEqual(self.request(), (400, {"error": "这一段已有更新版本 r2 生成成功或正在生成，不需要重新提交旧版本"}))
        self.start.assert_not_called()
        self.cli.assert_not_called()

    def test_later_active_or_unknown_runs_supersede_without_prior_refresh(self):
        self.write_state(SAFE)
        self.add_run("r2", "running")
        for state in ("running", "submitting", "unknown"):
            with self.subTest(state=state):
                self.record_update(state="failed", retryable=True, superseded_by="stale")
                store.update(self.pid, lambda x: bridge._find_run(x, self.tid, "r2").update(state=state))
                self.assertEqual(self.request(), (400, {"error": "这一段已有更新版本 r2 生成成功或正在生成，不需要重新提交旧版本"}))
                bridge.refresh(self.pid, self.tid, self.rev)
                self.assertFalse(self.record()["retryable"])
                self.assertEqual(self.record().get("superseded_by"), "r2")
        self.start.assert_not_called()
        self.cli.assert_not_called()

    def test_earlier_success_does_not_block_newer_safe_failure(self):
        self.rev = "r2"
        store.update(self.pid, lambda x: x["runs"][self.tid][0].update(rev=self.rev, state="failed"))
        store.update(self.pid, lambda x: x["runs"][self.tid].insert(0, {"rev": "r1", "state": "completed"}))
        self.write_state(SAFE)
        bridge.refresh_all(self.pid)
        self.assertTrue(self.record()["retryable"])
        self.assertNotIn("superseded_by", self.record())
        self.outcomes([DONE])
        self.assertEqual(self.request(), (200, {"ok": True}))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.record()["state"], "completed")

    def test_later_preview_or_failed_runs_do_not_block_resubmit(self):
        self.add_run("r2", "pending_confirmation")
        for state in ("pending_confirmation", "cancelled", "superseded", "failed"):
            with self.subTest(state=state):
                self.record_update(state="failed", retryable=False, superseded_by="r2")
                store.update(self.pid, lambda x: bridge._find_run(x, self.tid, "r2").update(state=state))
                self.write_state(SAFE)
                bridge.refresh_all(self.pid)
                self.assertTrue(self.record()["retryable"])
                self.assertNotIn("superseded_by", self.record())
                self.outcomes([DONE])
                self.assertEqual(self.request(), (200, {"ok": True}))
                self.assertEqual(self.drain(), [])

    def test_first_qualifying_run_uses_list_order_and_same_tid(self):
        self.record_update(state="failed")
        self.write_state(SAFE)
        self.add_run("r2", "pending_confirmation")
        self.add_run("r10", "unknown")
        self.add_run("r3", "completed")
        self.add_run("r99", "completed", tid="t2")
        bridge.refresh_all(self.pid)
        self.assertEqual(self.record().get("superseded_by"), "r10")
        self.assertFalse(self.record()["retryable"])
        store.update(self.pid, lambda x: x["runs"].update({self.tid: [x["runs"][self.tid][0]]}))
        bridge.refresh_all(self.pid)
        self.assertTrue(self.record()["retryable"])
        self.assertNotIn("superseded_by", self.record())

    def test_refresh_recomputes_siblings_after_later_state_changes(self):
        self.record_update(state="failed", retryable=True)
        self.write_state(SAFE)
        self.add_run("r2", "running", {"phase": "running"})
        bridge.refresh_all(self.pid)
        self.assertFalse(self.record()["retryable"])
        self.assertEqual(self.record().get("superseded_by"), "r2")
        later_dir = self.d / bridge._find_run(store.load(self.pid), self.tid, "r2")["run_dir"]
        for child, expected_state, retryable in ((DONE, "completed", False), (SAFE, "failed", True)):
            with self.subTest(state=expected_state):
                store.atomic_write_json(later_dir / "state.json", {"tasks": {self.tid: child}})
                bridge.refresh(self.pid, self.tid, "r2")
                self.assertEqual(bridge._find_run(store.load(self.pid), self.tid, "r2")["state"], expected_state)
                self.assertEqual(self.record()["retryable"], retryable)
                self.assertEqual(self.record().get("superseded_by"), None if retryable else "r2")

    def test_refresh_all_recomputes_after_later_run_stops_superseding(self):
        self.record_update(state="failed", retryable=False, superseded_by="r2")
        self.write_state(SAFE)
        self.add_run("r2", "running", SAFE)
        bridge.refresh_all(self.pid)
        self.assertTrue(self.record()["retryable"])
        self.assertNotIn("superseded_by", self.record())

    def test_superseded_metadata_is_recomputed_without_local_state(self):
        self.record_update(state="failed", retryable=True)
        self.add_run("r2", "completed")
        bridge.refresh(self.pid, self.tid, self.rev)
        self.assertFalse(self.record()["retryable"])
        self.assertEqual(self.record().get("superseded_by"), "r2")
        store.update(self.pid, lambda x: bridge._find_run(x, self.tid, self.rev).pop("run_dir"))
        self.record_update(retryable=True, superseded_by="stale")
        bridge.refresh(self.pid, self.tid, self.rev)
        self.assertFalse(self.record()["retryable"])
        self.assertEqual(self.record().get("superseded_by"), "r2")

    def test_refresh_all_recomputes_metadata_for_completed_runs(self):
        self.record_update(state="completed", retryable=True, superseded_by="stale")
        self.add_run("r2", "completed")
        bridge.refresh_all(self.pid)
        self.assertFalse(self.record()["retryable"])
        self.assertEqual(self.record().get("superseded_by"), "r2")
        self.assertNotIn("superseded_by", bridge._find_run(store.load(self.pid), self.tid, "r2"))

    def image_setup(self, phase="pending_confirmation", child=None):
        self.iid = "img-test"
        self.image_dir = self.d / "image-runs" / self.iid
        self.image_dir.mkdir(exist_ok=True)
        self.image_preview = self.image_dir / "image-job.preview.json"
        self.image_preview.write_bytes(b'{"prompt": "approved image"}')
        store.update(self.pid, lambda x: x["images"].update({self.iid: {
            "id": self.iid, "state": phase, "run_dir": store.rel(self.pid, self.image_dir)}}))
        if child is not None:
            store.atomic_write_json(self.image_dir / "image-state.json", child)

    def image_outcomes(self, states, raises=True):
        pending = iter(states)
        self.image_commands = []
        def fake(args, cwd, timeout=600):
            command = args[1]
            self.image_commands.append(command)
            self.assertEqual(Path(args[args.index("--run") + 1]), self.image_dir)
            self.assertEqual(self.image_preview.read_bytes(), b'{"prompt": "approved image"}')
            if command == "submit":
                child = next(pending)
                store.atomic_write_json(self.image_dir / "image-state.json", child)
                if raises and child["phase"] != "submitted":
                    raise bridge.BridgeError("image upload failed\nsecond line")
                return json.dumps(child)
            self.assertEqual(command, "resume")
            output = self.image_dir / "output.png"
            output.write_bytes(b"mock image bytes")
            return json.dumps({"phase": "completed", "output": str(output)})
        self.cli.side_effect = fake

    def test_image_confirm_retries_only_precreate_upload_failure(self):
        self.image_setup()
        self.image_outcomes([{"phase": "upload_failed"}, {"phase": "submitted", "remote_task_id": "image-id"}])
        self.assertEqual(bridge.image_confirm(self.pid, self.iid), {"ok": True})
        self.assertEqual(self.drain(), [])
        rec = store.load(self.pid)["images"][self.iid]
        self.assertEqual(rec["state"], "completed")
        self.assertEqual(rec["upload_retries"], 1)
        self.assertEqual(self.image_commands, ["submit", "submit", "resume"])
        self.sleep.assert_called_once_with(10)

    def test_image_resume_upload_failure_submits_same_run_with_retries(self):
        self.image_setup("failed", {"phase": "upload_failed", "request": None, "remote_task_id": None})
        self.image_outcomes([{"phase": "upload_failed"}, {"phase": "submitted", "remote_task_id": "image-id"}], raises=False)
        self.assertEqual(bridge.image_resume(self.pid, self.iid), {"ok": True})
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.image_commands, ["submit", "submit", "resume"])
        self.assertEqual(store.load(self.pid)["images"][self.iid]["state"], "completed")

    def test_image_upload_retries_exhausted(self):
        self.image_setup()
        self.image_outcomes([{"phase": "upload_failed"}] * 4)
        bridge.image_confirm(self.pid, self.iid)
        self.assertEqual(len(self.drain()), 1)
        rec = store.load(self.pid)["images"][self.iid]
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec.get("upload_retries"), 3)
        self.assertEqual(self.image_commands, ["submit"] * 4)
        self.assertEqual(self.sleep.call_args_list, [call(10), call(30), call(60)])

    def test_image_unsafe_states_never_resubmit(self):
        for child in ({"phase": "submission_outcome_unknown"}, {"phase": "create_started"},
                      {"phase": "failed"}, {"phase": "uploading"},
                      {"phase": "upload_failed", "remote_task_id": "image-id"},
                      {"phase": "upload_failed", "request": {}},
                      {"phase": "submitted", "remote_task_id": "image-id"}):
            with self.subTest(child=child):
                self.image_setup()
                self.image_outcomes([child])
                bridge.image_confirm(self.pid, self.iid)
                self.drain()
                self.assertEqual(self.image_commands.count("submit"), 1)
                self.image_setup("failed", child)
                self.image_outcomes([])
                bridge.image_resume(self.pid, self.iid)
                self.drain()
                self.assertEqual(self.image_commands, ["resume"])
        self.sleep.assert_not_called()

    def test_frozen_hash_and_safety_rechecked_after_delay(self):
        for change in ("hash", "state"):
            with self.subTest(change=change):
                self.record_update(state="pending_confirmation")
                store.atomic_write_json(self.review / "jimmy-handoff.json", {"state": "ready_for_confirmation"})
                self.outcomes([SAFE])
                def change_during_wait(seconds):
                    if change == "hash":
                        self.frozen.write_bytes(b"modified while waiting")
                    else:
                        self.write_state(UNSAFE[0])
                self.sleep.side_effect = change_during_wait
                self.assertEqual(len(self.confirm()), 1)
                self.assertEqual(len(self.generates), 1)
                self.assertTrue((self.run_dir / "state.json").exists())
        self.assertEqual(list(self.run_dir.parent.glob("*.upload-failed-*")), [])

    def test_missing_request_is_safe_and_existing_archives_are_preserved(self):
        archive = self.run_dir.with_name(self.job_id + ".upload-failed-1")
        archive.mkdir()
        (archive / "keep.txt").write_text("original evidence")
        self.outcomes([{"phase": "failed"}, DONE])
        self.assertEqual(self.confirm(), [])
        self.assertEqual(len(self.generates), 2)
        self.assertEqual((archive / "keep.txt").read_text(), "original evidence")
        self.assertTrue(self.run_dir.with_name(self.job_id + ".upload-failed-2").is_dir())

    def test_simultaneous_resubmits_start_only_one_worker(self):
        self.record_update(state="failed")
        self.write_state(SAFE)
        self.outcomes([DONE])
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.request(), range(2)))
        self.assertEqual(sorted(r[0] for r in responses), [200, 400])
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(self.drain(), [])
        self.assertEqual(len(self.generates), 1)

    def test_worker_start_failure_restores_record_exactly(self):
        self.record_update(state="failed")
        self.write_state(SAFE)
        before = self.record()
        self.start.side_effect = RuntimeError("cannot start worker")
        self.assertEqual(self.request()[0], 400)
        self.assertEqual(self.record(), before)
        self.assertTrue((self.run_dir / "state.json").exists())

    def test_manual_resubmit_logs_safe_reason(self):
        self.record_update(state="failed")
        self.write_state(SAFE)
        self.outcomes([DONE])
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.drain(), [])
        logs = [x for x in store.load(self.pid)["log"] if x["level"] == "warn"]
        self.assertEqual(len(logs), 1)
        self.assertIn("还没创建远端任务，没有产生费用", logs[0]["msg"])
        self.assertIn("三方渠道参考素材上传失败", logs[0]["msg"])

    def test_recover_does_not_mutate_state_while_generate_is_active(self):
        self.record_update(state="running")
        self.write_state(SAFE)
        self.active[f"gen:{self.tid}:{self.rev}"] = {"state": "running"}
        with self.assertRaises(RuntimeError):
            bridge.recover(self.pid, self.tid, self.rev, "original-id")
        self.cli.assert_not_called()
        self.assertEqual(json.loads((self.run_dir / "state.json").read_text())["tasks"][self.tid], SAFE)

    def test_image_resume_still_pending_keeps_manual_recovery_available(self):
        self.image_setup("failed", {"phase": "submitted", "remote_task_id": "image-id"})
        self.cli.return_value = json.dumps({"phase": "submitted"})
        bridge.image_resume(self.pid, self.iid)
        self.assertEqual(self.drain(), [])
        self.assertEqual(store.load(self.pid)["images"][self.iid]["state"], "failed")
        self.assertEqual([c.args[0][1] for c in self.cli.call_args_list], ["resume"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
