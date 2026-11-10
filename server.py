#!/usr/bin/env python3
"""爆款复刻工作台 — local web console.

Run:  python3 server.py      then open http://127.0.0.1:8765
Only listens on 127.0.0.1. Pure standard library; faster-whisper is optional.
"""
from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from console import runtime_env  # noqa: E402  (must run before anything that downloads models)

runtime_env.apply()

from console import asr, config, gateway, jimmy_bridge, jobs, pipeline, replacement_guard, scene_edits, scene_plan, store, storyboard, cast, storyboard_edits, voice_input, director  # noqa: E402
from console.channel_text import user_message, technical_detail
from console import segment_plan, segment_boards, final_preview, segment_review, fix_plan, library, fission, fission_dashboard

def _route_selection(pid, step, body):
    selection = body.get('route') or body.get('provider')
    return gateway.routes.resolve(step,pid,selection)


WEB = Path(__file__).resolve().parent / "web"
VERSION_FILE = WEB.parent / "VERSION"
PID_FILE = runtime_env.RUNTIME / "server.pid"
RESTART_BLOCKED = ""
_project_requests: dict[str, int] = {}  # Guarded by each project's store._lock.


def _server_log(message: str) -> None:
    lines = [f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {line}"
             for line in message.splitlines()]
    text = "\n".join(lines)
    print(text, flush=True)
    try:
        with (runtime_env.RUNTIME / "server.log").open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError as exc:
        print(f"写入 server.log 失败：{exc}", file=sys.stderr, flush=True)


def _remove_trash(path: Path) -> None:
    """Reclaim a detached project without making the DELETE request wait."""
    def remove():
        try:
            shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                _server_log(f"项目回收目录暂未清理完，下次启动重试：{path.name}")
        except Exception as exc:
            _server_log(f"项目回收目录清理失败，下次启动重试：{path.name}：{exc}")
    try:
        threading.Thread(target=remove, name=f"trash:{path.name}", daemon=True).start()
    except RuntimeError as exc:
        _server_log(f"项目回收目录清理未启动，下次启动重试：{path.name}：{exc}")


def _cleanup_trash() -> None:
    trash = config.PROJECTS_DIR / '.trash'
    if trash.is_dir():
        for path in trash.iterdir():
            if path.is_dir():
                _remove_trash(path)


SERVER_FILES = [Path(__file__).resolve(), VERSION_FILE]


def _build_file(path: Path) -> bool:
    return (path.is_file() and not any(p == '__pycache__' or p.startswith('.') for p in path.parts)
            and not path.name.endswith(('~', '.pyc', '.swp', '.swo', '.tmp', '.bak'))
            and not path.name.startswith('#'))


def _hash_build(paths) -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted((p for p in paths if _build_file(p)), key=str):
        digest.update(str(path.relative_to(root) if path.is_relative_to(root) else path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def read_server_build() -> str:
    return _hash_build(SERVER_FILES + list((WEB.parent / 'console').rglob('*.py')))


def read_web_build() -> str:
    return _hash_build(WEB.rglob('*'))


def read_build() -> str:
    return read_server_build() + ':' + read_web_build()


SERVER_BUILD_AT_START = read_server_build()


def build_status() -> dict:
    web = read_web_build()
    disk = read_server_build()
    return {'server_build': SERVER_BUILD_AT_START, 'web_build': web,
            'restart_pending': disk != SERVER_BUILD_AT_START,
            'build': SERVER_BUILD_AT_START + ':' + web, 'build_on_disk': disk + ':' + web}


BUILD_AT_START = read_build()
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GiB
mimetypes.add_type("text/javascript", ".js")


def project_view(data: dict) -> dict:
    """Read-only derived camera validity accompanies every full-project response."""
    # This is the response copy; ignored merge settings must not override the
    # derived casting again in the editor. No project file is saved here.
    scene_edits.discard_invalid_merges(data)
    for frame in data.get("assets", []):
        if frame.get("frame_at") is not None:
            frame.update(pipeline.frame_metadata(data, frame))
    assets = {a["id"]: a for a in data.get("assets", [])}
    for record in (data.get("images") or {}).values():
        if not (record.get("for_task") or "").startswith("camera:"):
            continue
        frame_id = record.get("frame_asset_id") or (record.get("image_ids") or [None])[-1]
        frame = assets.get(frame_id, {})
        if frame.get("frame_at") is not None or record.get("frame_at") is not None:
            metadata = pipeline.frame_metadata(data, {**frame, **{k: record[k] for k in ("frame_at", "frame_reason") if record.get(k) is not None}})
            record.update(metadata)
    data["camera_stale_assets"] = replacement_guard.camera_stale_assets(data, store.project_dir(data["id"]))
    data["camera_stale_images"] = replacement_guard.camera_stale_images(data, store.project_dir(data["id"]))
    data["camera_current"] = {c.get("source_id"): {s["id"]: replacement_guard.camera_image_for(data, c.get("source_id"), s["id"])
        for s in replacement_guard.scenes(data)} for c in (data.get("replacements") or {}).get("characters", [])}
    directory = store.project_dir(data["id"]).resolve()
    data["scene_cast"] = scene_plan.scene_cast(data, directory)
    data['scene_plan_enabled'] = scene_plan.enabled(data)
    if data['scene_plan_enabled']:
        from console import speech_timing
        for task in (data.get('script') or {}).get('tasks', []):
            for run in (data.get('runs') or {}).get(task['id'], []):
                if run.get('state') == 'completed' and run.get('result'):
                    try:
                        timing = speech_timing.for_run(task,run,directory)
                        if timing:
                            run['speech_timing'] = timing
                    except (ValueError,TypeError,OSError):
                        pass
    data["scene_edits_script_stale"] = bool((data.get("replacements") or {}).get("scene_edits") and data.get("script")
        and not replacement_guard.script_current(data, directory))
    data["effective_scenes"] = scene_edits.effective_scenes(data)
    data["scene_editor_scenes"] = scene_edits.effective_scenes(data, include_dropped=True)
    data["missing_asset_files"] = [a["id"] for a in data.get("assets", []) if not a.get("file")
        or not (directory / a["file"]).resolve().is_relative_to(directory) or not (directory / a["file"]).is_file()]
    data["storyboard_status"] = storyboard.status(data, directory)
    data["storyboard_edit_status"] = storyboard_edits.view(data, directory)
    data["segments_plan_status"] = {**segment_plan.status(data, directory),
                                    "material_options": segment_plan.material_options(data, directory)}
    data["segment_board_status"] = {s["id"]: data["storyboard_edit_status"].get(s["id"], {})
                                    for s in (data.get("segments_plan") or {}).get("segments", [])}
    data["director_status"] = director.view(data, directory)
    data["final_preview_status"] = final_preview.view(data, directory)
    for segment in (data.get("director") or {}).get("segments", []):
        signature = director.segment_sig(segment)
        for run in (data.get("runs") or {}).get(segment["id"], []):
            if run.get("kind") == "director":
                run["director_stale"] = run.get("director_sig") != signature
                if run.get('fix_plan'):
                    run['fix_plan'] = fix_plan.view(data, segment['id'], run, directory)
    data["source_product"] = storyboard.product_names(data.get("breakdown") or {})
    data["story_checks_stale"] = cast.story_stale(data, directory)
    data["product_sig"] = cast.product_sig(data, directory)
    data['fission_status'] = fission.eligibility(data, directory)
    data['library_saved_assets'] = library.saved_assets(data, directory)
    return data


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "ViralReplica/1.0"

    def log_message(self, fmt, *args):  # quieter console; args may be ints (send_error)
        first = str(args[0]) if args else ""
        request = first.split()
        if len(request) >= 2 and request[0] == "GET" and request[1].split("?", 1)[0] == "/api/status":
            return
        if first.startswith("GET /api/projects/") or first.startswith("GET /files/"):
            return
        try:
            sys.stderr.write("%s\n" % (fmt % args))
        except Exception:
            pass

    # ---------- helpers ----------
    def _json(self, obj, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError("请求体不是合法 JSON")

    def _body_file(self, pid: str) -> Path:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ApiError("空文件")
        if length > MAX_UPLOAD:
            raise ApiError("文件超过 1GB")
        tmp_dir = store.project_dir(pid) / ".upload"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=tmp_dir)
        remaining = length
        with os.fdopen(fd, "wb") as fh:
            while remaining:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining:
            Path(name).unlink(missing_ok=True)
            raise ApiError("上传中断")
        return Path(name)

    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = fh.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---------- dispatch ----------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if method == "GET" and (path == "/" or path == "/index.html"):
                return self._serve_file(WEB / "index.html")
            if method == "GET" and path.startswith("/static/"):
                target = (WEB / path[len("/static/"):]).resolve()
                if WEB.resolve() not in target.parents:
                    raise ApiError("forbidden", 403)
                return self._serve_file(target)
            if method == "GET" and path.startswith("/files/"):
                _, _, pid, rest = path.split("/", 3)
                base = store.project_dir(pid).resolve()
                target = (base / rest).resolve()
                if base not in target.parents:
                    raise ApiError("forbidden", 403)
                return self._serve_file(target)
            if path.startswith("/api/"):
                result = self._api(method, path[5:].strip("/").split("/"), query)
                if isinstance(result, Path):
                    return self._serve_file(result)
                if isinstance(result, dict) and result.get("id") and "assets" in result:
                    result = project_view(result)
                return self._json(result)
            self.send_error(404)
        except ApiError as exc:
            self._json({"error": user_message(exc)}, exc.status)
        except store.ProjectDeletedError as exc:
            self._json({"error": str(exc)}, 404)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            payload = {"error": user_message(exc)}
            if isinstance(exc, gateway.GatewayError) and path.endswith('/script/dialogue-proposal'):
                payload['error_kind'] = gateway.classify_error(exc)
            if isinstance(exc, jimmy_bridge.BridgeError) and exc.issues is not None:
                payload["issues"] = exc.issues
                payload["errors"] = user_message(exc).splitlines()
            self._json(payload, 400)
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            self._json({"error": user_message(f"内部错误：{exc}")}, 500)

    def _api(self, method: str, parts: list[str], query: dict):
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            current = build_status()
            headers = getattr(self, 'headers', {})
            client_build = headers.get("X-Client-Build")
            server_build, web_build = headers.get('X-Server-Build'), headers.get('X-Web-Build')
            if current['restart_pending']:
                raise ApiError("工作台正在更新，请稍候刷新", 409)
            if not (client_build or server_build or web_build):
                raise ApiError("页面缺少版本信息，为避免写坏数据已拒绝本次保存，请刷新页面", 409)
            if ((server_build or web_build) and (server_build != current['server_build'] or web_build != current['web_build'])) or (client_build and client_build != current['build']):
                raise ApiError("页面版本过旧，为避免写坏数据已拒绝本次保存，请刷新页面", 409)
        if parts == ["status"]:
            route_values = config.load()
            return {**build_status(), "restart_blocked": RESTART_BLOCKED,
                    "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    "asr": asr.available(), "asr_engines": asr.engines(), "jimmy": config.jimmy_configured(),
                    "runtime": runtime_env.report(),
                    "providers": gateway.channels(),
                    "routes": gateway.routes.public_routes(),
                    "dialogue_provider_routes": {channel: gateway.routes.resolve('dialogue_proposal', override=channel, values=route_values)
                                                 for channel in config.PROVIDERS},
                    "default_llm": config.default_llm(),
                    "default_image_channel": 'official' if config.get('OPENAI_API_KEY') else 'third_party',
                    "openai_image_model": config.get('OPENAI_IMAGE_MODEL') or 'gpt-image-2'}
        if parts == ["settings", "test-connection"] and method == "POST":
            return gateway.test_connection(self._body_json().get('channel'))
        if parts == ["settings", "local-models"] and method == "GET":
            from console import local_models
            return {"models": local_models.report(config.get('ASR_MODEL') or 'medium')}
        if parts == ["settings"]:
            if method == "GET":
                return config.public_view()
            config.save(self._body_json())
            return config.public_view()
        if parts == ["projects"]:
            if method == "GET":
                return store.list_projects()
            return store.create(self._body_json().get("name", ""))
        if parts == ['library']:
            if method == 'GET': return library.list_all()
            if method == 'POST':
                length = int(self.headers.get('Content-Length') or 0)
                if not 0 < length <= 30 * 1024 * 1024: raise ApiError('请上传不超过 30MB 的照片')
                raw = self.rfile.read(length)
                if len(raw) != length: raise ApiError('照片没有上传完整，请重试')
                return library.upload(query.get('kind'), query.get('name'), urllib.parse.unquote(self.headers.get('X-Filename','photo')), raw)
        if len(parts) == 3 and parts[0] == 'library' and parts[2] == 'file' and method == 'GET':
            return library.file_path(parts[1])
        if len(parts) == 2 and parts[0] == 'library' and parts[1] != 'groups':
            if method in ('PUT','PATCH'): return library.rename(parts[1], self._body_json().get('name'))
            if method == 'DELETE': return library.delete(parts[1])
        if len(parts) >= 2 and parts[0] == 'fission-boards':
            if len(parts) == 2 and method == 'GET':
                return fission_dashboard.view(parts[1])
            if len(parts) == 4 and method == 'POST' and parts[2] in ('images', 'videos'):
                if parts[3] == 'prepare':
                    return fission_dashboard.prepare(parts[1], parts[2])
                if parts[3] == 'confirm':
                    return fission_dashboard.confirm(parts[1], parts[2], self._body_json())
            raise ApiError('not found', 404)
        if len(parts) == 2 and parts[0] == 'batches' and method == 'GET':
            return fission.batch_view(parts[1])
        if len(parts) < 2 or parts[0] != "projects":
            raise ApiError("not found", 404)
        pid = parts[1]
        rest = parts[2:]
        store.project_dir(pid)  # validates id
        # Reserve synchronous writes before releasing the lock for uploads,
        # model I/O or subprocesses. DELETE can promptly explain why it cannot
        # proceed, while project reads/listing remain responsive.
        with store._lock(pid):
            store.load(pid)
            if method == 'DELETE' and not rest:
                return self._project_api(method, pid, rest, query)
            if method != 'GET':
                _project_requests[pid] = _project_requests.get(pid, 0) + 1
        try:
            return self._project_api(method, pid, rest, query)
        finally:
            if method != 'GET':
                with store._lock(pid):
                    _project_requests[pid] -= 1
                    if not _project_requests[pid]:
                        del _project_requests[pid]

    def _project_api(self, method: str, pid: str, rest: list[str], query: dict):
        if rest == ['library-import'] and method == 'POST':
            return library.to_project(pid, self._body_json())
        if rest == ['library'] and method == 'POST':
            return library.from_project(pid, self._body_json())
        if rest == ['fission-draft']:
            if method == 'GET': return fission.view(pid)
            if method == 'PUT': return fission.save_draft(pid, self._body_json())
        if rest == ['fission-draft', 'story-check']:
            if method == 'POST': return fission.check_story(pid, self._body_json())
            if method == 'PUT': return fission.answer_story(pid, self._body_json())
        if rest == ['fission']:
            if method == 'GET': return fission.view(pid)
            if method == 'POST': return fission.create(pid, self._body_json())
        if rest == ['fission-retry'] and method == 'POST':
            return fission.retry(pid, self._body_json())
        if rest == ["segment-plan"] and method == "POST":
            return project_view(segment_plan.generate(pid))
        if len(rest) == 2 and rest[0] == "segment-plan" and method == "PUT":
            return project_view(segment_plan.save(pid, rest[1], self._body_json()))
        if rest == ["final-preview"] and method == "POST":
            return project_view(final_preview.generate(pid))
        if rest == ["final-preview", "check"] and method == "POST":
            result = final_preview.check(pid, self._body_json())
            result['jobs'] = jobs.snapshot(pid)
            return project_view(result)
        if rest == ["final-preview", "check", "accept"] and method == "POST":
            return project_view(final_preview.accept_check(pid, self._body_json()))
        if len(rest) == 5 and rest[0] == "runs" and rest[3:] == ["review", "accept"] and method == "POST":
            return project_view(segment_review.accept(pid, rest[1], rest[2], self._body_json()))
        if len(rest) == 4 and rest[0] == "runs" and rest[3] == "fix-plan" and method in {"POST", "PUT"}:
            action = fix_plan.generate if method == "POST" else fix_plan.update
            return project_view(action(pid, rest[1], rest[2], self._body_json()))
        if len(rest) == 5 and rest[0] == "runs" and rest[3:] == ["fix-plan", "apply"] and method == "POST":
            return project_view(fix_plan.apply(pid, rest[1], rest[2], self._body_json()))
        if len(rest) == 4 and rest[0] == "runs" and rest[3] == "review" and method == "POST":
            self._body_json()
            result = segment_review.start(pid, rest[1], rest[2], force=True)
            result['jobs'] = jobs.snapshot(pid)
            return project_view(result)
        if rest == ["director"] and method in {"POST", "PUT"}:
            body = self._body_json()
            if method == "POST" and body.get("action", "generate") == "generate":
                return project_view(director.generate(pid))
            return project_view(director.save(pid, body))
        if rest == ["storyboard-edits"] and method == "POST":
            body = self._body_json()
            if body.get("action") != "reslice":
                raise ApiError("这个操作不支持，请刷新页面", 400)
            from console import storyboard_images
            return project_view(storyboard_images.reslice(pid, body.get("sheet_id"), body.get("version_id")))
        if rest == ["voice-to-text"] and method == "POST":
            return voice_input.transcribe_stream(self.rfile, int(self.headers.get("Content-Length") or 0))
        if len(rest) >= 2 and rest[0] == "storyboard-edits":
            sid = rest[1]
            if method == "PUT" and len(rest) == 2:
                return project_view(storyboard_edits.save(pid, sid, self._body_json()))
            if method == "POST" and len(rest) == 3:
                body = self._body_json()
                if rest[2] == "plan":
                    return project_view(storyboard_edits.generate_plan(pid, sid, body))
                if rest[2] == "template":
                    data = storyboard_edits.save(pid, sid, body)
                    materials = storyboard_edits.materials_for(data, sid, store.project_dir(pid))
                    return {"text": storyboard_edits.request_template(data, sid, materials, store.project_dir(pid))}
                if rest[2] == "preview":
                    return storyboard_edits.preview(pid, sid, body)
                if rest[2] == "use":
                    return project_view(storyboard_edits.use_version(pid, sid, body.get("version")))
        if rest == ["storyboard"]:
            if method == "GET":
                return storyboard.status(store.load(pid), store.project_dir(pid))
            if method == "POST":
                body = self._body_json()
                action = body.get("action")
                if action in {"build", "restore"}:
                    return storyboard.build(pid)
                if action == "merge":
                    return storyboard.merge(pid, body.get("cell_id"))
                raise ValueError("请选择生成、合并或恢复自动切格")
        if rest == ["cast", "identify"] and method == "POST":
            result = cast.identify(pid, self._body_json())
            result["project"] = project_view(result["project"])
            return result
        if rest == ["cast", "story-check"] and method == "POST":
            result = cast.check_story(pid, self._body_json())
            result["project"] = project_view(result["project"])
            return result
        if rest == ["thumb"] and method == "GET":
            return scene_plan.cached_thumbnail(pid, query.get('at'))
        if len(rest) == 2 and rest[0] == "routes" and method == "DELETE":
            step = rest[1]
            if step not in gateway.routes.DEFAULT_ROUTES:
                raise ValueError('未知模型步骤：' + step)
            data = store.update(pid, lambda d: d.get('routes', {}).pop(step, None))
            return {**data, "resolved_routes": gateway.routes.public_routes(data)}

        if not rest and method == "GET":
            live_jobs = jobs.snapshot(pid)
            if live_jobs:
                try:
                    gateway.video_refresh_all(pid)
                except Exception:
                    pass
                pipeline.recover_stale(pid, live_jobs)
            data = store.load(pid)
            data["jobs"] = jobs.snapshot(pid)
            data["resolved_routes"] = gateway.routes.public_routes(data)
            return data
        if not rest and method == "DELETE":
            data = store.load(pid)
            if data.get('derived_from') and fission_dashboard.has_remote_work(data):
                raise ApiError("这个裂变项目还有生成结果未确认，请先重新查询或恢复任务，再删除项目")
            if any(r.get('state') in {'submitting', 'running'}
                   for runs in (data.get('runs') or {}).values() for r in runs):
                raise ApiError("这个项目还有视频在生成，等它结束后再删除")
            if _project_requests.get(pid) or any(j["state"] == "running" for j in jobs.snapshot(pid).values()):
                raise ApiError("这个项目还有任务在运行，等它结束后再删除")
            trash = config.PROJECTS_DIR / '.trash'
            trash.mkdir(exist_ok=True)
            detached = trash / f'{pid}-{time.time_ns()}'
            os.replace(store.project_dir(pid), detached)
            _remove_trash(detached)
            return {"deleted": pid}
        if rest == ["source"] and method == "DELETE":
            data = store.load(pid)
            if data.get('derived_from') and fission_dashboard.has_remote_work(data):
                raise ApiError("这个裂变项目还有生成结果未确认，请先重新查询或恢复任务，再清除原片")
            if any(j["state"] == "running" for j in jobs.snapshot(pid).values()):
                raise ApiError("还有任务在运行，等它结束后再清除")
            return pipeline.clear_source(pid)
        if not rest and method == "PATCH":
            body = self._body_json()
            return store.update(pid, lambda d: d.update(name=body.get("name", d["name"])))

        if rest == ["source"] and method == "POST":
            tmp = self._body_file(pid)
            name = urllib.parse.unquote(self.headers.get("X-Filename") or "source.mp4")
            return pipeline.save_source(pid, name, tmp)
        if rest == ["assets"] and method == "POST":
            tmp = self._body_file(pid)
            name = urllib.parse.unquote(self.headers.get("X-Filename") or "asset.png")
            roles = [r for r in (query.get("roles") or "").split(",") if r]
            return pipeline.save_asset(pid, name, tmp, query.get("kind", "image"), query.get("label", ""), roles)
        if len(rest) == 2 and rest[0] == "assets" and method == "PATCH":
            body = self._body_json()
            aid = rest[1]

            def fn(d):
                for a in d["assets"]:
                    if a["id"] == aid:
                        for k in ("label", "description"):
                            if k in body:
                                a[k] = str(body[k])
                        if isinstance(body.get("roles"), list) and body["roles"]:
                            a["roles"] = [str(r) for r in body["roles"]]
            return store.update(pid, fn)
        if len(rest) == 2 and rest[0] == "assets" and method == "DELETE":
            return pipeline.delete_asset(pid, rest[1])

        if rest == ["analyze"] and method == "POST":
            body = self._body_json()
            provider = _route_selection(pid, "analysis", body)
            config.provider_settings(provider["channel"])  # fail fast if not configured
            jobs.start(pid, "analyze", lambda: pipeline.run_analysis(pid, provider, body.get("srt_text") or None,
                                                                     bool(body.get("skip_asr")),
                                                                     bool(body.get("send_video"))), "拆解")
            return {"ok": True}
        if rest == ["breakdown"] and method == "PUT":
            body = self._body_json()
            return store.update(pid, lambda d: d.update(breakdown=body["breakdown"]))
        if rest == ["breakdown", "select"] and method == "POST":
            prov = self._body_json().get("provider")

            def fn(d):
                v = (d.get("breakdown_versions") or {}).get(prov)
                if not v:
                    raise ValueError("没有这个模型的拆解结果")
                d["breakdown"] = v["pack"]
                d["breakdown_provider"] = prov
            return store.update(pid, fn)
        if rest == ["scene-edits"] and method == "POST":
            body = self._body_json()
            return pipeline.edit_scenes(pid, body.get("action"), body.get("scene_id"), body.get("next_scene_id"))
        if rest == ["replacements"] and method == "PUT":
            body = self._body_json()
            return pipeline.save_replacements(pid, body)
        if rest == ["script"] and method == "POST":
            body = self._body_json()
            provider = _route_selection(pid, "script", body)
            config.provider_settings(provider["channel"])
            rng = body.get("range") if isinstance(body.get("range"), list) else None
            jobs.start(pid, "script", lambda: pipeline.run_script(
                pid, provider, body.get("dialogue_mode", "keep"), body.get("instruction", ""), rng,
                review_dialogue=True), "新脚本生成")
            return {"ok": True}
        if rest == ["script", "camera-repair"] and method == "POST":
            body = self._body_json()
            return pipeline.repair_camera_reference(pid, body["task_id"], body["character_id"], body["scene_id"])
        if rest == ["script", "dialogue-proposal"] and method == "POST":
            body = self._body_json()
            return pipeline.dialogue_proposal(pid, body["task_id"], body.get("instruction", ""), provider=_route_selection(pid, 'dialogue_proposal', body), previous_proposal=body.get('previous_proposal'))
        if rest == ["script", "dialogue-adopt"] and method == "POST":
            body = self._body_json()
            return pipeline.adopt_dialogue_proposal(pid, body["task_id"], body.get("lines"))
        if rest == ["script", "dialogue-keep"] and method == "POST":
            return pipeline.keep_original_dialogue(pid, self._body_json()["task_id"])
        if rest == ["script"] and method == "PUT":
            body = self._body_json()
            return pipeline.save_script(pid, body.get("script"), reference_orders=body.get("reference_orders"))
        if rest == ["replacement-check"] and method == "POST":
            # Read-only: check unsaved task edits with server-held script provenance.
            body = self._body_json()
            data = store.load(pid)
            if isinstance(body.get("replacements"), dict):
                data["replacements"] = body["replacements"]
            tasks = body.get("tasks") or (data.get("script") or {}).get("tasks", [])
            return {t["id"]: jimmy_bridge.replacement_check(data, t, store.project_dir(pid)) for t in tasks}
        if len(rest) == 3 and rest[0] == "tasks" and method == "POST":
            tid, action = rest[1], rest[2]
            body = self._body_json()
            if action == "preview":
                return gateway.video_preview(pid, tid, **({'fix_plan': body['fix_plan']} if 'fix_plan' in body else {}))
            if action == "dismiss-motion-warning":
                return jimmy_bridge.dismiss_motion_warning(pid, tid, body.get("fingerprint"))
            if action == "confirm":
                return gateway.video_confirm(pid, tid, body["rev"], body["job_sha256"])
            if action == "resubmit" and method == "POST":
                return gateway.video_resubmit(pid, tid, body["rev"])
            if action == "resume":
                return gateway.video_resume(pid, tid, body["rev"])
            if action == "recover":
                return gateway.video_recover(pid, tid, body["rev"], body["remote_task_id"])
            if action == "choose":
                if any(s.get("id") == tid for s in (store.load(pid).get("director") or {}).get("segments", [])):
                    return director.save(pid, {"action": "select-run", "segment_id": tid, "rev": body["rev"]})
                def fn(d):
                    for t in d["script"]["tasks"]:
                        if t["id"] == tid:
                            t["chosen_rev"] = body["rev"]
                return store.update(pid, fn)
            if action == "cancel":
                def fn(d):
                    for r in d.get("runs", {}).get(tid, []):
                        if r["rev"] == body["rev"] and r["state"] == "pending_confirmation":
                            r["state"] = "cancelled"
                return store.update(pid, fn)
        if rest == ["camera-image", "use"] and method == "POST":
            b = self._body_json()
            return pipeline.use_camera_image(pid, b.get("character_id"), b.get("scene_id"), b.get("asset_id"))
        if rest == ["camera-image", "draft"] and method == "POST":
            b = self._body_json()
            provider = _route_selection(pid, "camera_prompt", b)
            at = b.get("at")
            return pipeline.draft_camera_image(pid, provider, b["character_id"], float(at) if at not in (None, "") else None, scene_id=b.get("scene_id"))
        if rest == ["scene-camera", "frame"] and method == "POST":
            b = self._body_json()
            return pipeline.capture_scene_frame(pid, b.get("scene_id"), b.get("at") if b.get("at") not in (None, "") else None)
        if rest == ["scene-camera", "draft"] and method == "POST":
            b = self._body_json()
            provider = _route_selection(pid, "camera_prompt", b)
            return pipeline.draft_scene_camera_image(pid, provider, b.get("scene_id"), b.get("at") if b.get("at") not in (None, "") else None)
        if rest == ["scene-camera", "use"] and method == "POST":
            b = self._body_json()
            return pipeline.use_scene_camera_image(pid, b.get("scene_id"), b.get("asset_id"))
        if rest == ["images", "preview"] and method == "POST":
            b = self._body_json()
            selected = {'channel':b['channel'],'model':b.get('model')} if b.get('channel') else None
            if b.get('route_selected') is False and (b.get('for_task') or '').startswith(('camera:', 'scene-camera:')):
                selected = None  # Default camera previews follow the saved per-step model.
            if selected is None and not (b.get('for_task') or '').startswith(('camera:', 'scene-camera:')):
                selected = {'channel':'third_party','model':b.get('model') or 'gpt-image-2'}
            return gateway.image_preview('camera_image', pid, b.get("prompt", ""), b.get("image_ids") or [], b.get("ratio", "9:16"),
                                              b.get('model'), b.get("resolution", "2k"), b.get("for_task"), override=selected,
                                              draft_binding=b.get("camera_binding"), draft_frame_reason=b.get("frame_reason_code"))
        if len(rest) == 3 and rest[0] == "images" and method == "POST":
            if rest[2] == "confirm":
                return gateway.image_confirm(pid, rest[1])
            if rest[2] == "resume":
                return gateway.image_resume(pid, rest[1])
        if rest == ["assembly-options"] and method == "PUT":
            return jimmy_bridge.save_assembly_options(pid, self._body_json())
        if rest == ["assemble"] and method == "POST":
            return jimmy_bridge.assemble(pid)
        raise ApiError("not found", 404)


CODE_FILES = [Path(__file__).resolve(), VERSION_FILE]


def _check_new_code() -> str:
    """Use a fresh interpreter so imports cannot reuse the running process's modules."""
    code = """
import importlib
import py_compile
from pathlib import Path
py_compile.compile('server.py', doraise=True)
files = sorted(Path('console').rglob('*.py'))
for path in files:
    py_compile.compile(str(path), doraise=True)
from console import runtime_env
runtime_env.apply()
for path in files:
    importlib.import_module('.'.join(path.with_suffix('').parts).removesuffix('.__init__'))
"""
    try:
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(WEB.parent),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return "代码导入检查超时（60 秒）"
    except OSError as exc:
        return f"无法执行代码导入检查：{exc}"
    if proc.returncode:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        # Put the exception summary first for the one-line browser banner, then retain context.
        return "\n".join([lines[-1], *lines[-6:-1]]) if lines else f"代码导入检查失败（退出码 {proc.returncode}）"
    return ""


def _watch_code_and_restart() -> None:
    """Restart only after five quiet seconds, successful imports and idle jobs."""
    import threading
    import time as _t

    def snapshot():
        files = CODE_FILES + sorted((WEB.parent / "console").rglob("*.py"))
        result = {}
        for f in files:
            try:
                result[f] = f.stat().st_mtime_ns
            except FileNotFoundError:
                pass
        return result

    def loop():
        global RESTART_BLOCKED
        before = snapshot()
        changed_at = None
        while True:
            _t.sleep(1)
            now = snapshot()
            if now != before:
                before = now
                changed_at = _t.monotonic()
            if changed_at is None or _t.monotonic() - changed_at < 5 or jobs.any_running():
                continue
            reason = _check_new_code()
            after = snapshot()
            if after != before:
                before = after
                changed_at = _t.monotonic()
                continue
            RESTART_BLOCKED = reason
            if reason:
                _server_log(f"代码更新重启被拦下，继续运行旧版本：{reason}")
                changed_at = None  # Retry only when another file change is observed.
                continue
            if jobs.any_running():
                continue
            _server_log("检测到代码更新，工作台自动重启…")
            os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=loop, daemon=True, name="code-watch").start()


def main() -> None:
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_trash()
    port = int(os.environ.get("PORT") or config.get("PORT") or 8765)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        PID_FILE.write_text(str(os.getpid()) + "\n", encoding="utf-8")
        _watch_code_and_restart()
        _server_log(f"爆款复刻工作台已启动： http://127.0.0.1:{port}")
        rep = runtime_env.report()
        print(f"  依赖目录: {rep['runtime']}（{rep['runtime_mb']} MB）| 项目目录: {rep['projects']}")
        if not rep["running_in_venv"]:
            print("  ⚠️ 没有使用工作台自带的 Python 环境：请先运行 ./install.sh，再用 ./start.command 启动")
        print(f"  FFmpeg: {'OK' if shutil.which('ffmpeg') else '未安装'} | faster-whisper: {'OK' if asr.available() else '未安装（可上传 SRT）'}"
              f" | 三方渠道 Key: {'已填写' if config.jimmy_configured() else '未填写（在网页右上角“设置”里填）'}")
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
