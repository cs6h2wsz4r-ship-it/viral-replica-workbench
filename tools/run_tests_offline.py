"""Run every tools/test_*.py offline, including the live probe under complete mocks.

The audit hook rejects secret reads, protected writes, real connections and child
processes except tightly scoped local FFmpeg/FFprobe media checks. No server is
started. Temporary files stay inside runtime/temp.
"""
import io
import hashlib
import json
import os
import shutil
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def audit(event, args):
    if event == "subprocess.Popen":
        executable, command = args[:2]
        name = Path(executable).name
        resolved = shutil.which(executable)
        allowed = {(ROOT / "runtime/ffmpeg" / n).resolve() for n in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe")}
        if not resolved or Path(resolved).resolve() not in allowed or name not in {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}:
            raise AssertionError("离线测试仅允许本地 ffmpeg/ffprobe 子进程")
        if any(any(secret in str(v) for secret in ('console.env', 'openAiKey.txt')) or "://" in str(v) for v in command):
            raise AssertionError("本地媒体测试禁止配置文件和网络输入")
        for value in command:
            path = Path(str(value)).resolve()
            if path.is_relative_to(ROOT / "projects") and not path.is_relative_to(ROOT / "projects/p1027195039-ccc0"):
                raise AssertionError("第九轮只允许读取指定复制项目")
        if name.startswith("ffmpeg") and command[-1] != "-" and not Path(command[-1]).resolve().is_relative_to(ROOT / "runtime/temp"):
            raise AssertionError("媒体测试输出只能写入 runtime/temp")
    if event.startswith("socket.") or event in {"os.system", "os.posix_spawn"}:
        raise AssertionError(f"离线测试禁止：{event}")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if (path.name == "openAiKey.txt" or path.name.startswith("console.env")):
            raise AssertionError("禁止读取或修改密钥文件")
        mode, flags = args[1], args[2]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
        protected = (ROOT / "projects", ROOT / "engine/skills", ROOT / "runtime/models")
        if writing and (not path.is_relative_to(ROOT) or any(path.is_relative_to(p) for p in protected)):
            raise AssertionError(f"禁止写入：{path}")
    if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.chmod", "os.symlink"}:
        paths = args[:2] if event in {"os.rename", "os.symlink"} else args[:1]
        for value in paths:
            if isinstance(value, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(value)).resolve()
                if (path.name == "openAiKey.txt" or path.name.startswith("console.env")):
                    raise AssertionError("禁止修改密钥文件")
                if any(path.is_relative_to(p) for p in (ROOT / "projects", ROOT / "engine/skills", ROOT / "runtime/models")):
                    raise AssertionError(f"禁止修改受保护目录：{path}")


class JimmyProbeOfflineTests(unittest.TestCase):
    """Execute the original probe's text/image/video and --chat paths with fake I/O."""

    def test_original_probe_with_mock_transport(self):
        from console import config, llm, runtime_env
        for chat in (False, True):
            with self.subTest(chat=chat), tempfile.TemporaryDirectory(dir=ROOT / "runtime/temp") as temp:
                folder = Path(temp)
                cfg = {**config.DEFAULTS, "JIMMY_API_KEY": "offline-placeholder"}
                payload = {"code": 0, "msg": "offline mock", "choices": [{"message": {"content": "mock answer"}}],
                           "data": {"candidates": [{"content": {"parts": [{"text": "mock answer"}]}}]}}
                output, requests = io.StringIO(), []

                def request(req, **kwargs):
                    requests.append(json.loads(req.data))
                    response = io.BytesIO(json.dumps(payload).encode())
                    response.status = 200
                    return response

                def fake_ffmpeg(command, **kwargs):
                    if Path(command[0]).name == "ffprobe":
                        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                            "streams": [{"width": 720, "height": 1280}]}), stderr="")
                    self.assertEqual(command[0], "ffmpeg")
                    data = ((ROOT / "projects/p1027195039-ccc0/assets/frame-C1-0.50.jpg").read_bytes()
                            if Path(command[-1]).suffix == ".jpg" else b"offline dummy media")
                    Path(command[-1]).write_bytes(data)

                def source_glob(path, pattern, **kwargs):
                    self.assertEqual(path, ROOT / "projects")
                    self.assertEqual(pattern, "*/source/source.*")
                    return iter([folder / "source.mp4"])

                with patch.object(sys, "argv", ["test_jimmy_gemini.py"] + (["--chat"] if chat else [])), \
                        patch.object(runtime_env, "apply"), patch.object(config, "_parse", return_value=cfg), \
                        patch.object(config, "get", side_effect=lambda key: cfg[key]), \
                        patch.object(llm, "_jimmy_dns_shim"), patch.object(llm, "_ssl_context", return_value=None), \
                        patch("urllib.request.urlopen", side_effect=request), \
                        patch("subprocess.run", side_effect=fake_ffmpeg), patch.object(Path, "glob", source_glob), \
                        patch.object(tempfile, "mkdtemp", return_value=temp), redirect_stdout(output):
                    runpy.run_path(str(ROOT / "tools/test_jimmy_gemini.py"), run_name="__main__")
                self.assertEqual(len(requests), 1 if chat else 3)
                self.assertEqual(output.getvalue().count("HTTP 200"), len(requests))
                self.assertTrue(all(r["model"] for r in requests))


def projects_snapshot():
    root = ROOT / "projects"
    return {"directories": sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir()),
            "project_hashes": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(root.rglob("project.json"))}}


def synchronous_start(pid, key, fn, label):
    # Never allow a callback to outlive the test's temporary project/config patches.
    from console import jobs
    job = {"key": key, "label": label, "state": "running", "error": None}
    jobs._jobs[(pid, key)] = job
    try:
        fn()
        job["state"] = "done"
    except Exception as exc:
        job.update(state="error", error=str(exc))
    return job


def main():
    (ROOT / "runtime/temp").mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(ROOT / "runtime/temp")
    sys.addaudithook(audit)
    before = projects_snapshot()
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for path in sorted((ROOT / "tools").glob("test_*.py")):
        if path.name == "test_jimmy_gemini.py":
            suite.addTests(loader.loadTestsFromTestCase(JimmyProbeOfflineTests))
        else:
            suite.addTests(loader.loadTestsFromName(f"tools.{path.stem}"))
    from console import jobs
    try:
        with patch.object(jobs, "start", side_effect=synchronous_start):
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        after = projects_snapshot()
        unchanged = before == after
        (ROOT / "runtime/temp/round18-projects-integrity.json").write_text(
            json.dumps({"unchanged": unchanged, "before": before, "after": after}, ensure_ascii=False, indent=2))
        print("projects/ 目录与 project.json 哈希不变检查：" + ("通过" if unchanged else "失败"))
    return 0 if result.wasSuccessful() and unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
