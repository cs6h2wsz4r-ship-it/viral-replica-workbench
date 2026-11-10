"""Seedance and GPT-Image prompt kits, wording copied from Hypit's packages
(@hypit/seedance-kits speaker-v1 / broll-v1 / motion-reference-v1 / camera-reference-v1 and
@hypit/gpt-image-kits phone-ugc-v1, modified Apache-2.0, © Hypit.AI) so requests sent through Jimmy
use the same tested direction. Reference tokens follow Seedance's @image1/@video1/@audio1 order.
"""
from __future__ import annotations

SPEAKER_DEFAULTS = {"composition-stability": "soft-locked", "camera-motion": "none",
                    "edit-rhythm": "continuous-take", "performance": "natural-explainer", "gesture": "natural"}
SPEAKER_CHOICES = {
    "composition-stability": {
        "flexible-ugc": "COMPOSITION: preserve the reference person and setup while allowing normal lively creator-video framing variation.",
        "soft-locked": "COMPOSITION: keep the speaker in the same general region and preserve the reference composition, allowing natural posture changes and tiny crop or scale breathing.",
        "strict-locked": "COMPOSITION: preserve subject placement, perspective, left-right orientation, background relationship, and framing envelope throughout the shot.",
    },
    "camera-motion": {
        "none": "CAMERA: hold a steady fixed framing.",
        "subtle-punch-in-return": "CAMERA: use one subtle push-in on the most important phrase, then return smoothly to the original framing.",
    },
    "edit-rhythm": {
        "continuous-take": "EDIT RHYTHM: one continuous creator explanation with natural breath timing.",
        "pause-trim-jump-cuts": "EDIT RHYTHM: polished pause-trim jump cuts at phrase boundaries while keeping the framing coherent.",
    },
    "performance": {
        "natural-explainer": "PERFORMANCE: engaged, friendly conversation with responsive intonation and clear emphasis as the thought develops. Let gaze, expression, and gesture convey the speaker's attitude to what they are saying.",
        "high-energy-ugc": "PERFORMANCE: vivid, energetic short-form delivery with nimble phrasing, animated intonation, and emphatic key ideas. Let the voice and visible reactions develop together as the speaker makes their point.",
        "calm-authority": "PERFORMANCE: composed, thoughtful conviction with measured phrasing and purposeful vocal emphasis. Let focused attention and small, deliberate reactions express how the speaker weighs the idea and reaches a judgment.",
        "reactive-playful": "PERFORMANCE: playful, responsive delivery with teasing inflection, lively timing, and an audible smile where the thought amuses the speaker. Let curiosity, skepticism, or delight come through in the voice and matching visible reactions as the passage calls for them.",
    },
    "gesture": {
        "restrained": "GESTURE: restrained body language; let face, eyes, and voice carry the meaning.",
        "compact": "GESTURE: compact explanatory hand movement close to the torso and inside the frame.",
        "natural": "GESTURE: natural creator gestures such as open palms, small pointing beats, nods, and posture shifts where the script calls for them.",
        "expressive": "GESTURE: richer hand, shoulder, and posture changes that support the spoken idea while remaining physically coherent.",
    },
}

BROLL_DEFAULTS = {"material-mode": "practical-real", "story-shape": "single-moment", "edit-language": "continuous-shot",
                  "camera-language": "reference-locked", "motion-intensity": "readable"}
BROLL_CHOICES = {
    "material-mode": {
        "practical-real": "MATERIAL MODE: practical real-world UGC footage with believable physical causality and a lived-in phone-camera feeling.",
        "screen-demo": "MATERIAL MODE: a practical filmed screen demo. Interface elements remain physically contained on the device screen.",
        "product-beauty": "MATERIAL MODE: product beauty footage focused on object presence, packaging, texture, tabletop detail, or studio product motion.",
        "proof-capture": "MATERIAL MODE: proof capture such as a website, certificate, receipt, review, chart, or credibility page, treated as visual evidence.",
        "stylized-vfx": "MATERIAL MODE: an intentional visual-effects metaphor used as an expressive explainer rather than practical documentation.",
    },
    "story-shape": {
        "single-moment": "STORY SHAPE: one continuous visual moment with a small readable change or payoff.",
        "reaction-beat": "STORY SHAPE: stimulus, realization, then a clearly visible reaction.",
        "process-demo": "STORY SHAPE: a stepwise demonstration whose practical stages remain legible.",
        "before-after": "STORY SHAPE: a clear and visually legible contrast between before and after states.",
        "montage-sequence": "STORY SHAPE: several short shots around one idea, each adding distinct evidence or feeling.",
    },
    "edit-language": {
        "continuous-shot": "EDIT LANGUAGE: continuous-shot timing; let the visual action play as one coherent moment.",
        "jump-cut-beats": "EDIT LANGUAGE: crisp jump-cut beats, with each meaningful action stage landing cleanly.",
        "match-cut-montage": "EDIT LANGUAGE: match-cut montage connecting similar framing, motion, or composition across shots.",
        "insert-cutaway": "EDIT LANGUAGE: relevant detail cutaways such as hand, screen, product, face, or reaction.",
    },
    "camera-language": {
        "reference-locked": "CAMERA LANGUAGE: preserve the reference framing with minimal drift and let subject action carry the shot.",
        "selfie-vlog": "CAMERA LANGUAGE: intimate selfie, mirror, or vlog phone camera with natural arm and camera bob.",
        "handheld-follow": "CAMERA LANGUAGE: a handheld follow camera moving with the subject or object through space.",
        "push-reveal": "CAMERA LANGUAGE: a motivated push, tilt, or reveal guiding attention to the key discovery.",
        "product-macro": "CAMERA LANGUAGE: close product macro, orbit, parallax, or detail-focused movement.",
    },
    "motion-intensity": {
        "micro": "MOTION INTENSITY: micro motion only, prioritizing proof, product, or still-evidence clarity.",
        "readable": "MOTION INTENSITY: readable action with clear movement that remains easy to understand.",
        "lively": "MOTION INTENSITY: lively UGC body, camera, or object movement.",
        "impact": "MOTION INTENSITY: high-impact physical or sensory motion such as splash, burst, or fast reveal.",
    },
}

PHONE_UGC_CAPTURE = ("A photograph captured as a single frame from a video actually shot on an iPhone, with the texture "
                     "of real iPhone footage. The image looks real, without an oily, overprocessed finish. The background "
                     "is clearly visible, with no depth-of-field blur. Skin texture is natural and fine, and the lighting "
                     "is natural. The image is coherent and free of visual artifacts.")


def _pick(choices: dict, defaults: dict, recipe: dict | None) -> list[str]:
    recipe = recipe or {}
    out = []
    for axis, options in choices.items():
        value = recipe.get(axis) or defaults[axis]
        out.append(options.get(value, options[defaults[axis]]))
    return out



def offscreen_voice(character: dict) -> str:
    """Only translate explicit age/gender facts, never infer them from the role."""
    import re
    facts = " ".join(str(character.get(k) or "") for k in ("role", "appearance", "voice"))
    # Ignore clauses explicitly stating uncertainty, including gender speculation.
    facts = " ".join(c for c in re.split(r"[。；;.!?]", facts)
                     if not re.search(r"不确定|无法|未知|未见|不明|unknown|uncertain|cannot", c, re.I))
    female = bool(re.search(r"女性|女人|女士|女孩|\bwoman\b|\bfemale\b|\bgirl\b", facts, re.I))
    male = bool(re.search(r"男性|男人|男士|男孩|\bman\b|\bmale\b|\bboy\b", facts, re.I))
    age = "adult " if re.search(r"成年|成人|\badult\b", facts, re.I) else ""
    if re.search(r"老年|年长|elderly|older adult", facts, re.I):
        age = "older adult "
    if re.search(r"儿童|小孩|\bchild\b", facts, re.I):
        return "a child's voice"
    if female != male:
        noun = "woman" if female else "man"
        return ("an " if age.startswith(("adult", "older")) else "a ") + age + noun + "'s voice"
    return "an " + age.strip() + " voice" if age else ""

def speaker_prompt(dialogue: str, action: str, recipe: dict | None, *, has_audio: bool, voice_note: str = "",
                   has_video: bool = False, dialogue_windows: list[dict] | None = None,
                   characters: list[dict] | None = None) -> list[str]:
    base = ("Create a realistic vertical phone-shot talking-head video. @image1 is the visible speaker and scene reference. "
            "Preserve the person's identity, face, outfit, setting, lighting, lens feel, and broad composition.")
    if has_video:
        base = ("Create a realistic vertical phone-shot talking-head video. "
                "@image1 defines the speaker's identity, face, outfit and the look of the setting; "
                "framing over time follows @video1, not @image1.")
    base += " @audio1 is the speaker's voice-timbre reference." if has_audio else (f" VOICE: {voice_note}" if voice_note else "")
    offscreen = any(row.get("onScreen") is False for row in dialogue_windows or [])
    blocks = [base,
              "Deliver the supplied script exactly in order with clear lip-sync. The visible person speaks every line. "
              "Do not rewrite, summarize, add, remove, or reorder words."]
    if offscreen or any(row.get("speaker") == "unknown" for row in dialogue_windows or []):
        blocks[1] = ("Deliver the supplied script exactly in order with clear lip-sync for ON-SCREEN lines only. "
                     "Lines marked OFF-SCREEN are spoken by an unseen person behind the camera; the visible person does not mouth them and only listens and reacts. The visible person speaks only the lines marked ON-SCREEN. "
                     + ("For UNKNOWN lines, speaker placement is unconfirmed; do not assign them to the visible person by default. "
                        if any(row.get("speaker") == "unknown" for row in dialogue_windows or []) else "") +
                     "Do not rewrite, summarize, add, remove, or reorder words.")
    if has_video:
        blocks += ["MOTION: body motion, gestures, turns, walking path and subject distance follow @video1 exactly, in the same order and timing.",
                   "FRAMING: the camera matches @video1. Framing changes come only from the subject moving as in @video1; never come closer to or move further from the lens than @video1 does at the same moment."]
    else:
        blocks += _pick(SPEAKER_CHOICES, SPEAKER_DEFAULTS, recipe)
    blocks.append("Keep face, hands, skin texture, lighting, and motion photoreal and stable. Add no subtitles, captions, "
                  "labels, logos, UI text, floating words, or other readable on-screen text.")
    if offscreen or any(row.get("speaker") == "unknown" for row in dialogue_windows or []):
        people = {c.get("id"): c for c in characters or []}
        lines = []
        for row in dialogue_windows:
            if row.get("onScreen") is False:
                cid = row.get("speaker") or "unknown"
                char = people.get(cid, {})
                label = f"OFF-SCREEN ({cid} {char.get('name') or '画外角色'}, voice from behind the camera)"
                voice = offscreen_voice(char)
                if voice and f"OFF-SCREEN VOICE ({cid}): {voice}." not in blocks:
                    blocks.append(f"OFF-SCREEN VOICE ({cid}): {voice}.")
            elif row.get("speaker") == "unknown" or row.get("onScreen") is None:
                label = "UNKNOWN (speaker not established; do not assume visible-person lip-sync)"
            else:
                label = "ON-SCREEN (the person from @image1)"
            lines.append(f"[{row['start']:g}–{row['end']:g}] {label}: {row['text']}")
        blocks.append("Speak each line inside its time window; do not start early. Lip-sync applies only to ON-SCREEN lines.")
        blocks.append("SCRIPT TO DELIVER:\n" + "\n".join(lines))
    elif has_video and dialogue_windows:
        blocks.append("Speak each line inside its time window, matching the pacing of @video1; do not start early.")
        blocks.append("SCRIPT TO DELIVER:\n" + "\n".join(
            f"[{r['start']:g}–{r['end']:g}] {r['text']}" for r in dialogue_windows))
    else:
        blocks.append("SCRIPT TO DELIVER:\n" + dialogue.strip())
    if action.strip():
        blocks.append("PERFORMANCE AND ACTION DIRECTION:\n" + action.strip())
    return blocks


def broll_prompt(story: str, recipe: dict | None) -> list[str]:
    blocks = ["Create a silent B-roll micro-film using the provided reference image or images.",
              "B-ROLL CONTRACT: create visual support footage. Focus on visible action, object behavior, camera movement, "
              "and editing rhythm. Visible people perform visual actions rather than speaking to camera.",
              "REFERENCE CONTRACT: preserve the intended identity, product, UI, props, setting, lighting, texture, wardrobe, "
              "and composition from the reference images. Use the references in their authored order and follow the "
              "story's explicit description of each reference's role."]
    blocks += _pick(BROLL_CHOICES, BROLL_DEFAULTS, recipe)
    blocks.append("SCREEN AND TEXT HYGIENE: add no subtitles, captions, floating words, labels, logos, or ad overlays. "
                  "Referenced physical labels, screens, websites, and documents remain attached to their objects.")
    blocks.append("MICRO-STORY PROMPT:\n" + story.strip())
    return blocks


def motion_reference_prompt(direction: str) -> list[str]:
    blocks = ["Use @image1 as the subject and preserve the subject's identity, outfit, and physical features.",
              "Replicate the body motion, gestures, and pose dynamics from @video1 exactly. The subject from @image1 "
              "performs that motion in their own setting.",
              "Do not copy @video1's camera framing or lens. Let the camera move naturally to capture the transferred action."]
    if direction.strip():
        blocks.append("ADDITIONAL DIRECTION:\n" + direction.strip())
    return blocks


def camera_reference_prompt(direction: str) -> list[str]:
    blocks = ["Use @image1 as the subject and preserve the subject's identity, outfit, and physical features.",
              "Replicate @video1's camera framing, lens choice, camera height, movement, and photographic rhythm, applying "
              "that camera language to the subject from @image1 in a coherent setting.",
              "Do not copy @video1's subject pose or body action. Let the subject move naturally for the transferred camera treatment."]
    if direction.strip():
        blocks.append("ADDITIONAL DIRECTION:\n" + direction.strip())
    return blocks


# Speaking passage that must also keep the source excerpt's timing (short drama / skit).
SPEAKER_WITH_FOOTAGE = ("@video1 is source footage carrying the performed timing: replicate its body motion, gestures, "
                        "blocking, camera framing and movement, and the rhythm of its actions and cuts. Do not inherit "
                        "@video1's people, faces, outfits, products, captions or any on-screen text; the visible people, "
                        "products and setting come from the image references.")


def phone_ugc_image_prompt(person: str, shot: str, setting: str) -> str:
    return "\n\n".join(p.strip() for p in (PHONE_UGC_CAPTURE, person, shot, setting) if p and p.strip())
