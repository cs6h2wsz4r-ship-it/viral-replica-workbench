"""Read-only replacement preflight shared by previews and the live editor."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from . import scene_edits, scene_plan, store

NO_IMAGES = "这一段还没选参考图：请先在第 03 步设置替换，或在本段点选参考图（先点人物）"
STALE_SCRIPT = "替换设定在生成脚本之后改过，请重新生成新脚本"
NEED_CAMERA = "视频参考模式下换人，需要先生成主镜头图：新人物在原片机位里的一帧完整画面"
MUTE_REASON = "原声会把旧人物的声音带进来，Seedance 会按新人物生成声音。"
TEXT_FIELDS = ("action", "story", "direction")


def scenes(data: dict) -> list[dict]:
    return scene_edits.effective_scenes(data)


def scene_ranges(scene: dict) -> list[tuple[float, float]]:
    return scene_edits.ranges(scene)


def scene_overlaps(data: dict, start: float, end: float) -> list[dict]:
    out = []
    for scene in scenes(data):
        intervals = [(max(start, a), min(end, b)) for a, b in scene_ranges(scene) if b > start and a < end]
        if intervals:
            out.append({"scene_id": scene["id"], "overlap": sum(b - a for a, b in intervals),
                        "start": min(a for a, b in intervals)})
    return sorted(out, key=lambda row: -row["overlap"])


def scene_for_range(data: dict, start: float, end: float) -> str:
    overlaps = scene_overlaps(data, float(start), float(end))
    return overlaps[0]["scene_id"] if overlaps else scenes(data)[0]["id"] if scene_edits.fingerprint_edits(data) else "S1"


def first_scene(data: dict, char_id: str) -> str:
    shots = sorted((data.get("breakdown") or {}).get("shots") or [], key=lambda s: float(s.get("start") or 0))
    shot = next((s for s in shots if char_id in (s.get("characters") or [])), None)
    return scene_for_range(data, float(shot["start"]), float(shot["start"]) + 1e-6) if shot else scenes(data)[0]["id"]


def task_scene(data: dict, task: dict) -> str:
    return scene_for_range(data, task.get("source_start") or 0, task.get("source_end") or 0)


def offscreen_character(data: dict, char_id: str) -> bool:
    char = next((c for c in (data.get("breakdown") or {}).get("characters", []) if c.get("id") == char_id), {})
    appearance = str(char.get("appearance") or "")
    return bool(re.search(r"未见|不出镜|画外|off[- ]screen|not visible", appearance, re.I)
                or "画外" in str(char.get("name") or "") + str(char.get("role") or ""))


def camera_asset_valid(data: dict, aid: str | None, directory: Path | None = None) -> bool:
    asset = next((a for a in data.get("assets", []) if a.get("id") == aid), None)
    if not asset or asset.get("type") != "image":
        return False
    if directory is None and data.get("id"):
        candidate = store.project_dir(data["id"])
        if candidate.is_dir():
            directory = candidate
    if asset.get("scene_camera_binding") or asset.get("scene_camera_scene"):
        return scene_plan.camera_valid(data, aid, asset.get("scene_camera_scene") or asset.get("camera_scene"), directory)
    if directory is not None:
        path = (directory / str(asset.get("file") or "")).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            return False
    if asset.get("camera_character"):
        return asset.get("camera_binding") == camera_binding(data, asset["camera_character"], directory,
            scene_id=None if asset.get("camera_binding_legacy") else asset.get("camera_scene"))
    return True  # Legacy selected assets predate camera binding metadata.


def camera_image_for(data: dict, char_id: str, scene_id: str) -> str | None:
    if offscreen_character(data, char_id):
        return None
    char = next((c for c in (data.get("replacements") or {}).get("characters", []) if c.get("source_id") == char_id), {})
    selected = char.get("camera_assets") or {}
    candidates = [selected.get(scene_id)]
    records = list((data.get("images") or {}).values())
    candidates += [r.get("asset_id") for r in reversed(records)
                   if r.get("for_task") == f"camera:{char_id}:{scene_id}" and r.get("state") == "completed"]
    if scene_id == first_scene(data, char_id):
        candidates.append(char.get("camera_asset_id"))
        candidates += [r.get("asset_id") for r in reversed(records)
                       if r.get("for_task") == f"camera:{char_id}" and r.get("state") == "completed"]
    return next((aid for aid in candidates if aid and camera_asset_valid(data, aid)), None)


def camera_reference_matches(data: dict, aid: str, char_id: str, scene_id: str) -> bool:
    """Identify a scene camera even after its library asset has been deleted."""
    return any(a.get("id") == aid and a.get("camera_character") == char_id
               and a.get("camera_scene") == scene_id for a in data.get("assets", [])) or any(
        r.get("for_task") == f"camera:{char_id}:{scene_id}" and aid in {r.get("asset_id"), r.get("id")}
        for r in (data.get("images") or {}).values())



def clean_camera_selections(data: dict) -> None:
    """Persist only existing, current choices; keep the read-side fallback intact."""
    scene_ids = [s["id"] for s in scenes(data)]
    for character in (data.get("replacements") or {}).get("characters", []):
        cid = character.get("source_id")
        if offscreen_character(data, cid):
            character["camera_assets"] = {}
            character.pop("camera_asset_id", None)
            continue
        selected = {sid: camera_image_for(data, cid, sid) for sid in scene_ids}
        character["camera_assets"] = selected
        if character.get("camera_asset_id") and not camera_asset_valid(data, character["camera_asset_id"]):
            character["camera_asset_id"] = selected.get(first_scene(data, cid))

def camera_images(data: dict, scene_id: str | None = None, *, _legacy: bool = False) -> dict:
    if not _legacy and scene_plan.enabled(data):
        sid = scene_id or scenes(data)[0]["id"]
        aid = scene_plan.camera_image_for(data, sid)
        return {s["source_id"]: aid for s in scene_plan.effective_subjects(data, sid)
                if s["kind"] == "character" and s["on"] and aid}
    chars = {c.get("source_id") for c in (data.get("replacements") or {}).get("characters", [])}
    chars.update((r.get("for_task") or "").split(":")[1] for r in (data.get("images") or {}).values()
                 if (r.get("for_task") or "").startswith("camera:"))
    return {cid: aid for cid in sorted(chars - {None})
            if (aid := camera_image_for(data, cid, scene_id or first_scene(data, cid)))}


def camera_binding(data: dict, char_id: str, directory: Path | None = None, *, scene_id: str | None = None) -> str:
    rep = data.get("replacements") or {}
    character = next((c for c in rep.get("characters", []) if c.get("source_id") == char_id), {})
    selected = {"characters": [{k: v for k, v in character.items() if k not in {"camera_asset_id", "camera_assets"}}],
                "product": rep.get("product"), "scene": rep.get("scene")}
    # Old generated assets retain their exact binding until explicitly regenerated.
    if scene_id is not None:
        scene = next((s for s in scenes(data) if s["id"] == scene_id), {})
        selected["camera_scene"] = {"scene_id": scene_id, "description": scene.get("description"),
                                    "name": scene.get("name"), "lighting": scene.get("lighting")}
    return fingerprint({"replacements": selected, "assets": data.get("assets", [])}, directory, _legacy=True)


def camera_stale_assets(data: dict, directory: Path | None = None) -> list[str]:
    """Read-only per-asset state for the editor, including projects without scripts."""
    return [a["id"] for a in data.get("assets", []) if (a.get("scene_camera_binding")
            or a.get("scene_camera_scene")) and not scene_plan.camera_valid(data, a["id"],
                a.get("scene_camera_scene") or a.get("camera_scene"), directory)] + [a["id"] for a in data.get("assets", []) if a.get("camera_character")
            and a.get("camera_binding") != camera_binding(data, a["camera_character"], directory,
                                                          scene_id=None if a.get("camera_binding_legacy") else a.get("camera_scene"))]


def camera_stale_images(data: dict, directory: Path | None = None) -> list[str]:
    stale = []
    for iid, record in (data.get("images") or {}).items():
        if (record.get("for_task") or "").startswith("scene-camera:"):
            if record.get("state") != "completed":
                sid = record["for_task"].split(":", 1)[1]
                if sid not in {s["id"] for s in scenes(data)}:
                    stale.append(iid)
                    continue
                current = scene_plan.binding(data, sid, directory)
                anchor = record.get("anchor_asset_id")
                if record.get("scene_camera_binding", record.get("camera_binding")) != current or (
                        anchor and anchor not in {scene_plan.camera_image_for(data, s["id"], directory)
                                                 for s in scenes(data) if s["id"] != sid}):
                    stale.append(iid)
            continue
        cid = record.get("camera_character")
        if not cid or record.get("state") == "completed":
            continue
        sid = record.get("camera_scene")
        binding_changed = record.get("camera_binding") != camera_binding(data, cid, directory, scene_id=sid)
        anchor = record.get("anchor_asset_id")
        anchor_changed = anchor and anchor not in {camera_image_for(data, cid, s["id"])
                                                   for s in scenes(data) if s["id"] != sid}
        if binding_changed or anchor_changed:
            stale.append(iid)
    return stale


def task_cameras(data: dict, char_id: str, task: dict) -> set[str]:
    scene_ids = [task_scene(data, task)]
    scene_ids += [r["scene_id"] for r in scene_overlaps(data, task.get("source_start") or 0,
                                                       task.get("source_end") or 0) if r["overlap"] >= 1]
    if scene_plan.enabled(data):
        return {aid for sid in scene_ids if any(s["source_id"] == char_id and s["on"]
                for s in scene_plan.effective_subjects(data, sid))
                and (aid := scene_plan.camera_image_for(data, sid))}
    return {aid for sid in scene_ids if (aid := camera_image_for(data, char_id, sid))}


def task_product(data: dict, task: dict) -> dict:
    """The product selected for this scene, including a scene-only photo."""
    if scene_plan.enabled(data):
        row = next((s for s in scene_plan.effective_subjects(data, task_scene(data, task))
                    if s["kind"] == "product"), {})
        return {**row, "action": "replace" if row.get("on") else "keep"}
    return (data.get("replacements") or {}).get("product") or {}


def product_present(data: dict, task: dict) -> bool:
    moments = (data.get("breakdown") or {}).get("productMoments") or []
    if not moments:
        return True  # no evidence that the requested replacement is absent
    for m in moments:
        try:
            if float(m["end"]) > float(task.get("source_start") or 0) and float(m["start"]) < float(task.get("source_end") or 0):
                return True
        except (KeyError, TypeError, ValueError):
            return True
    return False


def product_camera(data: dict, task: dict, directory: Path | None = None) -> str | None:
    if scene_plan.enabled(data):
        product = task_product(data, task)
        aid = scene_plan.camera_image_for(data, task_scene(data, task), directory)
        return aid if product.get("on") and aid in (task.get("image_ids") or []) else None
    prod = (data.get("replacements") or {}).get("product") or {}
    for asset in data.get("assets", []):
        if (asset["id"] in (task.get("image_ids") or []) and asset.get("camera_character")
                and asset.get("camera_product_id") == prod.get("asset_id")
                and asset.get("camera_binding") == camera_binding(data, asset["camera_character"], directory, scene_id=None if asset.get("camera_binding_legacy") else asset.get("camera_scene"))):
            return asset["id"]
    return None


def fingerprint(data: dict, directory: Path | None = None, *, _legacy: bool = False) -> str:
    edits = scene_edits.fingerprint_edits(data) if not _legacy else {}
    if not _legacy and scene_plan.enabled(data):
        # Script provenance excludes reference-frame choices and selected camera
        # versions: switching either requires at most an image-ID synchronization.
        rows = [{"scene_id": scene["id"], **scene_plan.visual_content(data, scene["id"], directory)}
                for scene in scenes(data)]
        return 'script-v2:' + hashlib.sha256(json.dumps([rows, edits], ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
    rep = {k:v for k,v in (data.get("replacements") or {}).items() if k != "scene_edits"}
    if edits:
        rep["scene_edits"] = edits
    cams = camera_images(data, _legacy=_legacy)
    if len(scenes(data)) == 1 and any("camera_assets" in c for c in rep.get("characters", [])):
        # Schema-only migration must not invalidate an otherwise identical old script.
        rep = {**rep, "characters": [{**{k: v for k, v in c.items() if k != "camera_assets"},
                                      "camera_asset_id": cams.get(c.get("source_id"))}
                                     if "camera_assets" in c else c for c in rep.get("characters", [])]}
    if len(scenes(data)) > 1:
        cams = {c["source_id"]: {s["id"]: camera_image_for(data, c["source_id"], s["id"]) for s in scenes(data)}
                for c in rep.get("characters", [])}
    ids = {c.get("asset_id") for c in rep.get("characters", []) if c.get("asset_id")}
    ids.update(v for value in cams.values() for v in (value.values() if isinstance(value, dict) else [value]) if v)
    if (rep.get("product") or {}).get("asset_id"):
        ids.add(rep["product"]["asset_id"])
    assets = {a["id"]: a for a in data.get("assets", [])}
    facts = []
    for aid in sorted(ids):
        a = assets.get(aid, {})
        actual = a.get("sha256")
        if directory is not None and a.get("file"):
            path = (directory / a["file"]).resolve()
            # Never follow an asset path out of its project, including symlinks.
            actual = store.sha256_file(path) if path.is_relative_to(directory.resolve()) and path.is_file() else "missing"
        facts.append({"id": aid, "sha256": a.get("sha256"), "actual_sha256": actual,
                      "roles": a.get("roles"), "description": a.get("description")})
    raw = json.dumps([rep, cams, facts], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def script_current(data: dict, directory: Path | None = None) -> bool:
    """Accept old provenance only when its original inputs still verify exactly."""
    saved = (data.get('script') or {}).get('replacements_fingerprint')
    if saved == fingerprint(data, directory):
        return True
    if not scene_plan.enabled(data) or not saved or str(saved).startswith('script-v2:'):
        return False
    # Historical digests did not encode keep versus remove. They cannot prove
    # a removal was already present when that script was generated.
    if any(scene_plan.visual_content(data, s['id'], directory)['removed_subjects'] for s in scenes(data)):
        return False
    assets = {a['id']: a for a in data.get('assets', [])}
    tasks = (data.get('script') or {}).get('tasks', [])
    # A historical script may use the previous camera after a failed sync.
    # Reconstruct from those references, then from current selected cameras.
    for use_script in (True, False):
        for normalization in ('raw', 'rounded', 'saved'):
            rows = []
            for scene in scenes(data):
                sid = scene['id']
                aid = scene_plan.camera_image_for(data, sid, directory)
                if use_script:
                    candidates = [a for t in tasks if task_scene(data, t) == sid
                                  for a in t.get('image_ids') or []
                                  if a in assets and scene_plan._asset_scene(data, assets[a])[0] == sid]
                    if candidates:
                        aid = candidates[0]
                if aid and not scene_plan.camera_valid(data, aid, sid, directory):
                    break
                asset = assets.get(aid) or {}
                frame = scene_plan.camera_frame(data, asset)
                at = frame.get('frame_at')
                if normalization == 'rounded' and isinstance(at, (int, float)):
                    at = round(at, 2)
                old = (asset.get('scene_camera_binding') if normalization == 'saved' else None)
                old = old or scene_plan._legacy_binding(data, sid, directory, _old_at=at,
                                                        _normalize=normalization == 'rounded')
                rows.append({'scene_id': sid, 'binding': old, 'camera_asset': aid})
            if len(rows) != len(scenes(data)):
                continue
            edits = scene_edits.fingerprint_edits(data)
            value = [rows, edits] if edits else rows
            digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest()
            if saved == digest:
                return True
    return False


def replaced_people(data: dict, task: dict | None = None) -> list[dict]:
    if scene_plan.enabled(data):
        scene_ids = [task_scene(data, task)] if task is not None else [s["id"] for s in scenes(data)]
        result = []
        for sid in scene_ids:
            for row in scene_plan.effective_subjects(data, sid):
                if row["kind"] == "character" and row["on"]:
                    person = {**row, "action": "replace"}
                    if person not in result:
                        result.append(person)
        return result
    people = [c for c in (data.get("replacements") or {}).get("characters", []) if c.get("action") == "replace" and not offscreen_character(data, c.get("source_id"))]
    if task is None:
        return people
    # Only use explicit source evidence to narrow a cast; unknown means conservative coverage.
    shots = [s for s in (data.get("breakdown") or {}).get("shots", [])
             if float(s.get("end") or 0) > float(task.get("source_start") or 0)
             and float(s.get("start") or 0) < float(task.get("source_end") or 0)]
    visible = {cid for s in shots for cid in s.get("characters") or []}
    return [c for c in people if c.get("source_id") in visible] if visible else people


# Bilingual groups detect Chinese analysis leaking into English prompts (and vice versa).
KEYWORDS = [
    ("open back", "backless", "露背", "背部较开放"), ("flowy", "垂坠", "垂坠感", "轻盈"),
    ("blonde", "blond", "金发", "金色长发", "金色头发"),
    ("black", "黑色", "黑发"), ("white", "白色"), ("red", "红色", "红发"),
    ("blue", "蓝色"), ("green", "绿色", "军绿"), ("yellow", "黄色"),
    ("brown", "brunette", "棕色", "棕发"), ("pink", "粉色"), ("purple", "紫色"),
    ("beige", "米色"), ("gray", "grey", "灰色"), ("gold", "金色"), ("silver", "银色"),
    ("long hair", "long-haired", "长发"), ("short hair", "short-haired", "短发"),
    ("curly", "卷发"), ("ponytail", "马尾"), ("bald", "光头"),
    ("dress", "gown", "连衣裙", "长裙"), ("skirt", "裙子", "短裙"),
    ("sleeveless", "无袖"), ("high-neck", "high neck", "turtleneck", "高领"),
    ("slit", "开衩", "开叉"), ("heels", "高跟鞋"), ("strappy", "细带"),
    ("cardigan", "开衫"), ("shirt", "衬衫"), ("t-shirt", "t恤"),
    ("trousers", "pants", "裤子", "阔腿裤"), ("jeans", "牛仔裤"),
    ("jacket", "夹克"), ("coat", "外套", "大衣"), ("sweater", "毛衣"),
    ("sneakers", "运动鞋"), ("boots", "靴子"), ("hat", "帽子"),
    ("slim", "slender", "修长", "纤细", "修身"), ("pale", "fair skin", "白皙", "浅色"),
]


def _contains(text: str, term: str) -> bool:
    if re.search(r"[a-z]", term):
        return bool(re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text, re.I))
    return term in text


def source_mappings(data: dict, task: dict) -> list[dict]:
    rep, pack = data.get("replacements") or {}, data.get("breakdown") or {}
    cams, ids = camera_images(data, task_scene(data, task)), task.get("image_ids") or []
    out = []
    for c in replaced_people(data, task):
        source = next((s for s in pack.get("characters", []) if s.get("id") == c.get("source_id")), {})
        aid = c.get("asset_id") if scene_plan.enabled(data) and c.get("asset_id") in ids else next((a for a in ids if a in ({c.get("asset_id")} | task_cameras(data, c.get("source_id"), task))), None)
        out.append({"source_id": c.get("source_id"), "original": " ".join(str(source.get(k) or "") for k in ("appearance", "outfit")),
                    "reference": f"the person from @image{ids.index(aid) + 1}" if aid else None,
                    "user_requirement": c.get("description") or ""})
    prod = task_product(data, task)
    if prod.get("action") == "replace":
        aid = prod.get("asset_id")
        if aid not in ids:
            aid = product_camera(data, task)
        # Scene context is supplied to the model; keywords use only product-bearing scene clauses.
        scene = [s for s in (scenes(data) if scene_edits.fingerprint_edits(data) else pack.get("scene", [])) if re.search(
            r"商品|产品|服装|穿|手持|包装|product|outfit|wear|holding", json.dumps(s, ensure_ascii=False), re.I)]
        original = json.dumps([pack.get("productMoments") or [], scene], ensure_ascii=False)
        out.append({"source_id": "product", "original": original,
                    "reference": f"the outfit/product from @image{ids.index(aid) + 1}" if aid in ids else None,
                    "user_requirement": prod.get("description") or ""})
    return out


def _appearance_groups(mapping: dict):
    return [group for group in KEYWORDS
            if any(_contains(mapping["original"], term) for term in group)
            and not any(_contains(mapping["user_requirement"], term) for term in group)]


def _dialogue_key(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


def _without_dialogue_quotes(text: str, dialogue: str) -> str:
    # Apostrophes inside words (I'm, can't, girl's) are not quote delimiters.
    quotes = r'''(?<!\w)'((?:\w'\w|[^'])*?)'(?!\w)|"(.*?)"|“(.*?)”|‘(.*?)’(?!\w)|「(.*?)」'''
    key = _dialogue_key(dialogue)

    def replace(match):
        quoted = _dialogue_key(next(g for g in match.groups() if g is not None))
        return " " if quoted and quoted in key else match.group(0)

    return re.sub(quotes, replace, text, flags=re.S)


def conflicts(data: dict, task: dict) -> list[str]:
    text = "\n".join(_without_dialogue_quotes(str(task.get(k) or ""), str(task.get("dialogue") or ""))
                     for k in TEXT_FIELDS)
    found = set()
    for mapping in source_mappings(data, task):
        for group in _appearance_groups(mapping):
            found.update(term for term in group if _contains(text, term))
    return sorted(found)


DIALOGUE_FIX = "点下方‘改写冲突台词’，模型会先给出改写版本，你确认后再采用；也可以选‘保持原台词’"


def dialogue_conflicts(data: dict, task: dict) -> list[dict]:
    if task.get("dialogue_choice") in {"keep", "adopt"} or task.get("dialogue_keep_original"):
        return []
    dialogue = str(task.get("dialogue") or "")
    terms, aliases = set(), set()
    for mapping in source_mappings(data, task):
        for group in _appearance_groups(mapping):
            if any(_contains(dialogue, term) for term in group):
                terms.add(group[0])
                aliases.update(group)
    if not terms:
        return []
    ordered = sorted(terms)
    sentences = [s.strip() for s in re.findall(r"[^.!?。！？\n]+(?:[.!?。！？]+|(?=\n)|$)", dialogue) if s.strip()]
    details = [line for line in sentences if any(_contains(line, term) for term in aliases)]
    title = f"台词里有 {len(terms)} 处提到原片的服装特征（{'、'.join(ordered)}），和新商品可能不符"
    return [{"code":"dialogue_conflict", "step":"04", "title":title, "fix":DIALOGUE_FIX,
             "details":details, "terms":ordered,
             "matched_terms": sorted(term for term in aliases if _contains(dialogue, term)),
             "segment_ids":[task.get("id")]}]


def dialogue_mismatches(data: dict, task: dict) -> list[str]:
    return [item["title"] + "；" + item["fix"] for item in dialogue_conflicts(data, task)]


def clean_bbox(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        nums = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in nums):
        return None
    x0, y0, x1, y1 = [max(0.0, min(1.0, v)) for v in nums]
    return [x0, y0, x1, y1] if x1 > x0 and y1 > y0 else None


def frame_caption_masks(data: dict, at: float) -> tuple[list[dict], bool]:
    """Sample the per-overlay clip rule, including its existing ±0.15s padding."""
    return caption_masks(data, {'mode': 'video', 'blank_captions': True,
                                'source_start': max(0, at), 'source_end': at + .001}, frame_at=at)


def frame_caption_overlap(data: dict, at: float, masks: list[dict]) -> bool:
    """Conservative warning when subject boxes are absent; never skip removal."""
    pack = data.get('breakdown') or {}
    subjects = [row for key in ('shots', 'productMoments') for row in pack.get(key) or []
                if float(row.get('start') or 0) <= at < float(row.get('end') or 0)
                and (row.get('characters') or row.get('product'))]
    for subject in subjects:
        bbox = clean_bbox(subject.get('bbox'))
        if bbox is None:
            return bool(masks)
        if any(max(bbox[0], m['bbox'][0]) < min(bbox[2], m['bbox'][2])
               and max(bbox[1], m['bbox'][1]) < min(bbox[3], m['bbox'][3]) for m in masks):
            return True
    return False


def caption_masks(data: dict, task: dict, *, frame_at: float | None = None) -> tuple[list[dict], bool]:
    if task.get("mode") != "video" or task.get("blank_captions") is not True:
        return [], False
    start = max(0.0, float(task.get("source_start") or 0))
    end = min(float(task.get("source_end") or start + task.get("duration", 0)), start + 15)
    masks, missing = [], False
    for overlay in (data.get("breakdown") or {}).get("overlays", []):
        if overlay.get("kind") not in {"caption", "typography", "sticker"}:
            continue
        try:
            a, b = float(overlay["start"]), float(overlay["end"])
        except (KeyError, TypeError, ValueError):
            missing = True
            continue
        if not math.isfinite(a) or not math.isfinite(b) or b <= a:
            missing = True
            continue
        if frame_at is not None and not a - .15 <= frame_at < b + .15:
            continue
        a, b = max(start, a - .15), min(end, b + .15)
        if b <= a:
            continue
        original = clean_bbox(overlay.get("bbox"))
        if original is None:
            missing = True
            continue
        x0, y0, x1, y1 = original
        # Normalize arithmetic noise before FFmpeg converts coordinates to pixels.
        bbox = [round(v, 9) for v in (max(0, x0 - .03), max(0, y0 - .04),
                                     min(1, x1 + .03), min(1, y1 + .04))]
        mask = {"bbox": bbox, "start": round(a - start, 3), "end": round(b - start, 3),
                "text": overlay.get("text", ""), "kind": overlay["kind"],
                "system": overlay.get("system") or overlay["kind"], "source_bboxes": [original],
                "padding": {"x": .03, "y": .04, "time": .15}}
        if bbox[3] - bbox[1] > .35 + 1e-9:
            mask["motion_warning"] = (f"这段字幕很大（覆盖画面高度 {bbox[1] * 100:g}–{bbox[3] * 100:g}%），"
                                      "这段时间里下半身动作的参考会变弱")
        masks.append(mask)
    return masks, missing



# Presentation metadata only: the original blocking conditions and errors stay intact.
ISSUE_INFO = {
    "invalid_camera_image": ("05", "图1 指向一张已删除或过期的主镜头图", "换成本场景当前的主镜头图并保存脚本。"),
    "analysis_no_caption_bbox": ("02", "这次拆解没有记录字幕位置", "点‘用所选模型重新拆解’；完成后继续后面的步骤。"),
    "person_keep_with_image": ("03", "人物处理为保持，但本段选了新人物图", "把对应人物的‘处理’改为‘替换’，选择人物图并保存；不换人则在第 04 步（对照和修改）里这一段的新分镜中去掉该图。"),
    "person_image_mismatch": ("03", "本段人物图与替换设定不一致", "在对应人物行选择正确的人物图或主镜头图，保存替换设定。"),
    "product_mismatch": ("03", "商品处理或商品图与本段不一致", "把商品‘处理’设为‘替换’并选择本段商品图，保存；不换商品则在第 04 步（对照和修改）里这一段的新分镜中去掉该图。"),
    "camera_image_stale": ("03", "主镜头图的替换依据已变化", "保存当前人物和商品设定，点对应人物的‘生成预览’，确认生图（付费）后会自动在用；已有合适版本可点“改用这张”。"),
    "missing_camera_image": ("03", "换人前还需要主镜头图", "保存替换设定，点对应人物的‘生成预览’，确认生图（付费）完成后会自动在用，分段图1会跟随更新。"),
    "script_stale": ("04", "新脚本需要按当前替换设定重新生成", "重新生成新脚本即可（会自动按新规则写），不用手动删改。"),
    "performance_has_motion": ("04", "动作描述里有 {n} 处身体动作", "重新生成新脚本即可（会自动按新规则写），不用手动删改。"),
    "anchor_order": ("04", "动作描述里有 {n} 处台词顺序不一致", "重新生成新脚本即可（会自动按新规则写），不用手动删改。"),
    "motion_additions": ("04", "表演说明里有 {n} 处和原片时机不一致", "可以改这句，也可以不用改，继续生成。"),
    "appearance_conflict": ("04", "动作描述里有 {n} 处原片外观冲突", "重新生成新脚本即可（会自动按新规则写），不用手动删改。"),
    "reference_mapping": ("04", "脚本里的参考图编号需要更新", "核对图号与对应素材后保存脚本，或重新生成新脚本。"),
    "missing_dialogue": ("04", "口播段落缺少台词", "在第 04 步（对照和修改）的“提示词”里补充本段台词并保存，或重新生成新脚本。"),
    "no_images": ("05", "这一段还没选参考图", "在本段点选参考图，先点人物或主镜头图。"),
    "invalid_image": ("05", "本段参考图不存在或不是图片", "取消失效参考图，重新点选素材库中的图片。"),
    "product_missing_image": ("05", "本段缺少替换商品参考图", "选入第 03 步的商品图，或包含当前商品的主镜头图。"),
    "person_replace_missing_image": ("05", "本段缺少替换人物参考图", "选入对应替换人物的主镜头图或人物图。"),
    "image1_not_person": ("05", "口播段落的图1必须是出镜人物", "把人物或主镜头图放到图1，再保存脚本。"),
    "video_reference_required": ("05", "动作或运镜复刻需要原片片段", "把本段‘参考方式’改为‘原片片段 + 图’。"),
}
SCRIPT_ISSUES = {"script_stale", "performance_has_motion", "anchor_order", "appearance_conflict"}


def add_issue(issues: list[dict], code: str, details: list[str]) -> None:
    if not details:
        return
    step, title, fix = ISSUE_INFO[code]
    issue = next((i for i in issues if i["code"] == code), None)
    if issue is None:
        issue = {"code": code, "step": step, "title": title, "fix": fix, "details": []}
        issues.append(issue)
    if code == "motion_additions":
        issue["severity"] = "warning"
    issue["details"] = list(dict.fromkeys(issue["details"] + details))
    issue["title"] = title.format(n=len(issue["details"]))


def order_issues(issues: list[dict]) -> list[dict]:
    early = any(i["step"] < "04" for i in issues)
    for issue in issues:
        if early and issue["code"] in SCRIPT_ISSUES | {"camera_scene_mismatch"}:
            issue["fix"] = "完成前面的步骤后，重新生成新脚本即可（会自动按新规则写），不用手动删改"
    return sorted(issues, key=lambda i: i["step"])


def motion_warning_fingerprint(data: dict, task: dict) -> str:
    ignored = {"chosen_rev", "replacement_safety", "replacement_safety_key", "replacement_safety_check"}
    values = [data.get("motion_warning_epoch"), {k: v for k, v in (data.get("script") or {}).items() if k != "tasks"},
              {k: v for k, v in task.items() if k not in ignored}]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def attach_motion_warning(data: dict, task: dict, check: dict) -> None:
    details = list(dict.fromkeys((check.get("action_comparison") or {}).get("errors") or []))
    # Also accept historical comparison issues while keeping all other gates.
    for issue in check.get("issues") or []:
        if issue["code"] == "motion_additions":
            issue["severity"] = "warning"
            details = list(dict.fromkeys(details + issue.get("details", [])))
    if details:
        key = motion_warning_fingerprint(data, task)
        check["motion_warning"] = {"fingerprint": key, "details": details,
            "content": [task.get(k) for k in ("action", "story", "direction", "dialogue", "mode", "source_start", "source_end", "image_ids")],
            "dismissed": (data.get("motion_warning_dismissals") or {}).get(task.get("id")) == key}


def check(data: dict, task: dict, directory: Path | None = None, *, compare_motion: bool = False) -> dict:
    errors, issues = [], []
    by_scene = scene_plan.enabled(data)

    def image_for(cid, sid):
        if by_scene:
            return scene_plan.camera_image_for(data, sid, directory) if any(
                s["kind"] == "character" and s["source_id"] == cid and s["on"]
                for s in scene_plan.effective_subjects(data, sid)) else None
        return camera_image_for(data, cid, sid)

    def block(code, message, **context):
        errors.append(message)
        add_issue(issues, code, [message])
        if context:
            issue = next(i for i in issues if i['code'] == code)
            affected = issue.setdefault('affected', [])
            if context not in affected:
                affected.append(context)

    assets = {a["id"]: a for a in data.get("assets", [])}
    ids = task.get("image_ids") or []
    people, cams = replaced_people(data, task) if by_scene else replaced_people(data), camera_images(data, task_scene(data, task))
    relevant = replaced_people(data, task)
    prod = task_product(data, task)
    if not ids:
        block("no_images", NO_IMAGES)
    if by_scene and len(ids) > 4:
        message = "每段最多 4 张参考图，请减少本段参考图或合成一张商品板"
        errors.append(message)
        issues.append({"code": "too_many_images", "step": "05", "title": "这一段参考图超过 4 张",
            "fix": "减少本段参考图，或把商品图合为一张商品板。", "details": [message]})
    for aid in ids:
        a = assets.get(aid)
        if not a or a.get("type") != "image":
            block("invalid_image", f"参考图 {aid} 不存在或不是图片，请重新选择", asset_id=aid)
            continue
        roles = set(a.get("roles") or [])
        person_image = "person_identity" in roles or ("target_state" in roles and aid != prod.get("asset_id"))
        if by_scene and not relevant and (a.get("scene_camera_binding") or a.get("scene_camera_scene")):
            person_image = False
        matching_character = next((c.get('source_id') for c in (data.get('replacements') or {}).get('characters', [])
                                   if c.get('asset_id') == aid), None) or a.get('camera_character')
        if not matching_character:
            visible_ids = {cid for shot in (data.get('breakdown') or {}).get('shots', [])
                           if float(shot.get('end') or 0) > float(task.get('source_start') or 0)
                           and float(shot.get('start') or 0) < float(task.get('source_end') or 0)
                           for cid in shot.get('characters') or []}
            candidates = [c.get('id') for c in (data.get('breakdown') or {}).get('characters', [])
                          if not offscreen_character(data, c.get('id')) and (not visible_ids or c.get('id') in visible_ids)]
            matching_character = candidates[0] if len(candidates) == 1 else None
        if person_image and not any(
                aid in ({c.get("asset_id")} | task_cameras(data, c.get("source_id"), task)) or
                (by_scene and scene_plan.camera_valid(data, aid, task_scene(data, task), directory)) or
                (a.get("camera_character") == c.get("source_id")) for c in people):
            block("person_keep_with_image" if not people else "person_image_mismatch", "第 03 步人物处理是‘保持’，但这一段用了新人物图。要换人请把处理改成‘替换’并重新生成新脚本；不换人请去掉这张图。"
                          if not people else f"人物图 {a.get('label', aid)} 不属于第 03 步的任何‘替换’人物，请选择该人物的人物图或主镜头图，并重新生成新脚本。",
                          asset_id=aid, character_id=matching_character)
        if ("product_truth" in roles or ("target_state" in roles and aid == prod.get("asset_id"))) and (prod.get("action") != "replace" or aid != prod.get("asset_id")):
            block("product_mismatch", "第 03 步商品处理是‘保持’或所选商品图与这一段不一致。要换商品请把处理改成‘替换’并选择这张图、重新生成新脚本；不换商品请去掉这张图。")
        if a.get("camera_character") and a.get("camera_binding") != camera_binding(data, a["camera_character"], directory, scene_id=None if a.get("camera_binding_legacy") else a.get("camera_scene")):
            message = "主镜头图的替换依据已变化，请按当前人物和商品重新生成主镜头图，再重新生成新脚本。"
            errors.append(message)
            step, title, fix = ISSUE_INFO["camera_image_stale"]
            issues.append({"code": "camera_image_stale", "step": step, "title": title, "fix": fix,
                           "details": [message], "asset_id": aid, "character_id": a["camera_character"],
                           "scene_id": a.get("camera_scene") or first_scene(data, a["camera_character"])})
        if by_scene and (a.get("scene_camera_binding") or a.get("scene_camera_scene")) and not camera_asset_valid(data, aid, directory):
            message = "这一场的设定已变，需要重新生成主镜头图，再重新生成新脚本。"
            block("camera_image_stale", message)
            issues[-1].update(asset_id=aid, scene_id=a.get("scene_camera_scene") or a.get("camera_scene"))
    if prod.get("action") == "replace" and product_present(data, task) and prod.get("asset_id") not in ids and not product_camera(data, task, directory):
        block("product_missing_image", "这一段缺少替换商品的参考图，请选入第 03 步的商品图，或先生成包含当前商品的主镜头图。")
    if task.get("kind") in {"speaker", "motion", "camera"}:
        for c in relevant:
            if not set(ids) & {c.get("asset_id"), cams.get(c.get("source_id"))}:
                block("person_replace_missing_image", f"这一段缺少替换人物 {c.get('source_id')} 的参考图，请选入该人物的主镜头图或人物图。",
                      character_id=c.get("source_id"), asset_id=c.get("asset_id"))
    overlaps = [r for r in scene_overlaps(data, task.get("source_start") or 0, task.get("source_end") or 0)
                if r["overlap"] >= 1]
    required_scenes = list(dict.fromkeys([task_scene(data, task)] + [r["scene_id"] for r in overlaps]))
    if task.get("mode") == "video" and relevant:
        sid = task_scene(data, task)
        first = assets.get(ids[0], {}) if ids else {}
        for required_sid in required_scenes:
            scene = next((s for s in scenes(data) if s["id"] == required_sid), {})
            scene_people = replaced_people(data, {**task, "source_start": (scene_ranges(scene) or [(0, 1)])[0][0],
                "source_end": (scene_ranges(scene) or [(0, 1)])[0][1]}) if by_scene else replaced_people(data, task)
            for c in scene_people:
                cid = c["source_id"]
                expected = image_for(cid, required_sid)
                if expected and (required_sid == sid or expected in ids):
                    continue
                # A present selection omitted from secondary references needs a script repair.
                if expected:
                    message = f"{task.get('id')} 缺少跨场景参考图：场景 {required_sid} 的主镜头图"
                    errors.append(message)
                    issues.append({"code": "camera_scene_mismatch", "step": "05", "title": message,
                        "fix": "重新生成新脚本即可", "details": [message], "character_id": cid,
                        "scene_id": required_sid, "scene_name": scene.get("name", ""),
                        "expected_asset_id": expected, "reference_index": 1, "segment_ids": [task.get("id")]})
                    continue
                segment_ids = list(dict.fromkeys([task.get("id", "本段")] + [t["id"] for t in
                    (data.get("script") or {}).get("tasks", []) if (task_scene(data, t) == required_sid or any(
                        r["scene_id"] == required_sid and r["overlap"] >= 1 for r in scene_overlaps(data,
                            t.get("source_start") or 0, t.get("source_end") or 0)))
                    and c in replaced_people(data, t) and t.get("mode") == "video"]))
                title = f"{segment_ids[0]} 在场景 {required_sid}（{scene.get('name', '')}），还缺 {cid} 在这个场景的主镜头图"
                errors.append(title)
                issues.append({"code": "missing_camera_image", "step": "03", "title": title,
                    "fix": f"在第 03 步 {cid} 的‘{required_sid} {scene.get('name', '')}’卡片上点‘生成预览’，再点‘确认生图（付费）’，生成后会自动用于这个场景的分段",
                    "details": [title] + segment_ids, "segment_ids": segment_ids, "character_id": cid,
                    "scene_id": required_sid, "scene_name": scene.get("name", "")})
        expected_cameras = {cams.get(c["source_id"]) for c in relevant} - {None}
        if expected_cameras and (not ids or ids[0] not in expected_cameras or "target_state" not in (first.get("roles") or [])):
            c = next((c for c in relevant if first.get("camera_character") == c["source_id"]), relevant[0])
            cid, expected = c["source_id"], cams.get(c["source_id"])
            scene = next((s for s in scenes(data) if s["id"] == sid), {})
            code = "camera_scene_mismatch" if ids and (first.get("camera_character") or first.get("scene_camera_scene") or any(
                ids[0] == image_for(cid, s["id"]) for s in scenes(data))) else "missing_camera_image"
            if code == "missing_camera_image":
                block(code, NEED_CAMERA)
            else:
                message = f"{task.get('id')} 图1不是场景 {sid} 的主镜头图"
                errors.append(message)
                issues.append({"code": code, "step": "05", "title": message,
                               "fix": f"换成 {sid} 的主镜头图并保存脚本。", "details": [message]})
            issues[-1].update(character_id=cid, scene_id=sid, scene_name=scene.get("name", ""),
                              expected_asset_id=expected, segment_ids=[task.get("id")])
    if by_scene:
        # A scene image defines the complete composition even when only its
        # product changes. This check cannot depend on a selected character.
        sid = task_scene(data, task)
        expected = scene_plan.camera_image_for(data, sid, directory)
        if expected and (not ids or ids[0] != expected) and not any(
                i["code"] == "camera_scene_mismatch" and i.get("scene_id") == sid for i in issues):
            message = f"{task.get('id', '本段')} 图1不是场景 {sid} 当前在用的主镜头图"
            errors.append(message)
            scene = next((s for s in scenes(data) if s["id"] == sid), {})
            issues.append({"code": "camera_scene_mismatch", "step": "05", "title": message,
                "fix": f"换成 {sid} 当前在用的主镜头图并保存脚本。", "details": [message],
                "scene_id": sid, "scene_name": scene.get("name", ""),
                "expected_asset_id": expected, "reference_index": 1,
                "segment_ids": [task.get("id")]})
    if not script_current(data, directory):
        block("script_stale", STALE_SCRIPT)
    words = conflicts(data, task)
    if words:
        add_issue(issues, "appearance_conflict", words)
        errors.append("映射安全检查发现原片外观冲突词：" + "、".join(words) + "；请改为对应的 @image 引用后保存脚本。")
    invalid_refs = sorted({m for k in TEXT_FIELDS for m in re.findall(r"@image(\d+)\b", str(task.get(k) or ""))
                           if not 1 <= int(m) <= len(ids)})
    if invalid_refs:
        block("reference_mapping", "参考图编号与本段实际顺序不一致：" + "、".join("@image" + n for n in invalid_refs))
    for item in task.get("reference_mapping_errors") or []:
        if task.get(item.get("field")) == item.get("text"):
            block("reference_mapping", item["message"])
    masks, missing = caption_masks(data, task)
    if missing:
        block("analysis_no_caption_bbox", "这次拆解没有记录字幕位置，请重新拆解后再预览；也可取消‘参考片段去掉字幕区域’，明确保留原字幕。")
    audio = assets.get(task.get("audio_id") or "", {})
    accepted_voice = audio.get("type") == "audio" and task.get("voice_approved") is True
    force_mute = task.get("mode") == "video" and bool(relevant) and not accepted_voice
    from . import motion
    warnings = dialogue_mismatches(data, task)
    unknown = sum(row.get("speaker") == "unknown" for row in task.get("dialogue_lines") or [])
    if unknown:
        warnings.append(f"有 {unknown} 句台词还没确定是谁说的，请在第 04 步（对照和修改）的“提示词”里确认")
    if len(overlaps) > 1 and all(image_for(c["source_id"], sid) in ids
                                 for sid in required_scenes for c in relevant):
        warnings.append(f"{task.get('id', '本段')} 横跨两个场景，已同时放入两个场景的主镜头图")
    rows = motion.source_timeline(data, task)
    for code, messages in (("anchor_order", motion.anchor_errors(task)),
                           ("performance_has_motion", motion.performance_errors(task))):
        errors.extend(messages)
        details = [hit["quote"] for hit in motion.performance_hits(task)] if code == "performance_has_motion" else messages
        add_issue(issues, code, details)
    windows, timing_warnings = motion.dialogue_windows(data, task, directory)
    warnings.extend(timing_warnings)
    comparison = {"status": "incomplete", "reason": "尚未执行，请生成预览进行动作对比" if rows
                  else "原片没有可用的动作时间轴"}
    if task.get("mode") != "video":
        comparison = {"status": "incomplete", "reason": "本段仅使用图片参考，未执行动作对比"}
    if task.get("mode") == "video":
        if not rows:
            warnings.append("原片没有可用的动作时间轴，请补全拆解后核对动作")
        if compare_motion:
            motion_errors, motion_warnings = motion.compare_actions(data, task, rows, report=comparison)
            add_issue(issues, "motion_additions", motion_errors)
            warnings.extend(motion_warnings)
    if task.get("mode") == "video" and relevant and ids and not camera_asset_valid(data, ids[0], directory):
        sid, cid = task_scene(data, task), relevant[0]["source_id"]
        scene_refs = {m[1] for field in TEXT_FIELDS for m in re.finditer(
            r"(?:scene|background|location|setting|lot|room|场景|背景|机位)\s+(?:from\s+)?@image(\d+)\b",
            str(task.get(field) or ""), re.I)}
        merged, retained = [], []
        for issue in issues:
            code = issue["code"]
            if code == "invalid_image":
                affected = [d for d in issue["details"] if d.startswith(f"参考图 {ids[0]} ")]
                other = [d for d in issue["details"] if d not in affected]
                if affected:
                    merged.append({**issue, "details": affected})
                if other:
                    retained.append({**issue, "details": other})
                continue
            related = (code == "script_stale"
                or (code == "camera_image_stale" and issue.get("asset_id") == ids[0])
                or (code in {"missing_camera_image", "camera_scene_mismatch"}
                    and issue.get("scene_id", sid) == sid and issue.get("character_id", cid) == cid)
                or (code == "person_replace_missing_image" and all(cid in d for d in issue["details"]))
                or (code == "reference_mapping" and bool(invalid_refs)
                    and set(invalid_refs) <= scene_refs and not task.get("reference_mapping_errors")))
            (merged if related else retained).append(issue)
        details = list(dict.fromkeys(detail for issue in merged for detail in issue.get("details", [])))
        issues = retained
        errors = [error for error in errors if error not in set(details)]
        expected = image_for(cid, sid)
        title = f"{task.get('id', '本段')} 的图1 指向一张已删除或过期的主镜头图"
        stale_first = bool(assets.get(ids[0], {}).get("camera_character") or assets.get(ids[0], {}).get("scene_camera_scene"))
        errors.append(title + ("；主镜头图的替换依据已变化" if stale_first else ""))
        issues.append({"code": "camera_image_stale" if stale_first else "invalid_camera_image", "step": "05" if expected else "03", "title": title,
                       "fix": f"换成 {sid} 当前的主镜头图" if expected else "到第 03 步重新生成本场景主镜头图",
                       "details": list(dict.fromkeys([title, errors[-1]] + details)), "asset_id": ids[0], "expected_asset_id": expected,
                       "character_id": cid, "scene_id": sid, "reference_index": 1,
                       "segment_ids": [task.get("id")]})
    result = {"errors": list(dict.fromkeys(errors)), "issues": order_issues(issues), "warnings": warnings,
            "action_comparison": comparison, "dialogue_conflicts": dialogue_conflicts(data, task),
            "source_motion_timeline": rows, "dialogue_windows": windows,
            "masks": masks, "force_mute": force_mute,
            "mute_clip": force_mute or task.get("mute_clip") is not False,
            "mute_reason": MUTE_REASON if force_mute else "", "conflicts": words}
    attach_motion_warning(data, task, result)
    return result
