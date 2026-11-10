#!/usr/bin/env python3
"""Prepare and verify a local Chain 5 preview. No provider imports or network calls."""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse


DEFAULT_MODEL = "seedance2.0-mini"
DEFAULT_VIDEO_MODEL = "seedance2.0-mini-video"
FAST_MODEL = "seedance2.0-fast-md"
MAX_FILE_BYTES = 100 * 1024 * 1024
V1_RATIOS = {"16:9", "9:16", "1:1"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EXTENSIONS = {"image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"},
              "video": {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"},
              "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}}
FILES = ("jimmy-job.preview.json", "jimmy-handoff.json", "chain5-ready.json")
OFFICIAL_SOURCE = "https://docs.viraltok.ai/en/api-reference/seedance/md/create"
MINI_SOURCE = "https://docs.viraltok.ai/en/api-reference/seedance/mini/create"
UNSUPPORTED_TRANSPORT = {"resolution", "first_image", "last_image", "first_frame", "last_frame",
                         "reference_audios", "referenceAudios", "audio", "generate_audio",
                         "reference_mode", "face_processing", "referenceImages", "reference_images",
                         "referenceVideos", "images", "reference_videos"}


class PackageError(ValueError):
    """A local package cannot safely be prepared or reused."""


def _fail(where: str, message: str) -> None:
    raise PackageError(f"{where}: {message}")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(where, "must be a non-empty string")
    return value


def _identifier(value: Any, where: str) -> str:
    value = _text(value, where)
    if not ID_RE.fullmatch(value):
        _fail(where, "must use 1..128 ASCII letters, digits, dot, underscore or hyphen")
    return value


def _number(value: Any, where: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(where, "must be a finite number")
    if positive and value <= 0:
        _fail(where, "must be positive")
    return float(value)


def _integer(value: Any, where: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(where, f"must be an integer >= {minimum}")
    return value


def _https(value: Any, where: str) -> str:
    value = _text(value, where)
    try:
        parsed = urlparse(value)
        valid = (parsed.scheme == "https" and parsed.hostname and parsed.netloc
                 and parsed.username is None and parsed.password is None
                 and value == value.strip() and not any(c.isspace() for c in value))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        _fail(where, "must be an absolute HTTPS URL without credentials or whitespace")
    return value


def _local_path(raw: Any, base: Path, where: str) -> Path:
    path = Path(_text(raw, where)).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    secret_root = (Path.home() / ".codex" / "secrets").resolve()
    if path == secret_root or secret_root in path.parents or path.suffix.lower() == ".env":
        _fail(where, "credential files cannot be read by this local preparation tool")
    return path


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    path = _local_path(str(path), Path.cwd(), "JSON path")
    try:
        raw = path.read_bytes()
        value = json.loads(raw, parse_constant=lambda constant: _fail("JSON", f"unsupported non-finite constant {constant}"))
    except (OSError, ValueError) as exc:
        _fail(str(path), f"cannot read JSON: {exc}")
    if not isinstance(value, dict):
        _fail(str(path), "must contain a JSON object")
    return value, raw


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_format(path: Path) -> str | None:
    """Detect relevant real image signatures, including GIF renamed to .png."""
    with path.open("rb") as handle:
        header = handle.read(16)
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    return None


def _probe(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if executable is None:
        _fail(str(path), "ffprobe is required for actual local video preflight; provide legal prepared media")
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-protocol_whitelist", "file,pipe",
             "-format_whitelist", "mov,matroska,webm,avi", "-show_entries",
             "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration:format=duration,size",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        streams = [item for item in data.get("streams", []) if item.get("codec_type") == "video"]
        if not streams:
            _fail(str(path), "ffprobe found no video stream")
        video = streams[0]
        rate = video.get("avg_frame_rate")
        if not rate or Fraction(rate) <= 0:
            rate = video.get("r_frame_rate")
        fps = float(Fraction(rate))
        durations = [float(x) for x in (video.get("duration"), data.get("format", {}).get("duration"))
                     if x is not None and x != "N/A"]
        if not durations:
            _fail(str(path), "ffprobe could not establish video duration")
        probe = {"duration": max(durations), "fps": fps, "width": video.get("width"),
                 "height": video.get("height"), "size_bytes": path.stat().st_size,
                 "audio_stream_count": sum(item.get("codec_type") == "audio" for item in data.get("streams", []))}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, ZeroDivisionError) as exc:
        _fail(str(path), f"actual ffprobe preflight failed: {exc}")
    return probe



def _probe_audio(path: Path) -> dict[str, Any]:
    """Probe and fully decode real local audio; extension is not evidence of valid sound."""
    probe_exe, decode_exe = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe_exe or not decode_exe:
        _fail(str(path), "ffprobe and ffmpeg are required for actual audio preflight")
    formats = "wav,mp3,mov,aac,flac,ogg,matroska,webm"
    try:
        result = subprocess.run([probe_exe, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", formats, "-show_entries",
            "stream=codec_type,sample_rate,channels,duration:format=duration,size", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=30)
        data = json.loads(result.stdout)
        audios = [item for item in data.get("streams", []) if item.get("codec_type") == "audio"]
        if len(audios) != 1 or any(item.get("codec_type") == "video" for item in data.get("streams", [])):
            _fail(str(path), "voice audio must contain exactly one audio stream and no video stream")
        durations = [float(x) for x in (audios[0].get("duration"), data.get("format", {}).get("duration"))
                     if x is not None and x != "N/A"]
        if not durations:
            _fail(str(path), "ffprobe could not establish audio duration")
        result = {"duration": max(durations), "sample_rate": int(audios[0].get("sample_rate", 0)),
                  "channels": audios[0].get("channels"), "audio_stream_count": len(audios),
                  "size_bytes": path.stat().st_size}
        subprocess.run([decode_exe, "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", formats, "-i", str(path), "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True, text=True, check=True, timeout=60)
        return result
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        _fail(str(path), f"actual audio probe/decode preflight failed: {exc}")

def _verified_evidence(data: dict[str, Any], where: str) -> None:
    if data.get("verified") is not True:
        _fail(where, "must explicitly assert verified=true based on checked evidence")
    _text(data.get("verified_at"), f"{where}.verified_at")
    _text(data.get("evidence"), f"{where}.evidence")


def _capabilities(plan: dict[str, Any], base: Path) -> tuple[str, dict[str, Any], dict[str, str] | None]:
    selected = plan.get("selected_mode") if plan.get("mode") == "auto" else plan.get("mode")
    default_model = DEFAULT_VIDEO_MODEL if selected == "video" else DEFAULT_MODEL
    model = plan.get("model", default_model)
    model = _text(model, "model")
    if model != model.strip():
        _fail("model", "must be exact, without leading/trailing whitespace; the tool will not rename it")
    if model in (DEFAULT_MODEL, DEFAULT_VIDEO_MODEL):
        if "capabilities_path" in plan:
            _fail("capabilities_path", "built-in verified models cannot be overridden by a sidecar")
        caps = {"model": model, "durations": list(range(4, 16)),
                "ratios": sorted(V1_RATIOS), "prompt_max_chars": 5000,
                "max_images": 4, "max_reference_videos": 1 if model == DEFAULT_VIDEO_MODEL else 0,
                "max_reference_audios": 1,
                "audio": {"min_duration_seconds": 1, "max_duration_seconds": 15, "max_file_bytes": MAX_FILE_BYTES,
                          "planning_policy": "Local policy only: one clean 1..15s mono/stereo voice reference; around 5s recommended. Official audio capacity limits are not inferred."},
                "unsupported_image_formats": [],
                "video": {"max_duration_seconds": 15, "max_file_bytes": MAX_FILE_BYTES},
                "source": MINI_SOURCE,
                "evidence": "Official Mini contract checked 2026-10-15: integer 4..15s; only Mini-video accepts video references",
                "planning_policy": "Existing Jimmy v1: at most 4 images, 5000 prompt characters, 3 ratios; local policy: at most one reference video <=15s and files <=100 MiB. These are not Mini official capacity limits."}
        return model, caps, None
    if model in (FAST_MODEL, "seedance2.0-md"):
        if "capabilities_path" in plan:
            _fail("capabilities_path", "built-in verified models cannot be overridden by a sidecar")
        fast = model == FAST_MODEL
        caps = {"model": model, "durations": [10, 15] if fast else [5, 10, 15],
                "ratios": sorted(V1_RATIOS), "prompt_max_chars": 5000,
                "max_images": 4, "max_reference_videos": 1,
                "unsupported_image_formats": ["gif"],
                "video": {"max_duration_seconds": 15, "max_file_bytes": MAX_FILE_BYTES},
                "source": OFFICIAL_SOURCE,
                "evidence": "Official MD contract checked 2026-10-14; v1 compatibility and one-video planning policy"}
        if fast:
            caps["video"].update(min_fps=24, max_fps=60, min_dimension=720, max_dimension=2160)
            caps["video"]["dimension_policy"] = "Conservative local policy: both dimensions 720..2160; official wording does not define the measured edge"
        return model, caps, None
    if "capabilities_path" not in plan:
        _fail("model", f"{model!r} requires a verified capability sidecar; no guessed model or duration")
    path = _local_path(plan["capabilities_path"], base, "capabilities_path")
    caps, raw = _read_json(path)
    if caps.get("schema_version") != "chain5.model-capabilities.v1" or caps.get("model") != model:
        _fail("capabilities_path", "schema/model must match the exact chosen model")
    _verified_evidence(caps, "capability sidecar")
    _https(caps.get("source"), "capability sidecar.source")
    durations = caps.get("durations")
    if not isinstance(durations, list) or not durations:
        _fail("capability sidecar.durations", "must be an explicit non-empty array")
    for duration in durations:
        _integer(duration, "capability sidecar.durations[]")
    ratios = caps.get("ratios")
    if not isinstance(ratios, list) or not ratios or any(not isinstance(x, str) for x in ratios):
        _fail("capability sidecar.ratios", "must be an explicit non-empty string array")
    _integer(caps.get("prompt_max_chars"), "capability sidecar.prompt_max_chars")
    _integer(caps.get("max_images"), "capability sidecar.max_images", minimum=0)
    blocked_formats = caps.get("unsupported_image_formats", [])
    if not isinstance(blocked_formats, list) or any(not isinstance(x, str) or not x.strip() for x in blocked_formats):
        _fail("capability sidecar.unsupported_image_formats", "must be an explicit array of format strings")
    caps["unsupported_image_formats"] = [x.lower() for x in blocked_formats]
    count = _integer(caps.get("max_reference_videos"), "capability sidecar.max_reference_videos", minimum=0)
    if count:
        video = caps.get("video")
        if not isinstance(video, dict):
            _fail("capability sidecar.video", "verified video limits are required")
        for field in ("max_duration_seconds", "min_fps", "max_fps", "min_dimension", "max_dimension", "max_file_bytes"):
            _number(video.get(field), f"capability sidecar.video.{field}", positive=True)
        if video["min_fps"] > video["max_fps"] or video["min_dimension"] > video["max_dimension"]:
            _fail("capability sidecar.video", "minimum limits must not exceed maximum limits")
    audio_count = _integer(caps.get("max_reference_audios", 0), "capability sidecar.max_reference_audios", minimum=0)
    if audio_count:
        audio = caps.get("audio")
        if not isinstance(audio, dict) or audio.get("transport_field") != "reference_audios":
            _fail("capability sidecar.audio", "explicit verified reference_audios transport contract is required")
        _verified_evidence(audio, "capability sidecar.audio")
        for field in ("min_duration_seconds", "max_duration_seconds", "max_file_bytes"):
            _number(audio.get(field), f"capability sidecar.audio.{field}", positive=True)
        if audio["min_duration_seconds"] > audio["max_duration_seconds"]:
            _fail("capability sidecar.audio", "minimum duration exceeds maximum")
    return model, caps, {"path": str(path), "sha256": _digest(raw)}


def _media(items: Any, base: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(items, list):
        _fail("media", "must be an array of explicit media mappings")
    mapping, records = {}, []
    for index, item in enumerate(items):
        where = f"media[{index}]"
        if not isinstance(item, dict):
            _fail(where, "must be an object")
        media_id = _identifier(item.get("id"), f"{where}.id")
        if media_id in mapping:
            _fail(where, "duplicate media id")
        kind = item.get("type")
        if not isinstance(kind, str) or kind not in EXTENSIONS:
            _fail(f"{where}.type", "must equal image, video or audio")
        roles = item.get("roles")
        if not isinstance(roles, list) or not roles:
            _fail(f"{where}.roles", "must explicitly record at least one agent-decided role")
        for role in roles:
            _text(role, f"{where}.roles[]")
        record = {"id": media_id, "type": kind, "roles": roles, "source": item.get("source")}
        if item.get("source") == "file":
            path = _local_path(item.get("path"), base, f"{where}.path")
            if path.suffix.lower() not in EXTENSIONS[kind]:
                _fail(where, f"extension does not match declared {kind} type")
            try:
                stat = path.stat()
                if not path.is_file() or not os.access(path, os.R_OK):
                    _fail(where, "source must be a readable regular file")
                if not 0 < stat.st_size <= MAX_FILE_BYTES:
                    _fail(where, "file must contain bytes and be <=100 MiB")
                digest = _file_digest(path)
                record.update(path=str(path), sha256=digest, size_bytes=stat.st_size)
                if kind == "video":
                    record["probe"] = _probe(path)
                elif kind == "audio":
                    record["probe"] = _probe_audio(path)
                else:
                    record["image_format"] = _image_format(path)
                after = path.stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                    _fail(where, "source changed during preflight; retry with stable media")
            except OSError as exc:
                _fail(where, f"source does not exist or is not readable: {exc}")
            transport = {"source": "file", "path": str(path), "sha256": digest}
        elif item.get("source") == "url":
            url = _https(item.get("url"), f"{where}.url")
            if Path(urlparse(url).path).suffix.lower() not in EXTENSIONS[kind]:
                _fail(where, f"URL extension does not match declared {kind} type")
            record["url"] = url
            verified = item.get("verified_metadata")
            if kind in {"video", "audio"} or verified is not None:
                if not isinstance(verified, dict):
                    _fail(where, f"URL {kind} requires a valid verified_metadata object; local-only cannot inspect remote media")
                _verified_evidence(verified, f"{where}.verified_metadata")
                if verified.get("source_url") != url:
                    _fail(where, "verified_metadata.source_url must bind the exact HTTPS URL")
                if not SHA_RE.fullmatch(str(verified.get("sha256", ""))):
                    _fail(where, "verified_metadata.sha256 must bind the verified media bytes")
                record["verified_metadata"] = verified
                record["sha256"] = verified["sha256"]
                if kind == "video":
                    record["probe"] = {key: verified.get(key) for key in ("duration", "fps", "width", "height", "size_bytes", "audio_stream_count")}
                elif kind == "audio":
                    record["probe"] = {key: verified.get(key) for key in ("duration", "sample_rate", "channels", "audio_stream_count", "size_bytes")}
                else:
                    record["image_format"] = _text(verified.get("image_format"), f"{where}.verified_metadata.image_format").lower()
                    size = _integer(verified.get("size_bytes"), f"{where}.verified_metadata.size_bytes")
                    if size > MAX_FILE_BYTES:
                        _fail(where, "verified URL image must be <=100 MiB")
                    record["size_bytes"] = size
            transport = {"source": "url", "url": url}
        else:
            _fail(f"{where}.source", "must equal file or url")
        if "sha256" in item and item["sha256"] != record.get("sha256"):
            _fail(where, "source bytes do not match agent-supplied sha256")
        mapping[media_id] = {"type": kind, "transport": transport, "record": record}
        records.append(record)
    return mapping, records


def _check_video(record: dict[str, Any], caps: dict[str, Any], where: str) -> None:
    probe = record.get("probe", {})
    duration = _number(probe.get("duration"), f"{where}.duration", positive=True)
    fps = _number(probe.get("fps"), f"{where}.fps", positive=True)
    width = _integer(probe.get("width"), f"{where}.width")
    height = _integer(probe.get("height"), f"{where}.height")
    size = _integer(probe.get("size_bytes"), f"{where}.size_bytes")
    limits = caps.get("video", {})
    maximum_duration = min(15, limits.get("max_duration_seconds", 15))
    if duration > maximum_duration + 0.000001:
        _fail(where, f"reference video duration {duration:g}s exceeds {maximum_duration:g}s; provide an explicit prepared clip, no automatic retime")
    if size > min(MAX_FILE_BYTES, limits.get("max_file_bytes", MAX_FILE_BYTES)):
        _fail(where, "reference video exceeds the per-file 100 MiB or verified model limit")
    if (("min_fps" in limits and fps < limits["min_fps"])
            or ("max_fps" in limits and fps > limits["max_fps"])):
        _fail(where, f"reference fps {fps:g} is outside verified {limits['min_fps']}–{limits['max_fps']}; explicitly prepare a legal clip")
    if (("min_dimension" in limits and min(width, height) < limits["min_dimension"])
            or ("max_dimension" in limits and max(width, height) > limits["max_dimension"])):
        _fail(where, f"reference dimensions {width}x{height} violate the conservative local policy for both dimensions; explicitly prepare a legal clip")



def _check_audio(record: dict[str, Any], caps: dict[str, Any], where: str) -> None:
    probe = record.get("probe", {})
    duration = _number(probe.get("duration"), f"{where}.duration", positive=True)
    _integer(probe.get("sample_rate"), f"{where}.sample_rate")
    channels = _integer(probe.get("channels"), f"{where}.channels")
    count = _integer(probe.get("audio_stream_count"), f"{where}.audio_stream_count")
    size = _integer(probe.get("size_bytes"), f"{where}.size_bytes")
    limits = caps.get("audio", {})
    minimum, maximum = max(1, limits.get("min_duration_seconds", 1)), min(15, limits.get("max_duration_seconds", 15))
    if duration < minimum - 0.000001 or duration > maximum + 0.000001:
        _fail(where, f"audio duration must satisfy local {minimum:g}..{maximum:g} seconds; prepare a clean natural sample without retiming")
    if count != 1 or channels > 2:
        _fail(where, "voice audio must contain one mono/stereo audio stream")
    if size > min(MAX_FILE_BYTES, limits.get("max_file_bytes", MAX_FILE_BYTES)):
        _fail(where, "reference audio exceeds per-file 100 MiB or verified model limit")


def _voice_replacement(metadata: dict[str, Any], mapping: dict[str, Any]) -> str | None:
    voice = metadata.get("voice")
    if not isinstance(voice, dict) or voice.get("action") != "replace":
        return None
    _text(voice.get("target_presentation"), "metadata.voice.target_presentation")
    reference_id = _identifier(voice.get("reference_media_id"), "metadata.voice.reference_media_id")
    if voice.get("approved") is not True:
        _fail("metadata.voice.approved", "the exact target voice sample must be approved before final voice replacement")
    _text(voice.get("approval_evidence"), "metadata.voice.approval_evidence")
    if reference_id not in mapping or mapping[reference_id]["type"] != "audio":
        _fail("metadata.voice.reference_media_id", "must identify actual reference audio")
    record = mapping[reference_id]["record"]
    if "voice_identity" not in record["roles"]:
        _fail("metadata.voice.reference_media_id", "approved sample must have role voice_identity")
    if (not SHA_RE.fullmatch(str(voice.get("reference_sha256", "")))
            or voice["reference_sha256"] != record.get("sha256")):
        _fail("metadata.voice.reference_sha256", "must bind the exact approved target voice bytes")
    if voice.get("method", "reference_audio") != "reference_audio":
        _fail("metadata.voice.method", "only reference_audio is supported here; local pitch shifting is not natural voice replacement")
    return reference_id

def _check_image(record: dict[str, Any], caps: dict[str, Any], where: str) -> None:
    blocked = caps.get("unsupported_image_formats", [])
    locator = record.get("path") or urlparse(record.get("url", "")).path
    extension_format = Path(locator).suffix.lower().lstrip(".")
    if record.get("image_format") in blocked or extension_format in blocked:
        rejected_format = record.get("image_format") if record.get("image_format") in blocked else extension_format
        _fail(where, f"chosen effective_model {caps['model']} does not support image format {rejected_format.upper()}; explicitly prepare a supported still image")


def _ids(value: Any, mapping: dict[str, Any], kind: str, where: str) -> list[str]:
    if not isinstance(value, list):
        _fail(where, "must be an ordered array of media IDs")
    if len(value) != len(set(str(x) for x in value)):
        _fail(where, "duplicate media mapping")
    for media_id in value:
        _identifier(media_id, where)
        if media_id not in mapping:
            _fail(where, f"unknown media id {media_id!r}")
        if mapping[media_id]["type"] != kind:
            _fail(where, f"media {media_id!r} type must be {kind}")
    return value


def _build(plan_path: Path) -> tuple[bytes, bytes, dict[str, Any]]:
    plan_path = _local_path(str(plan_path), Path.cwd(), "plan path")
    plan, raw = _read_json(plan_path)
    if plan.get("schema_version") != "chain5.package-plan.v1":
        _fail("schema_version", "must equal chain5.package-plan.v1")
    job_id = _identifier(plan.get("job_id"), "job_id")
    revision = _identifier(plan.get("revision"), "revision")
    mode = plan.get("mode")
    if not isinstance(mode, str) or mode not in {"auto", "storyboard", "video", "image"}:
        _fail("mode", "must equal auto, storyboard, video or image")
    selected = plan.get("selected_mode") if mode == "auto" else mode
    if not isinstance(selected, str) or selected not in {"storyboard", "video", "image"}:
        _fail("selected_mode", "auto requires an explicit agent-decided storyboard, video or image mode")
    if mode != "auto" and "selected_mode" in plan and plan["selected_mode"] != mode:
        _fail("selected_mode", "cannot contradict an explicit mode")
    metadata = plan.get("metadata", {})
    if not isinstance(metadata, dict):
        _fail("metadata", "must be a local-only object")
    if set(plan) & UNSUPPORTED_TRANSPORT:
        _fail("plan", "unsupported v1 transport fields; use explicit local media IDs and metadata")
    model, caps, cap_fingerprint = _capabilities(plan, plan_path.parent)
    mapping, media_records = _media(plan.get("media"), plan_path.parent)
    approved_voice_id = _voice_replacement(metadata, mapping)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        _fail("tasks", "must be a non-empty ordered array")
    result_tasks, seen = [], set()
    previous_end = 0.0
    for index, task in enumerate(tasks):
        where = f"tasks[{index}]"
        if not isinstance(task, dict):
            _fail(where, "must be an object")
        if set(task) & UNSUPPORTED_TRANSPORT:
            _fail(where, "unsupported v1 transport fields; use explicit local media IDs and metadata")
        task_id = _identifier(task.get("id"), f"{where}.id")
        if task_id in seen:
            _fail(where, "duplicate task id")
        seen.add(task_id)
        if _integer(task.get("order"), f"{where}.order") != index + 1:
            _fail(where, "order must match tasks array position, starting at 1")
        if "model" in task:
            _fail(where, "set the chosen exact model at plan.model; task overrides are outside this minimal plan schema")
        duration = _integer(task.get("duration"), f"{where}.duration")
        if duration > 15 or duration not in caps["durations"]:
            _fail(f"{where}.duration", f"chosen effective_model {model} allows {caps['durations']} seconds within v1 1..15")
        start = _number(task.get("start"), f"{where}.start")
        end = _number(task.get("end"), f"{where}.end")
        if not math.isclose(start, previous_end, abs_tol=0.000001) or end <= start:
            _fail(where, "target timeline must start at 0 and be contiguous in task order")
        if "output_trim" in task:
            trim = task["output_trim"]
            if not isinstance(trim, dict) or trim.get("approved") is not True:
                _fail(f"{where}.output_trim", "requires explicit approved=true and approval evidence")
            _text(trim.get("approval_evidence"), f"{where}.output_trim.approval_evidence")
            trim_start = _number(trim.get("start"), f"{where}.output_trim.start")
            trim_end = _number(trim.get("end"), f"{where}.output_trim.end")
            if trim_start < 0 or trim_end > duration or trim_end <= trim_start:
                _fail(f"{where}.output_trim", "must be a non-empty range within the provider output duration")
            mapped_duration = trim_end - trim_start
        else:
            mapped_duration = duration
        if not math.isclose(end - start, mapped_duration, abs_tol=0.000001):
            _fail(where, "duration and target timeline differ; provide an explicit approved output_trim mapping")
        previous_end = end
        prompt = _text(task.get("prompt"), f"{where}.prompt")
        limit = min(5000, caps["prompt_max_chars"])
        if len(prompt) > limit:
            _fail(f"{where}.prompt", f"must contain at most {limit} characters")
        ratio = task.get("ratio")
        if not isinstance(ratio, str) or ratio not in V1_RATIOS or ratio not in caps["ratios"]:
            _fail(f"{where}.ratio", "must satisfy both v1 (16:9, 9:16, 1:1) and verified model ratios")
        image_ids = _ids(task.get("image_ids"), mapping, "image", f"{where}.image_ids")
        video_ids = _ids(task.get("reference_video_ids"), mapping, "video", f"{where}.reference_video_ids")
        audio_ids = _ids(task.get("reference_audio_ids", []), mapping, "audio", f"{where}.reference_audio_ids")
        if len(image_ids) > min(4, caps["max_images"]):
            _fail(where, "v1 permits at most 4 images and must satisfy verified model capacity")
        if selected in {"storyboard", "image"} and video_ids:
            _fail(where, f"explicit {selected} mode cannot transmit video references")
        if selected == "video" and not video_ids:
            _fail(where, "video mode requires an explicit video mapping; the tool cannot add one")
        if video_ids and caps["max_reference_videos"] == 0:
            _fail(where, f"chosen effective_model {model} does not support reference videos; the tool will not switch an explicit model")
        if len(video_ids) > min(1, caps["max_reference_videos"]):
            _fail(where, "conservative policy permits at most one reference video per task")
        if audio_ids and not caps.get("max_reference_audios", 0):
            _fail(where, f"chosen effective_model {model} does not support verified reference audio transport")
        if audio_ids and model not in {DEFAULT_MODEL, DEFAULT_VIDEO_MODEL}:
            _fail(where, "current Jimmy executor only accepts reference audio for exact Mini/Mini-video models; a capability sidecar cannot extend executor transport")
        if len(audio_ids) > min(1, caps.get("max_reference_audios", 0)):
            _fail(where, "local policy permits at most one reference audio per task")
        if audio_ids and not image_ids and not video_ids:
            _fail(where, "audio reference requires an accompanying image or video visual reference")
        for audio_id in audio_ids:
            _check_audio(mapping[audio_id]["record"], caps, f"{where}.reference_audio_ids[{audio_id}]")
        if approved_voice_id is not None:
            voice_required = task.get("voice_required", True)
            if not isinstance(voice_required, bool):
                _fail(f"{where}.voice_required", "must be explicit boolean for speaking versus silent/B-roll")
            if voice_required and audio_ids != [approved_voice_id]:
                _fail(where, "voice replacement speaking task must transmit the exact approved reference audio")
            if not voice_required and audio_ids:
                _fail(where, "explicit silent/B-roll task cannot transmit spoken voice reference audio")
            for video_id in video_ids:
                stream_count = mapping[video_id]["record"].get("probe", {}).get("audio_stream_count")
                if isinstance(stream_count, bool) or not isinstance(stream_count, int) or stream_count != 0:
                    _fail(where, "voice replacement reference video must be muted with zero audio streams; quiet samples do not remove source identity")
        for image_id in image_ids:
            _check_image(mapping[image_id]["record"], caps, f"{where}.image_ids[{image_id}]")
        for video_id in video_ids:
            _check_video(mapping[video_id]["record"], caps, f"{where}.reference_video_ids[{video_id}]")
        output_task = {"id": task_id, "model": model, "prompt": prompt, "duration": duration, "ratio": ratio,
                       "images": [mapping[x]["transport"] for x in image_ids],
                       "reference_videos": [mapping[x]["transport"] for x in video_ids]}
        if audio_ids:
            output_task["reference_audios"] = [mapping[x]["transport"] for x in audio_ids]
        result_tasks.append(output_task)
    preview = _bytes({"schema_version": "jimmy-video-job.v1", "job_id": job_id, "tasks": result_tasks})
    handoff = _bytes({"schema_version": "seedance-jimmy-handoff.v1", "bundle_id": f"{job_id}-{revision}",
                      "state": "ready_for_confirmation", "executor_skill": "jimmy-video-generator",
                      "job_preview_path": "jimmy-job.preview.json", "job_sha256": _digest(preview),
                      "task_summary": [{"id": x["id"], "duration": x["duration"], "ratio": x["ratio"],
                                        "image_count": len(x["images"])} for x in result_tasks]})
    ready = {"schema_version": "chain5-ready.v1", "state": "ready_for_confirmation",
             "job_id": job_id, "revision": revision, "plan_path": str(plan_path),
             "plan_sha256": _digest(raw), "media_sha256": _digest(_bytes(media_records)),
             "prompt_sha256": _digest(_bytes([{ "id": x["id"], "prompt": x["prompt"]} for x in result_tasks])),
             "job_sha256": _digest(preview), "handoff_sha256": _digest(handoff),
             "effective_mode": selected, "effective_model": model, "capabilities": caps,
             "capability_sidecar": cap_fingerprint, "media": media_records,
             "tasks": tasks, "metadata": metadata}
    return preview, handoff, ready


def verify(output: Path, plan_path: Path | None = None) -> dict[str, Any]:
    """Rebuild all local fingerprints and reject consumed, cancelled or changed previews."""
    output = Path(output).resolve()
    if (output / "jimmy-job.json").exists():
        _fail(str(output), "existing frozen jimmy-job.json cannot be reused; use a new output/revision")
    if not all((output / name).is_file() for name in FILES):
        _fail(str(output), "missing or partial package; use a new output/revision")
    handoff, handoff_raw = _read_json(output / FILES[1])
    if handoff.get("state") != "ready_for_confirmation":
        _fail("handoff.state", "not pending; cancelled/confirmed/consumed/invalidated records require a new output/revision")
    ready, ready_raw = _read_json(output / FILES[2])
    preview_raw = (output / FILES[0]).read_bytes()
    if handoff.get("job_sha256") != _digest(preview_raw):
        _fail("handoff.job_sha256", "preview hash mismatch; use a new output/revision")
    if ready.get("schema_version") != "chain5-ready.v1" or ready.get("state") != "ready_for_confirmation":
        _fail("chain5-ready", "unknown schema or invalid readiness state; use a new output/revision")
    saved_plan = _local_path(ready.get("plan_path"), output, "chain5-ready.plan_path")
    chosen_plan = saved_plan if plan_path is None else Path(plan_path).resolve()
    if chosen_plan != saved_plan:
        _fail("plan_path", "plan path changed; use a new output/revision")
    _, plan_raw = _read_json(chosen_plan)
    if ready.get("plan_sha256") != _digest(plan_raw):
        _fail("plan", "plan bytes changed; use a new output/revision")
    expected_preview, expected_handoff, expected_ready = _build(chosen_plan)
    if ready.get("media_sha256") != expected_ready["media_sha256"]:
        _fail("media", "media fingerprint changed, even at the same path; use a new output/revision")
    if preview_raw != expected_preview or handoff_raw != expected_handoff or ready_raw != _bytes(expected_ready):
        _fail("package", "fingerprint or handoff hash mismatch; use a new output/revision")
    return {"verified": True, "output": str(output), "job_id": ready["job_id"],
            "revision": ready["revision"], "state": "ready_for_confirmation", "remote_executed": False}


def prepare(plan_path: Path, output: Path) -> dict[str, Any]:
    """Write only fresh local previews, or verify an identical still-pending package."""
    plan_path = Path(plan_path).resolve()
    output = Path(output).resolve()
    preview, handoff, ready = _build(plan_path)
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".chain5-prepare.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _fail(str(output), "preparation lock already exists; do not retry or overwrite another package")
    created = []
    try:
        if (output / "jimmy-job.json").exists() or any((output / name).exists() for name in FILES):
            return verify(output, plan_path)
        for name, raw in zip(FILES, (preview, handoff, _bytes(ready))):
            path = output / name
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created.append(path)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        return verify(output, plan_path)
    except BaseException:
        # Remove only files created by this attempt; never rewrite old approvals.
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="Prepare local pending preview files only")
    prepare_parser.add_argument("--plan", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify", help="Verify unchanged local pending files and source media")
    verify_parser.add_argument("--output", type=Path, required=True)
    verify_parser.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.plan, args.output) if args.command == "prepare" else verify(args.output, args.plan)
    except (PackageError, OSError) as exc:
        print(f"chain5 package rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
