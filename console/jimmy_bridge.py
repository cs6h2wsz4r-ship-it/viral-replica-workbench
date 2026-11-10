"""Steps 04–06: wire the console to the user's 链路五 skills (unchanged, vendored under engine/skills).

* chain5_package.py prepare/verify builds the exact jimmy-video-job.v1 preview + hash-bound handoff.
* The browser shows that exact preview; clicking 确认生成 is the 链路五 “确认” gate, bound to job_sha256.
* jimmy_cli.py generate/resume does upload → create → poll → download with its own idempotent state.json.
* chain5_image.py prepare/submit/resume generates missing target-state images through Jimmy (gpt-image-2).
"""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, gateway, jobs, media, motion, openai_image, remote_query, replacement_guard, scene_plan, segment_timing, store
from .channel_text import technical_detail, user_message, third_party_message

MINI = "seedance2.0-mini"
MINI_VIDEO = "seedance2.0-mini-video"
UPLOAD_RETRY_DELAYS = (10, 30, 60)
UNSAFE_RESUBMIT = "已经到达或可能到达创建任务这一步，可能已经扣费，不能重新提交；请用刷新/恢复"
IMAGE_REFERENCE_LIMITS = {"official": 16, "third_party": 10}


class BridgeError(gateway.GatewayError):
    def __init__(self, message: str, *, issues: list[dict] | None = None):
        super().__init__(third_party_message(message))
        self.technical_detail = technical_detail(message)
        from .gateway.errors import sanitize
        self.technical = sanitize(message)
        self.issues = issues


def _script(name: str) -> Path:
    path = (config.JIMMY_SCRIPTS if name == "jimmy_cli.py" else config.CHAIN5_SCRIPTS) / name
    if not path.exists():
        raise BridgeError("工作台文件不完整：缺少 engine/skills 下的对接脚本，请重新下载本项目")
    return path


def _call(args: list[str], cwd: Path, timeout: float = 600) -> str:
    proc = subprocess.run([sys.executable, *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise BridgeError(msg[-1] if msg else f"命令失败（退出码 {proc.returncode}）")
    return proc.stdout


def _record_error(record: dict, exc: object, *, field: str = "error") -> None:
    record[field] = third_party_message(exc)
    detail = technical_detail(getattr(exc, "technical_detail", exc))
    if detail != record[field]:
        previous = record.setdefault("previous_errors", [])
        if not previous:
            previous.append(detail)
        elif not any(detail in item for item in previous):
            # Keep one history entry per retry; attach terminal diagnostics to
            # the final attempt instead of inventing an extra failed attempt.
            previous[-1] += "\n" + detail


def _task(data: dict, tid: str) -> dict:
    for t in (data.get("script") or {}).get("tasks", []):
        if t["id"] == tid:
            return t
    for segment in (data.get("director") or {}).get("segments", []):
        if segment.get("id") == tid:
            return {**segment, "kind": "director", "ratio": "9:16", "mute_clip": True}
    raise BridgeError(f"找不到分段 {tid}")


def _role_text(asset: dict) -> str:
    roles = set(asset.get("roles") or [])
    if "target_state" in roles:
        return "main camera image: the visible person, setting, framing and lighting to preserve"
    if "person_identity" in roles:
        return "person identity: face, hair, body, outfit and accessories come from this image"
    if "product_truth" in roles:
        return "product truth: preserve its colors, structure, packaging text and real proportions"
    if "scene" in roles:
        return "setting / composition reference"
    if "storyboard" in roles:
        return "storyboard for shot order and composition; do not show it as a collage"
    return "reference"


def max_image_references(channel: str, model: str | None = None) -> int:
    """Image edit limits are separate from video's four-reference limit."""
    return IMAGE_REFERENCE_LIMITS.get(channel, 10)


def scene_image_references(data: dict, scene_id: str, *, at: float | None = None) -> dict:
    """Resolve the exact image-edit order, without the final source frame."""
    if scene_id not in {s["id"] for s in replacement_guard.scenes(data)}:
        raise BridgeError("当前项目没有这个场景，请刷新分场表")
    selected = [s for s in scene_plan.effective_subjects(data, scene_id) if s["on"]]
    assets = {a["id"]: a for a in data.get("assets", [])}
    for row in selected:
        if not row.get("asset_id") or assets.get(row["asset_id"], {}).get("type") != "image":
            raise BridgeError(f"请先为这一场的{row.get('name') or '对象'}选择照片，再生成预览")
    people = [s for s in selected if s["kind"] == "character"]
    product = next((s for s in selected if s["kind"] == "product"), {})
    ids = [p["asset_id"] for p in people] + ([product["asset_id"]] if product else [])
    anchor_id = None
    if len(people) == 1:
        person = people[0]
        cid = person["source_id"]
        first = replacement_guard.first_scene(data, cid)
        ordered = sorted(replacement_guard.scenes(data), key=lambda s: s["id"] != first)
        for other in ordered:
            sid = other["id"]
            if sid == scene_id:
                continue
            other_people = [s for s in scene_plan.effective_subjects(data, sid)
                            if s["kind"] == "character" and s["on"]]
            if len(other_people) != 1 or other_people[0]["source_id"] != cid or other_people[0].get("asset_id") != person.get("asset_id"):
                continue
            aid = scene_plan.camera_image_for(data, sid)
            if not aid:
                aid = replacement_guard.camera_image_for(data, cid, sid)
            if aid in assets:
                anchor_id = aid
                ids.append(aid)
                break
    return {"image_ids": ids, "subjects": selected, "anchor_asset_id": anchor_id,
            "product_id": product.get("asset_id")}


def compose_prompt(data: dict, task: dict, directory: Path | None = None) -> str:
    """Assemble the request with Hypit's Seedance kit wording. Reference tokens follow actual input order."""
    if scene_plan.enabled(data):
        from . import scene_prompt
        return scene_prompt.compose(data, task, directory)
    from . import kits
    assets = {a["id"]: a for a in data.get("assets", [])}
    images = [assets[a] for a in task.get("image_ids") or [] if a in assets]
    has_video = task.get("mode") == "video"
    audio = assets.get(task.get("audio_id") or "")
    kind = task.get("kind")
    if not kind:  # scripts from older versions
        kind, task = "broll", {**task, "story": task.get("prompt", "")}
    refs = []
    for i, a in enumerate(images, start=1):
        if kind == "speaker" and i == 1:
            continue  # the kit already defines @image1
        if task.get("identity_reference_version") == 1 and has_video and a["id"] in {
                c.get("asset_id") for c in replacement_guard.replaced_people(data, task)}:
            refs.append(f"@image{i} is the identity reference of the same person: use it only for face, hair and body proportions; ignore its clothing, background and multi-panel layout. Never show the reference collage or its panels in the video.")
            continue
        scene_ref = next((r for r in task.get("scene_references") or [] if r.get("asset_id") == a["id"]), None)
        if scene_ref:
            refs.append(f"@image{i} is the same person in scene {scene_ref['scene_id']}, used from {scene_ref['start']:g} s; follow @video1 for the scene transition.")
            continue
        desc = f" ({a['description']})" if a.get("description") else ""
        refs.append(f"@image{i} \"{a['label']}\"{desc}: {_role_text(a)}.")
    if kind == "speaker":
        voice_note = (audio or {}).get("description") or ""
        blocks = kits.speaker_prompt(task.get("dialogue", ""), "" if has_video else task.get("action", ""), task.get("recipe"),
                                     has_audio=bool(audio), voice_note=voice_note, has_video=has_video,
                                     dialogue_windows=motion.dialogue_windows(data, task, directory)[0],
                                     characters=(data.get("breakdown") or {}).get("characters") or [])
        if has_video:
            blocks.insert(1, kits.SPEAKER_WITH_FOOTAGE)
    elif has_video:
        # Motion/camera kits permit free camera/body movement; video reference mode
        # must preserve both. Generic B-roll recipes also cannot override the source.
        blocks = ["Use @image1 as the subject and preserve the subject's identity, outfit, and physical features.",
                  kits.SPEAKER_WITH_FOOTAGE,
                  "Add no subtitles, captions, floating words or other on-screen text."]
        if kind == "broll":
            blocks.append("Create silent B-roll; visible people do not speak to camera.")
    elif kind == "motion":
        blocks = kits.motion_reference_prompt(task.get("direction", ""))
    elif kind == "camera":
        blocks = kits.camera_reference_prompt(task.get("direction", ""))
    else:
        blocks = kits.broll_prompt(task.get("story", ""), task.get("recipe"))
        if has_video:
            blocks.insert(1, "@video1 is source footage carrying the timing of the action and camera; do not inherit its "
                             "people, products, captions or on-screen text.")
    if refs:
        blocks.insert(1, "OTHER REFERENCES: " + " ".join(refs))
    if has_video:
        blocks.append(motion.timeline_block(motion.source_timeline(data, task)))
        performance = "\n".join(task.get(k) or "" for k in replacement_guard.TEXT_FIELDS).strip()
        if performance:
            blocks.append(motion.PERFORMANCE_LABEL + "\n" + performance)
    if task.get("start_state") or task.get("end_state"):
        blocks.append(f"CONTINUITY: begins with {task.get('start_state') or 'the reference state'}; "
                      f"ends with {task.get('end_state') or 'a natural settle'}.")
    if scene_plan.enabled(data):
        # The original kit's blanket replacement sentence is correct only for
        # global replacements. A scene may deliberately retain a visible person.
        blanket = ("Do not inherit @video1's people, faces, outfits, products, captions or any on-screen text; "
                   "the visible people, products and setting come from the image references.")
        blocks = [b.replace(blanket, "Change only the subjects listed in SCENE REPLACEMENTS below. "
            "Keep all unchecked subjects as they appear in @video1. Add no captions or on-screen text.") for b in blocks]
        ids = task.get("image_ids") or []
        rows = scene_plan.effective_subjects(data, replacement_guard.task_scene(data, task))
        source_reference = "@video1" if has_video else "the scene camera image @image1"
        notes = []
        for row in rows:
            name = f"{row['source_id']} ({row.get('name') or row['source_id']})"
            if not row["on"]:
                notes.append(f"Keep {name} as in {source_reference}: preserve its original appearance and identity.")
                continue
            aid = row.get("asset_id")
            reference = f"@image{ids.index(aid) + 1}" if aid in ids else "the scene camera image @image1"
            notes.append(f"Replace {name} using {reference}; preserve its position and role in the source scene.")
        if not any(s["kind"] == "product" and s["on"] for s in rows):
            notes.append(f"Keep the original product as in {source_reference}.")
        blocks.append("SCENE REPLACEMENTS:\n" + "\n".join(notes))
    return "\n\n".join(b for b in blocks if b.strip())


def replacement_check(data: dict, task: dict, directory: Path | None = None, *, compare_motion: bool = False) -> dict:
    """Editor checks stay local. Explicit preview additionally compares actions once."""
    result = replacement_guard.check(data, task, directory, compare_motion=compare_motion)
    cached = (data.get("action_comparisons") or {}).get(task.get("id"))
    if not compare_motion and cached and directory is not None:
        if cached.get("fingerprint") == preflight_fingerprint(data, task, directory):
            comparison = cached["comparison"]
            result["action_comparison"] = comparison
            result["warnings"] = list(dict.fromkeys(result["warnings"] + comparison.get("warnings", [])))
            replacement_guard.add_issue(result["issues"], "motion_additions", comparison.get("errors", []))
        else:
            result["action_comparison"] = {"status": "incomplete", "reason": "脚本或检查依据已变化，请重新生成预览"}
    try:
        _check_task_kind(task, data)
    except BridgeError as exc:
        result["errors"].append(str(exc))
        result["issues"].extend(exc.issues or [])
    result["issues"] = replacement_guard.order_issues(result["issues"])
    if scene_plan.enabled(data) and task.get('mode') == 'video':
        from . import scene_prompt
        comparison = result.get('action_comparison') or {}
        en = scene_prompt.valid_english(comparison.get('sourceMotionEn'), motion.source_timeline(data, task), scene_prompt.role_names(data))
        if not en and scene_prompt.ENGLISH_WARNING not in result['warnings']:
            result['warnings'].append(scene_prompt.ENGLISH_WARNING)
    replacement_guard.attach_motion_warning(data, task, result)
    return result


def dismiss_motion_warning(pid: str, tid: str, fingerprint: str) -> dict:
    def save(data):
        task = _task(data, tid)
        if fingerprint != replacement_guard.motion_warning_fingerprint(data, task):
            raise BridgeError("这一段已改过，请检查这一段后再关闭提醒")
        data.setdefault("motion_warning_dismissals", {})[tid] = fingerprint
    store.update(pid, save)
    return {"ok": True}


def _check_kind(task: dict, data: dict, directory: Path | None = None, *, compare_motion: bool = False) -> dict:
    result = replacement_check(data, task, directory, compare_motion=compare_motion)
    if result["errors"]:
        raise BridgeError("\n".join(result["errors"]), issues=result["issues"])
    return result


def _check_task_kind(task: dict, data: dict) -> None:
    def blocked(code, message):
        issues = []
        replacement_guard.add_issue(issues, code, [message])
        raise BridgeError(message, issues=issues)

    kind = task.get("kind")
    assets = {a["id"]: a for a in data.get("assets", [])}
    if kind in {"motion", "camera"} and task.get("mode") != "video":
        blocked("video_reference_required", "动作/运镜复刻段落需要原片片段（参考方式选“原片片段 + 图”）")
    if kind == "speaker":
        if not task.get("dialogue", "").strip():
            blocked("missing_dialogue", "口播段落缺少台词")
        if not task.get("image_ids"):
            return  # shared replacement check supplies the actionable empty-selection message
        first = assets.get(task["image_ids"][0])
        if not first or not set(first.get("roles") or []) & {"target_state", "person_identity"}:
            blocked("image1_not_person", "口播段落的图1必须是出镜人物（主镜头图或人物照）：Seedance 模板把 @image1 当作说话人")


def _next_rev(data: dict, tid: str, pkg_root: Path) -> str:
    n = len(data.get("runs", {}).get(tid, [])) + 1
    while (pkg_root / f"{tid}-r{n}").exists():
        n += 1
    return f"r{n}"


def preflight_fingerprint(data: dict, task: dict, directory: Path) -> str:
    values = [replacement_guard.fingerprint(data, directory), data.get("source"),
              data.get("breakdown"), (data.get("script") or {}).get("dialogue_mode"),
              {k: (data.get("script") or {}).get(k) for k in ("provider", "model", "request_model")},
              {k: (data.get("analysis") or {}).get(k) for k in ("provider", "model", "request_model")},
              motion.read_transcript(directory) if task.get("mode") == "video" else None,
              {k: v for k, v in task.items() if k not in {"chosen_rev", "replacement_safety", "replacement_safety_key", "replacement_safety_check"}}]
    if task.get("mode") == "video" and task.get("blank_captions") is True:
        # Analysis alone cannot invalidate a preview after mask rules change.
        # Reference clips are rebuilt per revision once that preview is rejected.
        masks, missing = replacement_guard.caption_masks(data, task)
        values.append({"caption_masks": masks, "missing_caption_bbox": missing})
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def verify_preview_current(data: dict, task: dict, run: dict, directory: Path) -> None:
    if run.get("kind") == "director":
        from . import director
        if director.is_legacy(data.get("director")) or run.get("director_format_version") != 3:
            raise BridgeError(director.LEGACY_WARNING)
        # The reviewed package is frozen; edits are advisory, not a replacement
        # safety/motion preflight. A mismatched reference mapping still needs a rewrite.
        if not task:
            raise BridgeError("这一段已被移除，请回到第 04 步（对照和修改）查看当前分段方案")
        if run.get('fix_plan_binding'):
            from . import fix_plan
            fix_plan.verify_video_binding(data, task['id'], run['fix_plan_binding'], directory)
        if task.get("needs_rewrite"):
            director.refresh_prompt_status(task, data)
        if task.get("needs_rewrite"):
            raise BridgeError("这一段的说明没写好，回第 04 步（对照和修改），选中这一段，在“提示词”里点“重新写这一段”。")
        return
    if not task or run.get("preflight_fingerprint") != preflight_fingerprint(data, task, directory):
        raise BridgeError("预览之后脚本、替换设定或参考素材有变化（或为旧版预览），请重新生成预览")
    _check_kind(task, data, directory)


def preview(pid: str, tid: str, *, model: str | None = None, fix_plan=None) -> dict:
    """Build plan → package; one script-model motion audit, no Jimmy generation."""
    data = store.load(pid)
    task = _task(data, tid)
    if task.get("kind") == "director":
        return director_preview(pid, tid, model=model, **({'fix_plan': fix_plan} if fix_plan is not None else {}))
    if fix_plan is not None:
        raise ValueError('修改方案只适用于导演段落')
    d = store.project_dir(pid)
    rev = _next_rev(data, tid, d / "packages")
    pkg = d / "packages" / f"{tid}-{rev}"
    try:
        return _preview(pid, tid, data, task, d, rev, pkg, **({"model":model} if model else {}))
    except Exception:
        # A failed preflight leaves nothing behind; the next attempt gets a clean revision.
        shutil.rmtree(pkg, ignore_errors=True)
        for clip in (d / "clips").glob(f"{tid}-{rev}*.mp4"):
            clip.unlink(missing_ok=True)
        raise


def _preview(pid: str, tid: str, data: dict, task: dict, d: Path, rev: str, pkg: Path, *, model: str | None = None) -> dict:
    check = replacement_check(data, task, d, compare_motion=True)
    preflight = preflight_fingerprint(data, task, d)

    def save_comparison(current):
        current_task = _task(current, tid)
        if preflight_fingerprint(current, current_task, d) != preflight:
            raise BridgeError("动作对比期间脚本或检查依据已变化，请重新生成预览")
        current.setdefault("action_comparisons", {})[tid] = {
            "fingerprint": preflight, "comparison": check["action_comparison"]}

    # Keep comparison evidence; additions are advisory, other errors still block.
    store.update(pid, save_comparison)
    data.setdefault('action_comparisons', {})[tid] = {'fingerprint': preflight, 'comparison': check['action_comparison']}
    if check["errors"]:
        raise BridgeError("\n".join(check["errors"]), issues=check["issues"])
    # Runtime muting is a local clip treatment. English evidence remains bound
    # to the saved authoring task, before that effective flag is overridden.
    prompt_task = task if scene_plan.enabled(data) else None
    task = {**task, "mute_clip": check["mute_clip"]}
    job_id = f"{pid}-{tid}-{rev}"
    pkg.mkdir(parents=True, exist_ok=False)
    assets = {a["id"]: a for a in data.get("assets", [])}

    media_items, image_ids, video_ids, audio_ids = [], [], [], []
    for aid in task.get("image_ids") or []:
        a = assets.get(aid)
        if not a:
            raise BridgeError(f"素材 {aid} 不存在")
        media_items.append({"id": aid, "type": "image", "roles": a.get("roles") or ["reference"],
                            "source": "file", "path": str((d / a["file"]).resolve()), "sha256": a["sha256"]})
        image_ids.append(aid)
    if len(image_ids) > 4:
        raise BridgeError("每段最多 4 张参考图（三方渠道 v1 限制），请减少或合成一张商品板")

    clip_info = None
    clip = None
    caption_residue = {"status": "skipped", "reason": "本段仅使用图片参考，跳过字幕残留检查", "suspected": []}
    if task.get("mode") == "video":
        if not data.get("source"):
            raise BridgeError("视频参考模式需要原片")
        src = d / data["source"]["file"]
        src_dur = data["source"]["probe"]["duration"]
        start = max(0.0, float(task.get("source_start") or 0))
        length = float(task.get("source_end") or 0) - start
        if length <= 0:
            length = task["duration"]
        length = min(15.0, length, src_dur - start)
        if length < 1:
            raise BridgeError("原片片段太短，检查本段的原片起止时间")
        clip = d / "clips" / f"{tid}-{rev}{'-muted' if task.get('mute_clip', True) else ''}.mp4"
        media.cut_clip(src, start, length, clip, mute=task["mute_clip"], masks=check["masks"])
        clip_info = media.probe(clip)
        caption_residue = {"status": "skipped", "reason": "本段未启用去字幕", "suspected": []}
        if task.get("blank_captions") is True:
            try:
                caption_residue = media.caption_residue(clip, src, check["masks"],
                    (data.get("breakdown") or {}).get("overlays") or [], pkg / "caption-residue", source_start=start)
                for sample in caption_residue.get("samples", []):
                    if sample.get("thumbnail"):
                        sample["thumbnail"] = store.rel(pid, Path(sample["thumbnail"]))
            except (media.MediaError, OSError, ValueError, ImportError, subprocess.TimeoutExpired) as exc:
                caption_residue = {"status": "unavailable", "reason": f"字幕残留检查未完成，请人工检查参考片段：{exc}",
                                   "suspected": []}
        media_items.append({"id": "source-clip", "type": "video", "roles": ["source_motion"], "source": "file",
                            "path": str(clip.resolve()), "sha256": store.sha256_file(clip)})
        video_ids.append("source-clip")

    metadata: dict = {"console": {"project": pid, "task": tid, "revision": rev,
                                  "source_range": [task.get("source_start"), task.get("source_end")]},
                      "dialogue": {"mode": (data.get("script") or {}).get("dialogue_mode", "keep")}}
    if task.get("audio_id"):
        a = assets.get(task["audio_id"])
        if not a or a["type"] != "audio":
            raise BridgeError("声线样本不存在")
        if not task.get("voice_approved"):
            raise BridgeError("使用声线样本前，请先试听并勾选“我已认可这个声线”")
        if task.get("mode") == "video" and not task.get("mute_clip", True):
            raise BridgeError("换声时参考片段必须静音（去掉原音轨）")
        media_items.append({"id": a["id"], "type": "audio", "roles": ["voice_identity"], "source": "file",
                            "path": str((d / a["file"]).resolve()), "sha256": a["sha256"]})
        audio_ids.append(a["id"])
        metadata["voice"] = {"action": "replace", "target_presentation": a.get("description") or a["label"],
                             "reference_media_id": a["id"], "reference_sha256": a["sha256"], "approved": True,
                             "approval_evidence": f"用户在工作台试听后勾选认可（{time.strftime('%Y-%m-%d %H:%M')}）",
                             "method": "reference_audio"}

    prompt = compose_prompt(data, prompt_task or task, d)
    if len(prompt) > 5000:
        raise BridgeError(f"提示词 {len(prompt)} 字，超过 5000 字上限，请精简本段描述")
    if not image_ids and not video_ids:
        raise BridgeError("至少需要一张参考图或一段参考视频")

    selected = "video" if video_ids else "image"
    duration = int(task["duration"])
    task_entry = {"id": tid, "order": 1, "start": 0, "end": duration, "duration": duration,
                  "ratio": task.get("ratio", "9:16"), "prompt": prompt,
                  "image_ids": image_ids, "reference_video_ids": video_ids}
    if audio_ids:
        task_entry["reference_audio_ids"] = audio_ids
        task_entry["voice_required"] = True
    plan = {"schema_version": "chain5.package-plan.v1", "job_id": job_id, "revision": rev,
            "mode": "auto", "selected_mode": selected, "media": media_items, "tasks": [task_entry],
            "metadata": metadata}
    if model:
        plan["model"] = model
    job, handoff = _prepare_package(plan, pkg)
    model = job["tasks"][0]["model"]
    billing = duration + (round(clip_info["duration"]) if (clip_info and model == MINI_VIDEO) else 0)
    record = {"rev": rev, "job_id": job_id, "state": "pending_confirmation", "model": model,
              "mode": selected, "job_sha256": handoff["job_sha256"], "package": store.rel(pid, pkg),
              "preview": job, "billing_seconds": billing,
              "billing_note": (f"输出 {duration}s + 参考视频 {round(clip_info['duration'])}s" if clip_info
                               else f"输出 {duration}s") + "；单价以三方渠道账户为准",
              "clip": store.rel(pid, clip) if clip_info else None,
              "caption_residue": caption_residue,
              "source_motion_timeline": check["source_motion_timeline"],
              "action_comparison": check["action_comparison"],
              "dialogue_windows": check["dialogue_windows"],
              "motion_source": {"file": data["source"]["file"],
                                "sha256": store.sha256_file(d / data["source"]["file"]),
                                "transcript": motion.read_transcript(d),
                                "dialogue_lines": task.get("dialogue_lines") or []} if clip_info else None,
              "warnings": check["warnings"],
              "clip_processing": {"masks": check["masks"], "muted": task["mute_clip"],
                                  "blank_captions": task.get("blank_captions") is True,
                                  "mute_reason": check["mute_reason"]} if clip_info else None,
              "source_range": [start, start + length] if clip_info else [task.get("source_start"), task.get("source_end")],
              "preflight_fingerprint": preflight,
              "person_replaced": bool(replacement_guard.replaced_people(data, task)),
              "created": time.strftime("%H:%M:%S")}
    # Any older pending preview for this task is superseded.
    def fn(x: dict) -> None:
        runs = x.setdefault("runs", {}).setdefault(tid, [])
        for r in runs:
            if r["state"] == "pending_confirmation":
                r["state"] = "superseded"
        runs.append(record)
    store.update(pid, fn)
    return record


def _prepare_package(plan: dict, pkg: Path) -> tuple[dict, dict]:
    """Shared, local-only package handoff; keeps the legacy plan bytes unchanged."""
    plan_path = pkg / "plan.json"
    store.atomic_write_json(plan_path, plan)
    review = pkg / "review"
    try:
        _call([str(_script("chain5_package.py")), "prepare", "--plan", str(plan_path), "--output", str(review)], pkg)
    except BridgeError as exc:
        raise BridgeError(f"生成前的检查没通过：{exc}") from None
    return (json.loads((review / "jimmy-job.preview.json").read_bytes()),
            json.loads((review / "jimmy-handoff.json").read_text(encoding="utf-8")))


def director_preview(pid: str, seg_id: str, *, model: str | None = None, fix_plan=None) -> dict:
    """Package the authored prompt and ordered full-frame images verbatim."""
    from . import director
    with store._lock(pid):
        data = store.load(pid)
        if director.is_legacy(data.get("director") or {}):
            raise BridgeError(director.LEGACY_WARNING)
        segment = director.get_segment(data, seg_id)
        directory = store.project_dir(pid)
        try:
            director.require_ready(data, directory, seg_id)
        except (ValueError, OSError, KeyError) as exc:
            raise BridgeError(str(exc)) from None
        if segment.get("inputs_sig") != director.segment_inputs_sig(data, directory, seg_id):
            raise BridgeError("这一段的新分镜或分段设定改过，回第 04 步（对照和修改），选中这一段，在“提示词”里重新写这一段，再生成预览。")
        director.refresh_prompt_status(segment, data)
        prompt = segment.get("prompt")
        problem = segment.get("rewrite_reason")
        if problem:
            raise BridgeError("这一段的说明没写好：" + problem + "；回第 04 步（对照和修改），选中这一段，在“提示词”里修改并保存，或重新写这一段。")
        if segment.get("needs_rewrite"):
            raise BridgeError("这一段的说明没写好，回第 04 步（对照和修改），选中这一段，在“提示词”里点“重新写这一段”。")
        from . import fix_plan as plans
        fix_binding = plans.video_binding(data, seg_id, directory, marker=fix_plan)
        keys = segment.get("images") or []
        if not keys or len(keys) > 4 or len(set(keys)) != len(keys):
            raise BridgeError("每段提交 1–4 张图片，不能重复；回第 04 步（对照和修改）重新生成分段方案。")
        if segment.get("mode") not in {"image", "video"}:
            raise BridgeError("请选择原片视频 + 目标画面或只用图片")
        duration = segment.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
            raise BridgeError("生成时长必须是 4–15 的整数秒")
        mapping = {key: segment[key] for key in ("source_start", "source_end", "source_seconds", "target_seconds", "stretch")}
        directory = store.project_dir(pid)
        refs = director.references(data, segment, directory)
        rev = _next_rev(data, seg_id, directory / "packages")
        pkg = directory / "packages" / f"{seg_id}-{rev}"
        clip = None
        try:
            pkg.mkdir(parents=True, exist_ok=False)
            frozen_dir = pkg / "references"
            frozen_dir.mkdir()
            media_items, image_ids, reference_views = [], [], []
            for index, ref in enumerate(refs, 1):
                source = (directory / ref["file"]).resolve()
                if not source.is_relative_to(directory.resolve()) or not source.is_file():
                    raise BridgeError("参考图文件缺失，请重新选择照片")
                frozen = frozen_dir / f"image{index}{source.suffix.lower()}"
                shutil.copyfile(source, frozen)
                aid = f"image{index}"
                media_items.append({"id": aid, "type": "image", "roles": ref.get("roles") or ["reference"],
                                    "source": "file", "path": str(frozen.resolve()), "sha256": store.sha256_file(frozen)})
                image_ids.append(aid)
                reference_views.append({**{k: ref[k] for k in ('key', 'cell', 'role', 'roles', 'description', 'duty') if k in ref},
                                        "label": f"图{index}", "file": store.rel(pid, frozen),
                                        "id": aid, "tag": f"图{index}"})
            video_ids, masks, warnings = [], [], list(segment.get("warnings") or [])
            clip_info = None
            residue = {"status": "skipped", "reason": "本段仅使用图片参考，跳过字幕残留检查", "suspected": []}
            from . import segment_boards
            remove_text = (segment_boards.state_for(data, seg_id).get("plan") or {}).get("remove_text") is True
            if segment["mode"] == "video":
                import math
                source_data = data.get("source") or {}
                src = (directory / source_data.get("file", "")).resolve()
                if not src.is_relative_to(directory.resolve()) or not src.is_file():
                    raise BridgeError("原片文件找不到了")
                start, end = float(segment["source_start"]), float(segment["source_end"])
                length = end - start
                if not all(math.isfinite(n) for n in (start, end)) or start < 0 or not 0 < length <= 15:
                    raise BridgeError("原片时间范围不正确，每段不能超过 15 秒")
                masks, missing = replacement_guard.caption_masks(data, {**segment, "blank_captions": remove_text})
                if missing:
                    warnings.append("原片里有文字没标位置，遮罩可能没遮全，请检查参考片段")
                clip = directory / "clips" / f"{seg_id}-{rev}-muted.mp4"
                source_clip = pkg / "source-cut.mp4"
                media.cut_clip(src, start, length, source_clip, mute=True, masks=masks)
                residue = {"status": "skipped", "reason": "本段未启用去字幕", "suspected": []}
                if remove_text:
                    try:
                        residue = media.caption_residue(source_clip, src, masks, (data.get("breakdown") or {}).get("overlays") or [],
                                                        pkg / "caption-residue", source_start=start)
                        for sample in residue.get("samples", []):
                            if sample.get("thumbnail"):
                                sample["thumbnail"] = store.rel(pid, Path(sample["thumbnail"]))
                    except (media.MediaError, OSError, ValueError, ImportError, subprocess.TimeoutExpired) as exc:
                        residue = {"status": "unavailable", "reason": f"字幕残留检查未完成，请人工检查参考片段：{exc}", "suspected": []}
                clip_info = media.stretch_clip(source_clip, length, duration, clip)
                media_items.append({"id": "source-clip", "type": "video", "roles": ["source_motion"], "source": "file",
                                    "path": str(clip.resolve()), "sha256": store.sha256_file(clip)})
                video_ids.append("source-clip")
            job_id = f"{pid}-{seg_id}-{rev}"
            task = {"id": seg_id, "order": 1, "start": 0, "end": duration, "duration": duration,
                    "ratio": "9:16", "prompt": prompt, "image_ids": image_ids, "reference_video_ids": video_ids}
            plan = {"schema_version": "chain5.package-plan.v1", "job_id": job_id, "revision": rev,
                    "mode": "auto", "selected_mode": segment["mode"], "media": media_items, "tasks": [task],
                    "metadata": {"console": {"project": pid, "task": seg_id, "revision": rev, "kind": "director",
                                               "source_range": [segment["source_start"], segment["source_end"]], **mapping}}}
            if model:
                plan["model"] = model
            job, handoff = _prepare_package(plan, pkg)
            effective_model = job["tasks"][0]["model"]
            billing = duration + (duration if clip_info and effective_model == MINI_VIDEO else 0)
            from . import segment_review
            review_inputs = segment_review.freeze_inputs(data, segment, reference_views, directory, pkg)
            record = {"rev": rev, "job_id": job_id, "kind": "director", "director_format_version": 3,
                      "director_sig": director.segment_sig(segment),
                      "review_inputs": review_inputs,
                      "state": "pending_confirmation", "model": effective_model, "mode": segment["mode"],
                      "job_sha256": handoff["job_sha256"], "package": store.rel(pid, pkg), "preview": job,
                      "references": reference_views, "billing_seconds": billing,
                      "output_seconds": duration, "reference_seconds": duration if clip_info else 0,
                      "billing_note": (f"输出 {duration}s + 参考视频 {duration}s" if clip_info else f"输出 {duration}s") + "；单价以三方渠道账户为准",
                      "clip": store.rel(pid, clip) if clip else None, "caption_residue": residue,
                      "clip_processing": {"masks": masks, "muted": True, "blank_captions": remove_text,
                                          "masks_clock": "source", "indexed_frames": clip_info["indexed_frames"],
                                          "decoded_frames": clip_info["decoded_frames"], "fps": clip_info["fps"],
                                          "mute_reason": "参考片段默认静音，台词按导演说明生成"} if clip else None,
                      "source_range": [segment["source_start"], segment["source_end"]], **mapping, "warnings": warnings,
                      "created": time.strftime("%H:%M:%S")}
            if fix_binding:
                record['fix_plan_binding'] = fix_binding
            def save(current):
                runs = current.setdefault("runs", {}).setdefault(seg_id, [])
                for old in runs:
                    if old.get("state") == "pending_confirmation":
                        old["state"] = "superseded"
                runs.append(record)
            store.update(pid, save)
            return record
        except Exception:
            shutil.rmtree(pkg, ignore_errors=True)
            if clip:
                clip.unlink(missing_ok=True)
            raise


def _find_run(data: dict, tid: str, rev: str) -> dict:
    for r in data.get("runs", {}).get(tid, []):
        if r["rev"] == rev:
            return r
    raise BridgeError("找不到这次预览")


def _read_state(path: Path) -> dict:
    # Missing or damaged evidence must never authorize a new paid request.
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _video_child(run_dir: Path, tid: str) -> dict:
    tasks = _read_state(run_dir / "state.json").get("tasks")
    child = tasks.get(tid) if isinstance(tasks, dict) else None
    return child if isinstance(child, dict) else {}


def _upload_retryable(child: dict, *, image: bool = False) -> bool:
    return (child.get("phase") == ("upload_failed" if image else "failed")
            and child.get("request") is None
            and not child.get("remote_task_id" if image else "jimmy_task_id"))


def _superseding_runs(data: dict, tid: str) -> dict[str, str]:
    """Use creation order, with the first qualifying later run superseding each run."""
    superseded = {}
    later = None
    for run in reversed(data.get("runs", {}).get(tid, [])):
        if later is not None:
            superseded[run["rev"]] = later
        if run.get("state") in {"completed", "running", "submitting", "unknown", "query_pending"}:
            later = run["rev"]
    return superseded


def _update_retryability(pid: str, data: dict, tid: str) -> None:
    superseded = _superseding_runs(data, tid)
    for run in data.get("runs", {}).get(tid, []):
        later = superseded.get(run["rev"])
        if later is not None:
            run.update(superseded_by=later, retryable=False)
        else:
            run.pop("superseded_by", None)
            run["retryable"] = bool(run.get("state") == "failed" and run.get("run_dir")
                                    and _upload_retryable(_video_child(store.project_dir(pid) / run["run_dir"], tid)))


def _upload_error(child: dict, exc: Exception | None = None) -> str:
    error = child.get("error") or {}
    message = error.get("message") if isinstance(error, dict) else str(error)
    return technical_detail(message or getattr(exc, 'technical_detail', exc) or "参考素材上传/请求准备失败").splitlines()[0]


def _check_frozen(frozen: Path, sha256: str) -> None:
    if not frozen.is_file() or store.sha256_file(frozen) != sha256:
        raise BridgeError("已确认的冻结任务文件缺失或哈希改变，不能重新提交")


def _archive_upload_failure(run_dir: Path, tid: str) -> None:
    if not _upload_retryable(_video_child(run_dir, tid)):
        raise BridgeError(UNSAFE_RESUBMIT)
    n = 1
    while run_dir.with_name(f"{run_dir.name}.upload-failed-{n}").exists():
        n += 1
    run_dir.rename(run_dir.with_name(f"{run_dir.name}.upload-failed-{n}"))


def _start_generate(pid: str, tid: str, rev: str, *, resubmitting: bool = False) -> None:
    """Reserve one worker; retries keep the original frozen job and output root."""
    with store._lock(pid):
        run = _find_run(store.load(pid), tid, rev)
        key = f"gen:{tid}:{rev}"
        if jobs.snapshot(pid).get(key, {}).get("state") == "running":
            raise BridgeError("这次生成还在运行，请稍等")
        d = store.project_dir(pid)
        frozen = d / run["package"] / "review" / "jimmy-job.json"
        run_dir = d / run["run_dir"]
        _check_frozen(frozen, run["job_sha256"])
        store.update(pid, lambda x: _find_run(x, tid, rev).update(
            state="running", retryable=False, upload_retry_active=True, upload_retries=0,
            previous_errors=run.get("previous_errors", [])))

        def work() -> None:
            try:
                if resubmitting:
                    with store._lock(pid):
                        _check_frozen(frozen, run["job_sha256"])
                        reason = _upload_error(_video_child(run_dir, tid))
                        _archive_upload_failure(run_dir, tid)
                        store.log(pid, f"{tid} {rev} 手动重新提交：{third_party_message(reason)}；"
                                       "还没创建远端任务，没有产生费用", "warn")
                for attempt in range(len(UPLOAD_RETRY_DELAYS) + 1):
                    _check_frozen(frozen, run["job_sha256"])
                    error = None
                    try:
                        _call([str(_script("jimmy_cli.py")), "generate", "--job", str(frozen),
                               "--output-root", str(run_dir.parent), "--env-file", str(config.jimmy_env_file()),
                               "--timeout", "3600"], frozen.parent, timeout=3900)
                    except Exception as exc:
                        error = exc
                    child = _video_child(run_dir, tid)
                    if not _upload_retryable(child) or attempt == len(UPLOAD_RETRY_DELAYS):
                        if error:
                            raise error
                        return
                    reason = _upload_error(child, error)
                    store.log(pid, f"{tid} {rev} 上传自动重试第 {attempt + 1}/3 次：{third_party_message(reason)}；"
                                   "还没创建远端任务，没有产生费用", "warn")
                    time.sleep(UPLOAD_RETRY_DELAYS[attempt])
                    with store._lock(pid):
                        _check_frozen(frozen, run["job_sha256"])
                        _archive_upload_failure(run_dir, tid)
                        def retried(x: dict) -> None:
                            r = _find_run(x, tid, rev)
                            r.update(upload_retries=attempt + 1)
                            r.setdefault("previous_errors", []).append(reason)
                        store.update(pid, retried)
            except Exception as exc:
                store.update(pid, lambda x: _record_error(_find_run(x, tid, rev), exc))
                raise
            finally:
                with store._lock(pid):
                    store.update(pid, lambda x: _find_run(x, tid, rev).update(upload_retry_active=False))
                    if refresh(pid, tid, rev) is None:
                        store.update(pid, lambda x: _find_run(x, tid, rev).update(state="failed", retryable=False))

        try:
            jobs.start(pid, key, work, f"{tid} 视频生成")
        except Exception:
            def restore(x: dict) -> None:
                r = _find_run(x, tid, rev)
                r.clear()
                r.update(run)
            store.update(pid, restore)
            raise


def resubmit(pid: str, tid: str, rev: str) -> dict:
    with store._lock(pid):
        data = store.load(pid)
        run = _find_run(data, tid, rev)
        later = _superseding_runs(data, tid).get(rev)
        if later is not None:
            raise BridgeError(f"这一段已有更新版本 {later} 生成成功或正在生成，不需要重新提交旧版本")
        if run["state"] != "failed":
            raise BridgeError("只有失败的运行可以重新提交，已完成或正在运行的任务不能重新提交")
        run_dir = store.project_dir(pid) / run.get("run_dir", "")
        if not _upload_retryable(_video_child(run_dir, tid)):
            raise BridgeError(UNSAFE_RESUBMIT)
        _start_generate(pid, tid, rev, resubmitting=True)
    return {"ok": True}


def confirm(pid: str, tid: str, rev: str, job_sha256: str) -> dict:
    """The console's 确认 gate: hash-bound, single use, then hand off to jimmy_cli generate."""
    with store._lock(pid):
        return _confirm_locked(pid, tid, rev, job_sha256)


def _confirm_locked(pid: str, tid: str, rev: str, job_sha256: str) -> dict:
    data = store.load(pid)
    run = _find_run(data, tid, rev)
    if run["state"] != "pending_confirmation":
        raise BridgeError("这份预览已经失效或已提交，请重新生成预览")
    if run["job_sha256"] != job_sha256:
        raise BridgeError("你看到的预览和当前文件不一致，请重新生成预览")
    d = store.project_dir(pid)
    current_task = next((t for t in (data.get("script") or {}).get("tasks", []) if t.get("id") == tid), {})
    if run.get("kind") == "director":
        current_task = next((s for s in (data.get("director") or {}).get("segments", []) if s.get("id") == tid), {})
    verify_preview_current(data, current_task, run, d)
    if not config.jimmy_configured():
        raise BridgeError("还没填三方渠道 Key：点右上角“设置”，在第一栏填写")
    review = d / run["package"] / "review"
    _call([str(_script("chain5_package.py")), "verify", "--output", str(review)], review)
    preview_raw = (review / "jimmy-job.preview.json").read_bytes()
    frozen = review / "jimmy-job.json"
    frozen.write_bytes(preview_raw)
    if store.sha256_file(frozen) != job_sha256:
        frozen.unlink()
        raise BridgeError("冻结文件校验失败")
    handoff_path = review / "jimmy-handoff.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if handoff.get("state") != "ready_for_confirmation":
        raise BridgeError("交接记录不是待确认状态")
    handoff.update(state="confirmed", confirmed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   confirmed_by="console-ui")
    store.atomic_write_json(handoff_path, handoff)

    def fn(x: dict) -> None:
        r = _find_run(x, tid, rev)
        if r.get('fix_plan_binding'):
            from . import fix_plan
            fix_plan.mark_video_started(x, tid, r['fix_plan_binding'], rev, d)
        r.update(state="submitting", run_dir=f"jimmy-runs/{run['job_id']}", confirmed_at=time.strftime("%H:%M:%S"), submitted_at=time.time())
    store.update(pid, fn)
    store.log(pid, f"{tid} {rev} 已确认，交给三方渠道生成（{run['model']}，计费约 {run['billing_seconds']}s）")

    _start_generate(pid, tid, rev)
    return {"ok": True}


def _query_key(pid: str, key: str) -> tuple:
    return (str(store.project_dir(pid).resolve()), key)


def _schedule_video_query(pid: str, tid: str, rev: str) -> None:
    run = _find_run(store.load(pid), tid, rev)
    retry = run.get("query_retry") or {}
    key = _query_key(pid, f"gen:{tid}:{rev}")
    if run.get("state") not in {"running", "query_pending"} or not retry or retry.get("exhausted"):
        remote_query.cancel(key)
        return
    deadline = retry["started_at"] + remote_query.MAX_AGE
    remote_query.schedule(key, min(retry["next_at"], deadline) - time.time(),
                          lambda: _automatic_video_query(pid, tid, rev))


def _automatic_video_query(pid: str, tid: str, rev: str) -> None:
    with store._lock(pid):
        try:
            run = _find_run(store.load(pid), tid, rev)
        except (store.ProjectDeletedError, BridgeError):
            return
        retry = run.get("query_retry") or {}
        if not retry or run.get("state") not in {"running", "query_pending"}:
            return
        if time.time() - retry["started_at"] >= remote_query.MAX_AGE:
            store.update(pid, lambda d: _find_run(d, tid, rev).update(
                state="query_pending", error=None, query_retry={**retry, "exhausted": True}))
            return
        if jobs.snapshot(pid).get(f"gen:{tid}:{rev}", {}).get("state") == "running":
            store.update(pid, lambda d: _find_run(d, tid, rev)["query_retry"].update(next_at=time.time()+30))
            _schedule_video_query(pid, tid, rev)
            return
        resume(pid, tid, rev, _automatic=True)


def resume(pid: str, tid: str, rev: str, *, _automatic: bool = False, _recover: bool = True) -> dict:
    with store._lock(pid):
        data = store.load(pid)
        run = _find_run(data, tid, rev)
        key = f"gen:{tid}:{rev}"
        if jobs.snapshot(pid).get(key, {}).get("state") == "running":
            return {"ok": True}
        run_dir = store.project_dir(pid) / run.get("run_dir", "")
        child = _video_child(run_dir, tid)
        if not child:
            raise BridgeError("这次生成还没有本地运行记录")
        remote = child.get("jimmy_task_id") or run.get("remote_task_id")
        remote_query.cancel(_query_key(pid, key))
        retry = dict(run.get("query_retry") or {})
        if _automatic:
            retry["attempts"] += 1
            retry["next_at"] = time.time() + remote_query.DELAYS[min(retry["attempts"], len(remote_query.DELAYS)-1)]
            store.update(pid, lambda d: _find_run(d, tid, rev).update(query_retry=retry))

        def work() -> None:
            try:
                if remote and _recover:
                    _call([str(_script("jimmy_cli.py")), "recover-task-id", "--run", str(run_dir),
                           "--task-id", tid, "--remote-task-id", str(remote),
                           "--env-file", str(config.jimmy_env_file())], run_dir)
                timeout = max(1, min(3600, int(retry.get("started_at", time.time()) + remote_query.MAX_AGE - time.time()))) if _automatic else 3600
                _call([str(_script("jimmy_cli.py")), "resume", "--run", str(run_dir), "--env-file",
                       str(config.jimmy_env_file()), "--timeout", str(timeout)], run_dir, timeout=timeout+60)
            except Exception as exc:
                # A transport failure in recover/resume itself may leave an older
                # submitted state on disk. Preserve it as query evidence, never submit.
                error = {"type": type(exc).__name__, "message": getattr(exc, "technical_detail", str(exc))}
                if remote and remote_query.transient(error):
                    def pending(d):
                        r = _find_run(d, tid, rev)
                        r.update(query_error=error, remote_task_id=remote)
                    store.update(pid, pending)
                else:
                    raise
            finally:
                with store._lock(pid):
                    try:
                        current = _find_run(store.load(pid), tid, rev)
                        current_retry = current.get("query_retry")
                        if current_retry and _automatic:
                            delay = remote_query.DELAYS[min(current_retry["attempts"], len(remote_query.DELAYS)-1)]
                            store.update(pid, lambda d: _find_run(d, tid, rev)["query_retry"].update(next_at=time.time()+delay))
                    except store.ProjectDeletedError:
                        pass
                refresh(pid, tid, rev, review_completed=True)
        jobs.start(pid, key, work, f"{tid} 重新查询三方任务")
    return {"ok": True}


def recover(pid: str, tid: str, rev: str, remote_task_id: str) -> dict:
    with store._lock(pid):
        if jobs.snapshot(pid).get(f"gen:{tid}:{rev}", {}).get("state") == "running":
            raise BridgeError("这次生成还在运行，请稍等再填写远端任务 ID")
        run = _find_run(store.load(pid), tid, rev)
        run_dir = store.project_dir(pid) / run["run_dir"]
        _call([str(_script("jimmy_cli.py")), "recover-task-id", "--run", str(run_dir), "--task-id", tid,
               "--remote-task-id", remote_task_id.strip(), "--env-file", str(config.jimmy_env_file())], run_dir)
        return resume(pid, tid, rev, _recover=False)


def _schedule_motion_check(pid: str, tid: str, rev: str) -> None:
    """Schedule once per frozen run. A restarted process can resume an interrupted check."""
    data = store.load(pid)
    run = _find_run(data, tid, rev)
    if run.get("kind") == "director":
        return
    if run.get("mode") != "video" or run.get("state") != "completed" or not run.get("result"):
        return
    existing = run.get("motion_check") or {}
    key = f"motion:{tid}:{rev}"
    if existing and (existing.get("status") != "running" or jobs.snapshot(pid).get(key, {}).get("state") == "running"):
        return
    directory = store.project_dir(pid)
    source = run.get("motion_source") or data.get("source") or {}
    task = next((t for t in (data.get("script") or {}).get("tasks", []) if t.get("id") == tid), {})
    dialogue_lines = source.get("dialogue_lines", task.get("dialogue_lines"))
    source_range = run.get("source_range")
    result = run["result"]
    store.update(pid, lambda d: _find_run(d, tid, rev).update(motion_check={"status": "running", "warnings": []}))

    def work() -> None:
        try:
            if not source.get("file") or not source_range:
                raise ValueError("运行记录缺少原片及区间证据")
            src = directory / source["file"]
            if source.get("sha256") and store.sha256_file(src) != source["sha256"]:
                raise ValueError("原片已改变，不能与这次生成使用的原片比较")
            transcript = source.get("transcript")
            if transcript is None:
                transcript = motion.read_transcript(directory)
            a, b = map(float, source_range)
            report = media.motion_alignment(directory / result, src, a, b, transcript,
                                             directory / run["run_dir"] / "motion-check", dialogue_lines=dialogue_lines)
        except Exception as exc:
            report = {"status": "inconclusive", "warnings": [f"本地动作对齐检查未完成：{exc}"]}
        timing = None
        if scene_plan.enabled(data):
            from . import speech_timing
            try:
                timing = speech_timing.for_run(task, {**run, 'motion_check': report}, directory)
            except (ValueError,OSError):
                pass
        store.update(pid, lambda d: _find_run(d, tid, rev).update(motion_check=report, **({'speech_timing':timing} if timing else {})))

    try:
        jobs.start(pid, key, work, f"{tid} {rev} 本地动作对齐检查")
    except Exception as exc:
        store.update(pid, lambda d: _find_run(d, tid, rev).update(
            motion_check={"status": "inconclusive", "warnings": [f"本地动作检查未能启动：{exc}"]}))


def refresh(pid: str, tid: str, rev: str, *, review_completed: bool = False) -> dict | None:
    """Mirror jimmy-runs/<job>/state.json into project.json."""
    with store._lock(pid):
        try:
            before = _find_run(store.load(pid), tid, rev)
        except store.ProjectDeletedError:
            return None
        child = _refresh(pid, tid, rev)
        store.update(pid, lambda x: _update_retryability(pid, x, tid))
        after = _find_run(store.load(pid), tid, rev)
        if after.get("kind") != "director":
            _schedule_motion_check(pid, tid, rev)
        elif (after.get("state") == "completed" and after.get("result")
              and (before.get("review") or {}).get("status") == "running"):
            from . import segment_review
            segment_review.start(pid, tid, rev)
        _schedule_video_query(pid, tid, rev)
        return child


def _refresh(pid: str, tid: str, rev: str) -> dict | None:
    data = store.load(pid)
    run = _find_run(data, tid, rev)
    if not run.get("run_dir"):
        return None
    child = _video_child(store.project_dir(pid) / run["run_dir"], tid)
    if not child:
        return None
    phase = child.get("phase")
    mapped = {"completed": "completed", "failed": "failed", "submission_outcome_unknown": "unknown"}.get(phase, "running")
    query_error = child.get("error") or run.get("query_error")
    remote = child.get("jimmy_task_id") or run.get("remote_task_id")
    query_failed = bool(remote and phase != "completed" and (
        remote_query.transient(query_error, child.get("status"))
        or (run.get("query_retry") and phase in {"submitted", "running"} and not child.get("error"))))
    retry = remote_query.retry_state(run, remote_query.started_at(run, store.project_dir(pid) / run["run_dir"])) if query_failed else None
    if retry:
        mapped = "query_pending" if retry["exhausted"] else "running"
    retrying = (run.get("upload_retry_active")
                and jobs.snapshot(pid).get(f"gen:{tid}:{rev}", {}).get("state") == "running")
    if retrying and _upload_retryable(child):
        mapped = "running"
    result = None
    if phase == "completed" and child.get("local_path"):
        result = store.rel(pid, Path(child["local_path"]))
    copy_check = None
    if result and run.get("kind") != "director" and run.get("state") != "completed" and not run.get("copy_check"):
        try:
            task = next((t for t in (data.get("script") or {}).get("tasks", []) if t["id"] == tid), {})
            replaced = run.get("person_replaced", bool(replacement_guard.replaced_people(data, task)))
            if replaced and data.get("source"):
                a, b = run.get("source_range") or [task.get("source_start"), task.get("source_end")]
                copy_check = media.copy_similarity(store.project_dir(pid) / result,
                                                   store.project_dir(pid) / data["source"]["file"], float(a), float(b))
        except Exception as exc:
            copy_check = {"error": f"本地相似度检查未完成：{exc}"}

    def fn(x: dict) -> None:
        r = _find_run(x, tid, rev)
        r.update(state=mapped, phase=phase, remote_status=child.get("status"),
                 remote_task_id=remote, error=user_message((child.get("error") or {}).get("message")) if child.get("error") else None,
                 result=result or r.get("result"))
        if r.get("kind") == "director" and r["state"] == "completed" and r.get("result"):
            segment = next((s for s in (x.get("director") or {}).get("segments", []) if s.get("id") == tid), None)
            if segment is not None:
                selected = next((item for item in x.get("runs", {}).get(tid, [])
                                 if segment.get("selected_run") and item.get("rev") == segment["selected_run"]), None)
                completed = [item for item in x.get("runs", {}).get(tid, [])
                             if item.get("state") == "completed" and item.get("result")]
                if (not x.get('derived_from') and (selected is None or selected.get("state") != "completed")
                        or x.get('derived_from') and not segment.get('selected_run') and len(completed) == 1):
                    segment["selected_run"] = rev
        if (child.get("error") or {}).get("message"):
            _record_error(r, child["error"]["message"])
        if retry:
            r.update(query_retry=retry, error=None)
        else:
            r.pop("query_retry", None)
            r.pop("query_error", None)
        if copy_check is not None:
            r["copy_check"] = copy_check
            r["copy_suspected"] = copy_check.get("copy_suspected", False)
            r["similarity"] = copy_check.get("similarity")
    store.update(pid, fn)
    return child


def refresh_all(pid: str) -> None:
    with store._lock(pid):
        try:
            data = store.load(pid)
        except store.ProjectDeletedError:
            return
        _refresh_all_locked(pid, data)


def _refresh_all_locked(pid: str, data: dict) -> None:
    # A browser GET after restart must not recover historical jobs or write them.
    # Only jobs admitted by an explicit action in this process are polled here.
    active = jobs.snapshot(pid)
    if not active:
        return
    for tid, runs in (data.get("runs") or {}).items():
        touched = False
        for r in runs:
            if not any(f'{kind}:{tid}:{r["rev"]}' in active for kind in ('gen', 'review', 'motion')):
                continue
            touched = True
            if (r.get("kind") == "director" and r.get("state") == "completed" and r.get("result")
                    and (r.get("review") or {}).get("status") == "running"):
                from . import segment_review
                segment_review.start(pid, tid, r["rev"])
            if r.get("state") in {"submitting", "running", "failed", "query_pending"} or (
                    r.get("kind") != "director" and r.get("state") == "completed" and r.get("mode") == "video"
                    and (not r.get("motion_check") or r["motion_check"].get("status") == "running")):
                try:
                    gateway.video_refresh(pid, tid, r["rev"])
                except Exception:
                    pass
        if touched:
            store.update(pid, lambda x: _update_retryability(pid, x, tid))
    _refresh_image_queries(pid, active=active)


# ---------- target-state images through Jimmy (chain5_image.py) ----------

def image_preview(pid: str, prompt: str, image_ids: list[str], ratio: str, model: str, resolution: str,
                  for_task: str | None, *, draft_binding: str | None = None, draft_frame_reason: str | None = None,
                  channel: str | None = None, quality: str = "auto") -> dict:
    """Build a local preview. Omitted channels retain the legacy third-party route.

    The HTTP entry point selects official for new main-camera previews when its
    key is configured; legacy direct callers and old records never read config
    merely to infer a channel.
    """
    channel = channel or "third_party"
    if channel not in {"official", "third_party"}:
        raise BridgeError("未知生图渠道")
    if channel == "official" and quality not in {"auto", "high"}:
        raise BridgeError("生图画质只能为 auto 或 high")
    data = store.load(pid)
    d = store.project_dir(pid)
    assets = {a["id"]: a for a in data.get("assets", [])}
    iid = store.new_id("img")
    if (for_task or "").startswith("scene-camera:") and len(image_ids) > max_image_references(channel, model):
        raise BridgeError(f"这一场要送去的参考图有 {len(image_ids)} 张，当前生图渠道最多支持 {max_image_references(channel, model)} 张，请减少勾选或合成商品板")
    refs = []
    for aid in image_ids:
        a = assets.get(aid)
        if not a or a["type"] != "image":
            raise BridgeError(f"参考图 {aid} 不存在")
        refs.append({"source": "file", "path": str((d / a["file"]).resolve())})
    camera_fields = {}
    if (for_task or "").startswith("scene-camera:"):
        scene_id = for_task.split(":", 1)[1]
        current_binding = scene_plan.binding(data, scene_id, d)
        if draft_binding is not None and draft_binding != current_binding:
            raise BridgeError("这一场的设定已变，需要重新生成预览")
        if not image_ids:
            raise BridgeError('请先选择这一场的原片参考帧')
        frame = assets[image_ids[-1]]
        if frame.get("camera_scene") != scene_id or frame.get("frame_at") is None:
            raise BridgeError("原片参考帧不属于这一场，请先点自动选帧或换一帧")
        scene = next(s for s in replacement_guard.scenes(data) if s["id"] == scene_id)
        ranges = replacement_guard.scene_ranges(scene)
        at = float(frame["frame_at"])
        if ranges and not any(a <= at <= b for a, b in ranges):
            raise BridgeError("原片参考帧的时间不在这一场，请重新选帧")
        expected = scene_image_references(data, scene_id, at=at)
        if len(image_ids) != len(expected["image_ids"]) + 1 or image_ids[:-1] != expected["image_ids"]:
            raise BridgeError("这一场的参考图顺序已变化，请重新生成预览：人物照片、商品图、可选身份锚点、原片参考帧")
        if not all((d / assets[aid]["file"]).resolve().is_relative_to(d.resolve())
                   and (d / assets[aid]["file"]).is_file() for aid in image_ids):
            raise BridgeError("参考图文件缺失或路径无效，请重新选择照片和参考帧")
        camera_fields = {"camera_scene": scene_id, "scene_camera_scene": scene_id,
            "scene_camera_binding": current_binding, "camera_binding": current_binding,
            "scene_camera_subjects": expected["subjects"], "camera_product_id": expected["product_id"],
            "image_ids": list(image_ids), "frame_asset_id": frame.get("derived_from") or frame["id"],
            "frame_at": at, "frame_reason": draft_frame_reason or frame.get("frame_reason") or "manual",
            "anchor_asset_id": expected["anchor_asset_id"]}
        if frame.get("derived_from"):
            camera_fields.update(clean_frame_asset_id=frame["id"],
                caption_overlap_warning=frame.get("caption_overlap_warning", ""),
                caption_residue=frame.get("caption_residue"))
    elif (for_task or "").startswith("camera:"):
        parts = for_task.split(":")
        char_id = parts[1]
        scene_id = parts[2] if len(parts) > 2 else replacement_guard.first_scene(data, char_id)
        if scene_id not in {s["id"] for s in replacement_guard.scenes(data)}:
            raise BridgeError(f"场景 {scene_id} 不存在")
        for_task = f"camera:{char_id}:{scene_id}"
        current_binding = replacement_guard.camera_binding(data, char_id, d, scene_id=scene_id)
        if draft_binding is not None and draft_binding != current_binding:
            raise BridgeError("主镜头图起草已过期：人物、商品或场景设定已变化，请重新起草")
        rep = data.get("replacements") or {}
        char = next((c for c in rep.get("characters", []) if c.get("source_id") == char_id), {})
        prod = rep.get("product") or {}
        if not image_ids or image_ids[0] != char.get("asset_id"):
            raise BridgeError("主镜头图的图1必须是第 03 步所选人物照片，请重新生成预览")
        product_id = prod.get("asset_id") if prod.get("action") == "replace" else None
        if product_id and (len(image_ids) < 2 or image_ids[1] != product_id):
            raise BridgeError("主镜头图的图2必须是第 03 步所选商品图，请重新生成预览")
        base_count = 2 if product_id else 1
        anchor_id = image_ids[base_count] if len(image_ids) == base_count + 2 else None
        if len(image_ids) not in {base_count + 1, base_count + 2}:
            raise BridgeError("主镜头图参考图应为人物、可选商品、可选身份锚点和本场景参考帧")
        allowed_anchors = {replacement_guard.camera_image_for(data, char_id, scene["id"])
                           for scene in replacement_guard.scenes(data) if scene["id"] != scene_id}
        if anchor_id and anchor_id not in allowed_anchors:
            raise BridgeError("身份锚点必须是该人物在其他场景已选用的主镜头图")
        frame = assets[image_ids[-1]]
        if frame.get("camera_scene") and frame["camera_scene"] != scene_id:
            raise BridgeError("原片参考帧不属于本场景，请重新起草")
        from .pipeline import frame_metadata
        frame_info = frame_metadata(data, frame)
        if draft_frame_reason == "manual":
            frame_info["frame_reason"] = "manual"
            frame_info["frame_warning"] = ""
        camera_fields = {"camera_character": char_id, "camera_scene": scene_id, "camera_product_id": product_id,
                         "camera_binding": current_binding,
                         "image_ids": image_ids, "frame_asset_id": frame.get('derived_from') or image_ids[-1], **frame_info,
                         "anchor_asset_id": anchor_id}
        if frame.get('derived_from'):
            camera_fields.update(clean_frame_asset_id=frame['id'],
                caption_overlap_warning=frame.get('caption_overlap_warning', ''),
                caption_residue=frame.get('caption_residue'))
    job = {"schema_version": "chain5.image-job.v1", "id": iid, "model": model or "gpt-image-2",
           "prompt": prompt, "ratio": ratio, "resolution": resolution or "2k", "images": refs,
           "metadata": {"purpose": f"补齐目标状态图（{for_task or '素材库'}）"}}
    if (model or "gpt-image-2") == "gpt-image-2":
        job["quality"] = "auto"
    job_path = d / "image-runs" / f"{iid}.job.json"
    store.atomic_write_json(job_path, job)
    run_dir = d / "image-runs" / iid
    if channel == "official":
        preview_json = {**job, "channel": channel, "size": openai_image.image_size(ratio), "quality": quality,
                        "n": 1, "images": [{**ref, "sha256": store.sha256_file(Path(ref["path"]))} for ref in refs]}
        store.atomic_write_json(run_dir / "image-job.preview.json", preview_json)
        out = "官方生图：确认后按 OpenAI 计费"
    else:
        out = _call([str(_script("chain5_image.py")), "prepare", "--job", str(job_path), "--run", str(run_dir)], d)
        preview_json = json.loads((run_dir / "image-job.preview.json").read_text(encoding="utf-8"))
    record = {"id": iid, "state": "pending_confirmation", "channel": channel,
              "for_task": for_task, "preview": preview_json,
              "preview_sha256": store.sha256_file(run_dir / "image-job.preview.json"),
              "run_dir": store.rel(pid, run_dir),
              "summary": out.strip() if channel == "official" else "三方渠道生图：确认后约 0.02 点", **camera_fields}
    store.update(pid, lambda x: x.setdefault("images", {}).__setitem__(iid, record))
    return record


def _verify_camera_image_current(pid: str, iid: str) -> None:
    from . import storyboard_edits
    storyboard_edits.verify_preview(pid, store.load(pid).get("images", {}).get(iid, {}))
    if iid in replacement_guard.camera_stale_images(store.load(pid), store.project_dir(pid)):
        raise BridgeError("主镜头图预览已过期：人物、商品、场景或身份锚点已变化，请重新起草")


def _image_submit(pid: str, iid: str, run_dir: Path, env: str) -> None:
    """chain5_image can reuse upload_failed runs and their cached uploads safely."""
    store.update(pid, lambda x: x["images"][iid].update(upload_retries=0))
    for attempt in range(len(UPLOAD_RETRY_DELAYS) + 1):
        _verify_camera_image_current(pid, iid)
        error = None
        try:
            _call([str(_script("chain5_image.py")), "submit", "--run", str(run_dir), "--env-file", env],
                  store.project_dir(pid))
        except Exception as exc:
            error = exc
        child = _read_state(run_dir / "image-state.json")
        if (child.get('remote_task_id') or child.get('request') is not None
                or child.get('phase') in {'create_started', 'submission_outcome_unknown', 'submitted', 'running', 'completed'}):
            gateway.usage.mark_network_request()
        if not _upload_retryable(child, image=True):
            if error:
                raise error
            return
        reason = _upload_error(child, error)
        if attempt == len(UPLOAD_RETRY_DELAYS):
            raise error or BridgeError(reason)
        store.log(pid, f"{iid} 生图上传自动重试第 {attempt + 1}/3 次：{third_party_message(reason)}；"
                       "还没创建远端任务，没有产生费用", "warn")
        time.sleep(UPLOAD_RETRY_DELAYS[attempt])
        if not _upload_retryable(_read_state(run_dir / "image-state.json"), image=True):
            raise BridgeError(UNSAFE_RESUBMIT)
        def retried(x: dict) -> None:
            rec = x["images"][iid]
            rec.update(upload_retries=attempt + 1)
            rec.setdefault("previous_errors", []).append(reason)
        store.update(pid, retried)


def _image_poll(pid: str, iid: str, run_dir: Path, env: str, *, wait: bool) -> None:
    deadline = time.time() + 900
    while time.time() < deadline:
        try:
            out = json.loads(_call([str(_script("chain5_image.py")), "resume", "--run", str(run_dir),
                                    "--env-file", env], store.project_dir(pid)) or "{}")
        except BridgeError as exc:
            if "unrecognized status" not in getattr(exc, "technical_detail", str(exc)).lower():
                raise
            wait = True  # Unknown running states must keep the recovery worker alive.
            rec = store.load(pid)["images"][iid]
            if not rec.get("unknown_status_logged"):
                store.log(pid, "三方渠道返回生成中状态，继续等待")
            store.update(pid, lambda x: x["images"][iid].update(state="running", unknown_status_logged=True, started_at=rec.get("started_at") or time.time()))
            out = {}
        child = _read_state(run_dir / "image-state.json")
        record = store.load(pid)["images"][iid]
        metadata = {k: child[k] for k in ("remote_task_id", "output_sha256") if child.get(k) and not record.get(k)}
        if metadata:
            store.update(pid, lambda x: x["images"][iid].update(metadata))
        if out.get("phase") == "completed" and out.get("output"):
            _image_done(pid, iid, Path(out["output"]))
            return
        if out.get("phase") in {"failed", "error", "cancelled"}:
            raise BridgeError(str(out.get("error") or "三方渠道生图任务失败或已取消"))
        if not wait:
            return
        time.sleep(6)
    raise BridgeError("生图超过 15 分钟未完成，可稍后点“恢复”")


def _image_query_failure(pid: str, iid: str, exc: object) -> bool:
    rec = store.load(pid)["images"][iid]
    if rec.get("channel") == "official":
        return False
    run_dir = store.project_dir(pid) / rec["run_dir"]
    child = _read_state(run_dir / "image-state.json")
    remote = child.get("remote_task_id") or rec.get("remote_task_id")
    error = {"type": type(exc).__name__, "message": getattr(exc, "technical_detail", str(exc))}
    if not remote or child.get("phase") in {"failed", "error", "cancelled"} or not remote_query.transient(error):
        return False
    retry = remote_query.retry_state(rec, remote_query.started_at(rec, run_dir, image=True))
    if retry["next_at"] <= time.time():
        retry["next_at"] = time.time()+remote_query.DELAYS[min(retry["attempts"], len(remote_query.DELAYS)-1)]
    store.update(pid, lambda d: d["images"][iid].update(
        state="query_pending" if retry["exhausted"] else "running", error=None,
        remote_task_id=remote, query_error=error, query_retry=retry))
    _schedule_image_query(pid, iid)
    return True


def _schedule_image_query(pid: str, iid: str) -> None:
    rec = store.load(pid)["images"][iid]
    retry = rec.get("query_retry") or {}
    key = _query_key(pid, f"img:{iid}")
    if rec.get("state") not in {"running", "query_pending"} or not retry or retry.get("exhausted"):
        remote_query.cancel(key)
        return
    deadline = retry["started_at"] + remote_query.MAX_AGE
    remote_query.schedule(key, min(retry["next_at"], deadline)-time.time(), lambda: _automatic_image_query(pid, iid))


def _automatic_image_query(pid: str, iid: str) -> None:
    with store._lock(pid):
        try:
            rec = store.load(pid)["images"][iid]
        except (store.ProjectDeletedError, KeyError):
            return
        retry = rec.get("query_retry") or {}
        if not retry or rec.get("state") not in {"running", "query_pending"}:
            return
        if time.time()-retry["started_at"] >= remote_query.MAX_AGE:
            store.update(pid, lambda d:d["images"][iid].update(state="query_pending", error=None,
                         query_retry={**retry, "exhausted": True}))
            return
        if jobs.snapshot(pid).get(f"img:{iid}", {}).get("state") == "running":
            store.update(pid, lambda d:d["images"][iid]["query_retry"].update(next_at=time.time()+30))
            _schedule_image_query(pid, iid)
            return
        retry["attempts"] += 1
        retry["next_at"] = time.time()+remote_query.DELAYS[min(retry["attempts"], len(remote_query.DELAYS)-1)]
        store.update(pid, lambda d:d["images"][iid].update(query_retry=retry))
        image_resume(pid, iid)


def _refresh_image_queries(pid: str, *, active=None) -> None:
    for iid, rec in store.load(pid).get("images", {}).items():
        if active is not None and f'img:{iid}' not in active:
            continue
        if rec.get("channel") == "official" or rec.get("state") not in {"failed", "running", "query_pending"}:
            continue
        if rec.get("query_retry"):
            _schedule_image_query(pid, iid)
        elif rec.get("error") or rec.get("query_error"):
            # Older bridge versions sanitized error for display. The original
            # CLI exception survives only in previous_errors; the engine state
            # still decides whether this is an actually failed remote task.
            candidates = [(rec.get("query_error") or {}).get("message"), rec.get("error"),
                          *(rec.get("previous_errors") or [])]
            for message in candidates:
                if message and _image_query_failure(pid, iid, BridgeError(message)):
                    break


def _clear_image_error(record: dict) -> None:
    record.pop("restore_error", None)
    error = record.pop("error", None)
    if error:
        record.setdefault("previous_errors", []).append(technical_detail(error))


def _official_preview_current(pid: str, iid: str) -> dict:
    _verify_camera_image_current(pid, iid)
    record = store.load(pid)["images"][iid]
    directory = store.project_dir(pid)
    preview_path = directory / record["run_dir"] / "image-job.preview.json"
    if not preview_path.is_file() or store.sha256_file(preview_path) != record.get("preview_sha256"):
        raise BridgeError("官方生图预览哈希校验失败，请重新生成预览")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    for ref in preview.get("images") or []:
        path = Path(ref["path"])
        if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file() or store.sha256_file(path) != ref.get("sha256"):
            raise BridgeError("参考图哈希校验失败，预览已过期，请重新起草")
    return preview


def image_confirm(pid: str, iid: str) -> dict:
    # Reserve the pending record while locked, so duplicate clicks cannot pay twice.
    with store._lock(pid):
        _verify_camera_image_current(pid, iid)
        data = store.load(pid)
        rec = (data.get("images") or {}).get(iid)
        if not rec or rec["state"] != "pending_confirmation":
            raise BridgeError("这张图的预览不存在或已提交")
        official = rec.get("channel") == "official"
        preview = _official_preview_current(pid, iid) if official else None
        if not official and not config.jimmy_configured():
            raise BridgeError("还没填三方渠道 Key：点右上角“设置”，在第一栏填写")
        d = store.project_dir(pid)
        run_dir = d / rec["run_dir"]
        env = None if official else str(config.jimmy_env_file())
        store.update(pid, lambda x: x["images"][iid].update(state="running", started_at=time.time()))

        def work() -> None:
            try:
                if official:
                    output = run_dir / "result.png"
                    result = openai_image.generate(preview, output,
                        before_send=lambda: _official_preview_current(pid, iid))
                    store.update(pid, lambda x: x["images"][iid].update(result))
                    _image_done(pid, iid, output)
                else:
                    _image_submit(pid, iid, run_dir, env)
                    _image_poll(pid, iid, run_dir, env, wait=True)
            except Exception as exc:
                if not official and _image_query_failure(pid, iid, exc):
                    return
                def failed(x: dict) -> None:
                    x["images"][iid]["state"] = "failed"
                    _record_error(x["images"][iid], exc)
                    x["images"][iid].pop("query_retry", None)
                    x["images"][iid].pop("query_error", None)
                store.update(pid, failed)
                raise
        try:
            jobs.start(pid, f"img:{iid}", work, "生成目标状态图")
        except Exception:
            # Restore only a worker-start failure, never an executed failed request.
            if store.load(pid)["images"][iid].get("state") == "running":
                store.update(pid, lambda x: x["images"][iid].update(state="pending_confirmation"))
            raise
    return {"ok": True}


def _image_done(pid: str, iid: str, output: Path) -> None:
    d = store.project_dir(pid)
    target = d / "assets" / f"{iid}{output.suffix}"
    image_record = (store.load(pid).get("images") or {}).get(iid, {})
    expected = image_record.get("output_sha256") or _read_state(d / image_record.get("run_dir", "") / "image-state.json").get("output_sha256")
    if expected and store.sha256_file(output) != expected:
        raise BridgeError("恢复图片 SHA256 校验失败，未加入素材库")
    target.parent.mkdir(parents=True, exist_ok=True)
    if output.resolve() != target.resolve():
        shutil.copy2(output, target)
    if image_record.get("storyboard_context"):
        from . import storyboard_images
        storyboard_images.complete(pid, image_record, output)
    for_task = image_record.get("for_task") or ""
    parts = for_task.split(":")
    char = parts[1] if for_task.startswith("camera:") else None
    data = store.load(pid)
    scene_camera = for_task.startswith("scene-camera:")
    scene_id = parts[1] if scene_camera else parts[2] if len(parts) > 2 else replacement_guard.first_scene(data, char) if char else None
    scene = next((s for s in replacement_guard.scenes(data) if s["id"] == scene_id), {})
    label = f"主镜头图 {char} · {scene_id} {scene.get('name', '')}" if char else f"生成图 {iid[-4:]}"
    if scene_camera:
        label = f"主镜头图 {scene_id} {scene.get('name', '')}"
    generated_at = image_record.get("completed_at") or image_record.get("started_at") or time.time()
    asset = {"id": iid, "type": "image", "label": label,
             "roles": ["storyboard"] if image_record.get("storyboard_context") else ["target_state"], "file": store.rel(pid, target), "sha256": store.sha256_file(target),
             "description": "", "generated": True, "generated_at": generated_at,
             **{k: image_record[k] for k in ("camera_character", "camera_scene", "camera_product_id", "camera_binding", "frame_asset_id", "frame_at", "frame_reason", "frame_warning", "clean_frame_asset_id", "caption_overlap_warning", "caption_residue", "scene_camera_binding", "scene_camera_scene", "scene_camera_subjects") if k in image_record}}

    selected_camera = replacement_guard.camera_image_for(data, char, scene_id) if char else None
    if char:
        asset.update(camera_character=char, camera_scene=scene_id)
        asset.setdefault("camera_binding", replacement_guard.camera_binding(data, char, d, scene_id=scene_id))
        if "camera_scene" not in image_record:
            # Already-running legacy jobs were bound before scene metadata existed.
            asset["camera_binding_legacy"] = True

    def fn(x: dict) -> None:
        x["assets"] = [a for a in x["assets"] if a["id"] != iid] + [asset]
        x["images"][iid].update(state="completed", asset_id=iid, result=asset["file"], output_sha256=asset["sha256"], completed_at=generated_at)
        _clear_image_error(x["images"][iid])
        x["images"][iid].pop("query_retry", None)
        x["images"][iid].pop("query_error", None)
        remote_query.cancel(_query_key(pid, f"img:{iid}"))
        if scene_camera:
            asset.update(scene_camera_scene=scene_id, camera_scene=scene_id)
            # Preserve the submitted binding when settings change during generation;
            # a finished stale image stays available in history without becoming active.
            if scene_plan.camera_valid(x, iid, scene_id, d):
                x.setdefault("replacements", {}).setdefault("scene_plan", {}).setdefault(scene_id, {})["camera_asset"] = iid
                x.get("camera_notice", {}).pop(f"scene:{scene_id}", None)
        for c in (x.get("replacements") or {}).get("characters", []):
            if char and c.get("source_id") == char:
                c.setdefault("camera_assets", {})[scene_id] = iid if len(parts) > 2 else selected_camera or iid
        if char and len(parts) > 2:
            x.get("camera_notice", {}).pop(f"{char}:{scene_id}", None)
        replacement_guard.clean_camera_selections(x)
    store.update(pid, fn)
    if char and len(parts) > 2:
        from . import pipeline
        pipeline.follow_camera_selection(pid, char, scene_id, report_failure=True)
    if scene_camera:
        from . import pipeline
        pipeline.follow_scene_camera_selection(pid, scene_id, report_failure=True)
    store.log(pid, f"目标状态图已生成并加入素材库：{asset['label']}（请检查后再用于视频）")


def image_resume(pid: str, iid: str) -> dict:
    with store._lock(pid):
        if jobs.snapshot(pid).get(f"img:{iid}", {}).get("state") == "running":
            return {"ok": True}
        remote_query.cancel(_query_key(pid, f"img:{iid}"))
        return _image_resume_locked(pid, iid)


def _image_resume_locked(pid: str, iid: str) -> dict:
    data = store.load(pid)
    rec = data["images"][iid]
    d = store.project_dir(pid)
    run_dir = d / rec["run_dir"]
    official = rec.get("channel") == "official"
    restoring = official or rec.get("state") == "completed" or _read_state(run_dir / "image-state.json").get("phase") == "completed"
    if restoring:
        candidates = [run_dir / "result.png", *run_dir.glob("result.*")]
        if rec.get("result"):
            candidates.append(d / rec["result"])
        candidates += list((d / "assets").glob(f"{iid}.*"))
        for local in candidates:
            if local.resolve().is_relative_to(d.resolve()) and local.is_file():
                _image_done(pid, iid, local)
                return {"ok": True}
    if official:
        raise BridgeError("官方生图没有可恢复的远端任务，请重新确认生图（先重新生成预览）")
    if _upload_retryable(_read_state(run_dir / "image-state.json"), image=True):
        _verify_camera_image_current(pid, iid)
    env = str(config.jimmy_env_file())

    def work() -> None:
        try:
            upload_failed = _upload_retryable(_read_state(run_dir / "image-state.json"), image=True)
            if restoring:
                # chain5_image.resume refuses a completed state with missing output.
                # Use a GET-only adapter for that case, without changing engine code/state.
                child = _read_state(run_dir / "image-state.json")
                expected = rec.get("output_sha256") or child.get("output_sha256")
                remote = rec.get("remote_task_id") or child.get("remote_task_id")
                args = [str(config.ROOT / "tools/restore_image.py"), "resume", "--run", str(run_dir), "--env-file", env]
                if remote:
                    args += ["--task-id", remote]
                if expected:
                    args += ["--sha256", expected]
                out = json.loads(_call(args, d) or "{}")
                if out.get("phase") != "completed" or not out.get("output"):
                    raise BridgeError("原任务图片还不能恢复，请稍后重试")
                _image_done(pid, iid, Path(out["output"]))
                return
            if upload_failed:
                store.update(pid, lambda x: x["images"][iid].update(state="running", started_at=time.time()))
                _image_submit(pid, iid, run_dir, env)
            else:
                # The image engine refuses terminal local states before GET.
                # Recover only this same remote ID; keep the frozen job and all
                # hashes intact. This path always calls resume, never submit.
                with store._lock(pid):
                    child = _read_state(run_dir / "image-state.json")
                    remote = child.get("remote_task_id")
                    if child.get("phase") in {"failed", "error", "cancelled"} and remote:
                        if rec.get("remote_task_id") and rec["remote_task_id"] != remote:
                            raise BridgeError("本地记录的远端任务号不一致，请核对后再恢复")
                        retry = remote_query.retry_state(rec, remote_query.started_at(rec, run_dir, image=True))
                        child["phase"] = "submitted"
                        store.atomic_write_json(run_dir / "image-state.json", child)
                        store.update(pid, lambda d: d["images"][iid].update(
                            state="query_pending" if retry["exhausted"] else "running",
                            error=None, remote_task_id=remote, query_retry=retry))
            _image_poll(pid, iid, run_dir, env, wait=upload_failed)
            current = store.load(pid)["images"][iid]
            if current.get("query_retry") and current.get("state") != "completed":
                _image_query_failure(pid, iid, BridgeError("image query/download failed; still generating"))
        except Exception as exc:
            if not restoring and _image_query_failure(pid, iid, exc):
                return
            if restoring:
                def restore_failed(x: dict) -> None:
                    x["images"][iid]["state"] = "completed"
                    _record_error(x["images"][iid], exc, field="restore_error")
                store.update(pid, restore_failed)
            else:
                def failed(x: dict) -> None:
                    x["images"][iid]["state"] = "failed"
                    _record_error(x["images"][iid], exc)
                    x["images"][iid].pop("query_retry", None)
                    x["images"][iid].pop("query_error", None)
                store.update(pid, failed)
            raise
    jobs.start(pid, f"img:{iid}", work, "恢复生图")
    return {"ok": True}


# ---------- 06 assemble ----------

def save_assembly_options(pid: str, options: dict) -> dict:
    trim = options.get('trim_to_source')
    if not isinstance(trim, bool):
        raise ValueError('请用勾选项选择是否按原片时长裁剪')
    return store.update(pid, lambda data: data.setdefault('assembly_options', {}).update(trim_to_source=trim))


def assemble(pid: str) -> dict:
    data = store.load(pid)
    d = store.project_dir(pid)
    if (data.get("replacements") or {}).get("scene_edits") and not replacement_guard.script_current(data, d):
        raise BridgeError(replacement_guard.STALE_SCRIPT)
    clips, targets = [], []
    trim = (data.get('assembly_options') or {}).get('trim_to_source', True)
    for t in sorted((data.get("script") or {}).get("tasks", []), key=lambda x: x["order"]):
        chosen = t.get("chosen_rev")
        runs = [r for r in data.get("runs", {}).get(t["id"], []) if r.get("state") == "completed" and r.get("result")]
        if chosen:
            runs = [r for r in runs if r["rev"] == chosen] or runs
        if not runs:
            raise BridgeError(f"{t['id']}「{t['title']}」还没有成功的视频")
        clips.append(d / runs[-1]["result"])
        if trim:
            target = segment_timing.target_seconds(t)
            if scene_plan.enabled(data):
                from . import speech_timing
                timing = speech_timing.for_run(t, runs[-1], d, transcribe=True)
                if timing:
                    target = timing['trim_end']
                    store.update(pid, lambda current: _find_run(current, t['id'], runs[-1]['rev']).update(speech_timing=timing))
                    if timing.get('warning'):
                        store.log(pid,timing['warning'],'warning')
            targets.append(target)
    if not clips:
        raise BridgeError('没有可拼接的片段，请先生成脚本和视频')
    first = media.probe(clips[0])
    out = d / "final" / f"final-{time.strftime('%m%d-%H%M%S')}.mp4"
    media.concat(clips, out, first.get("width") or 720, first.get("height") or 1280,
                 target_seconds=targets if trim else None)
    store.update(pid, lambda x: x.update(final=store.rel(pid, out)))
    store.log(pid, f"已拼接成片：{out.name}")
    return {"final": store.rel(pid, out)}
