"""Prompt contracts for scene-plan projects only; legacy bytes live in jimmy_bridge."""
from __future__ import annotations

import re

from . import motion, replacement_guard as guard, scene_plan

DEFAULT_PROMPT_LIMIT = 5000
ENGLISH_WARNING = '动作时间轴未能转成英文'


def role_names(data: dict) -> dict[str, str]:
    words = [('父亲|爸爸|老爸|father|dad', 'FATHER'), ('儿子|son', 'SON'),
             ('母亲|妈妈|mother|mom', 'MOTHER'), ('女儿|daughter', 'DAUGHTER'),
             ('丈夫|husband', 'HUSBAND'), ('妻子|wife', 'WIFE'),
             ('女人|女性|女子|woman', 'WOMAN'), ('男人|男性|男子|man', 'MAN'),
             ('女孩|girl', 'GIRL'), ('男孩|boy', 'BOY'),
             ('幼童|儿童|小孩|宝宝|婴儿|child|toddler|baby', 'CHILD')]
    roles = {}
    unnamed = 0
    for c in (data.get('breakdown') or {}).get('characters', []):
        cid = c.get('id')
        if not cid:
            continue
        word = next((v for p, v in words if re.search('|'.join(r'\b'+word+r'\b' if word.isascii() else word for word in p.split('|')), str(c.get('name') or ''), re.I)), None)
        if word:
            roles[cid] = 'THE ' + word
        else:
            unnamed += 1
            number, label = unnamed, ''
            while number:
                number, digit = divmod(number - 1, 26)
                label = chr(ord('A') + digit) + label
            roles[cid] = 'PERSON ' + label
    for cid, name in list(roles.items()):
        if sum(n == name for n in roles.values()) > 1:
            for key in [key for key, n in roles.items() if n == name]:
                roles[key] += ' ' + key
    return roles


def signature_role_label(cid: str, role: str) -> str:
    """Keep V3 input hashes stable; this compatibility label is never a prompt."""
    if role.startswith('PERSON ') or role == 'THE CHILD' or role.startswith('THE CHILD '):
        return 'PERSON ' + cid
    return role


def valid_english(value, rows: list[dict], roles: dict) -> list[dict] | None:
    if not rows or not isinstance(value, list) or len(value) != len(rows):
        return None
    out = []
    for en, source in zip(value, rows):
        if not isinstance(en, dict) or any(en.get(k) != source[k] for k in ('start', 'end')):
            return None
        text = en.get('action')
        if (not isinstance(text, str) or not text.strip() or re.search(r'[\u3400-\u9fff]|@image|the person from', text, re.I)
                or not re.search(r'[A-Za-z]', text)):
            return None
        # Reject unresolved IDs: people must be named by their role in English.
        if re.search(r'\bC\d+\b', text) and not any(name.startswith('PERSON ') and name in text for name in roles.values()):
            return None
        out.append({**source, 'action': text.strip()})
    return out


def cached_comparison(data: dict, task: dict, directory) -> dict:
    from .jimmy_bridge import preflight_fingerprint
    cached = (data.get('action_comparisons') or {}).get(task.get('id')) or {}
    if cached.get('fingerprint') != preflight_fingerprint(data, task, directory):
        return {}
    return cached.get('comparison') or {}


def english_timeline(data: dict, task: dict, directory) -> list[dict] | None:
    return valid_english(cached_comparison(data, task, directory).get('sourceMotionEn'),
                         motion.source_timeline(data, task), role_names(data))


def source_product(data: dict, task: dict, comparison: dict) -> str:
    supplied = comparison.get('sourceProductEn')
    if isinstance(supplied, str) and supplied.strip() and not re.search(r'[\u3400-\u9fff]|@image', supplied):
        return supplied.strip()
    sid = guard.task_scene(data, task)
    text = ' '.join(str(r.get(k) or '') for r in (data.get('breakdown') or {}).get('productMoments', [])
                    if scene_plan._overlaps(r, scene_plan._ranges(data, sid)) for k in ('product', 'description'))
    text += ' ' + ' '.join(str(r.get(k) or '') for r in (data.get('breakdown') or {}).get('shots', [])
                         if scene_plan._overlaps(r, scene_plan._ranges(data, sid)) for k in ('product', 'action', 'description'))
    name = 'bracelet' if re.search(r'手环|手镯|bracelet', text, re.I) else 'watch' if re.search(r'手表|watch', text, re.I) else 'product'
    colors = [('白色|white', 'white'), ('黑色|black', 'black'), ('蓝色|blue', 'blue'), ('红色|red', 'red')]
    # Only product-bearing clauses are used for color/brand; outfit colors must not leak in.
    clauses = re.split(r'[，,。;；]', text)
    product_text = ' '.join(c for c in clauses if re.search(r'手环|手表|bracelet|watch|product', c, re.I))
    color = next((word + ' ' for pattern, word in colors if re.search(pattern, product_text, re.I)), '')
    brand = ' '.join(dict.fromkeys(re.findall(r'\b[A-Z][A-Z0-9]{1,}\b', product_text)))
    location = ''
    if re.search(r'手腕|wrist', text, re.I):
        owner = next((role for cid, role in role_names(data).items()
                      if any(c.get('id') == cid and c.get('name') and str(c['name']) in text
                             for c in data['breakdown'].get('characters', []))), None)
        location = f" on {owner}'s wrist" if owner else ' on the wrist'
    return color + (brand + ' ' if brand else '') + name + location


def compose(data: dict, task: dict, directory=None) -> str:
    assets = {a['id']: a for a in data.get('assets', [])}
    ids = task.get('image_ids') or []
    rows = scene_plan.effective_subjects(data, guard.task_scene(data, task))
    roles = role_names(data)
    visible = scene_plan.present_keys(data, guard.task_scene(data, task)) - {'product'}
    people = [s for s in rows if s['kind'] == 'character' and s['on']]
    video = task.get('mode') == 'video'
    source = '@video1' if video else 'the source scene in @image1'
    names = ' and '.join(roles.get(cid, 'PERSON ' + cid) for cid in roles if cid in visible)
    blocks = [f"Create a realistic vertical phone-shot video of this scene. @image1 shows the replaced scene ({names or 'the product'} in the source setting);"
              + (' timing and framing over time follow @video1.' if video else ' preserve its framing and lighting.')]
    refs = []
    for person in people:
        aid, cid = person.get('asset_id'), person['source_id']
        if aid not in ids:
            continue
        ref = f'@image{ids.index(aid)+1}'
        if len(people) == 1:
            refs.append(f'{ref} is the identity reference of the same person: use it only for face, hair and body proportions; ignore its clothing, background and multi-panel layout. Never show the reference collage or its panels in the video.')
        else:
            others = ', '.join(roles[p['source_id']] for p in people if p is not person)
            refs.append(f'{ref} is the identity of {roles[cid]} ({cid}) — face, hair and body proportions only; ignore its clothing, background and multi-panel layout. A different person from {others}; never merge, swap or blend their faces.')
    if refs:
        blocks.append('IDENTITY REFERENCES: ' + ' '.join(refs))
    consumed = {p.get('asset_id') for p in people} | {r.get('asset_id') for r in rows if r['kind']=='product' and r['on']}
    extra = []
    for i, aid in enumerate(ids, 1):
        if i == 1 or aid in consumed or aid not in assets:
            continue
        scene_ref = next((r for r in task.get('scene_references') or [] if r.get('asset_id') == aid), None)
        if scene_ref:
            extra.append(f"@image{i} is the scene {scene_ref['scene_id']} reference, used from {scene_ref['start']:g} s; follow {source} for the transition and preserve each person's assigned identity.")
        else:
            from .jimmy_bridge import _role_text
            role = _role_text(assets[aid]).replace('packaging text and ', '').replace('body, outfit and accessories', 'body proportions')
            extra.append(f'@image{i}: {role}.')
    if extra:
        blocks.append('OTHER REFERENCES: ' + ' '.join(extra))
    blocks.append(f'Clothing follows {source} for each person unless stated otherwise. Identity photos define face, hair and body proportions only. Explicit clothing instructions take priority.')
    for row in rows:
        if row['kind'] == 'character' and row['on'] and row.get('description'):
            blocks.append(f"Instructions for {roles[row['source_id']]}: {row['description']}")
    if video:
        blocks.append('MOTION AND FRAMING: follow @video1 exactly: body motion, gestures, blocking, camera movement and cuts at the same times. Perform each movement once; add, repeat, reverse or skip none.')
    blocks.append('SCENE REPLACEMENTS: Change only selected subjects; keep unchecked people and products as in ' + source + '. '
        "Replacements apply from the very first frame to the last frame: at no moment may the selected original actors' faces or hair, or the original product selected for replacement, appear. Any explicitly requested clothing changes also apply throughout; otherwise retain source-scene clothing.")
    for row in rows:
        if row['kind'] != 'character':
            continue
        name = f"{roles[row['source_id']]} ({row['source_id']})"
        ref = f"@image{ids.index(row['asset_id'])+1}" if row.get('asset_id') in ids else '@image1'
        blocks.append(f'Replace {name} using {ref}; preserve position and role in the source scene.' if row['on']
                      else f'Keep {name} as in {source}: preserve original appearance and identity.')
    product = next((r for r in rows if r['kind'] == 'product' and r['on']), None)
    comparison = cached_comparison(data, task, directory) if video else {}
    if product:
        ref = f"@image{ids.index(product['asset_id'])+1}" if product.get('asset_id') in ids else '@image1'
        target = assets.get(product.get('asset_id'), {})
        desc = product.get('description') or target.get('description') or target.get('label') or ''
        use = ('use, wear or place the product correctly; when displaying it, keep its front or functional side facing the camera; '
               'preserve the source action and real proportions')
        blocks.append(f'Replace the original product ({source_product(data, task, comparison)}) with the product in {ref} from the first frame: one single product; follow its real way of use ({use}). '
                      f'{ref} may be a multi-view product sheet: use it only for shape, material and details; never render its layout or label text. Product instructions: {desc}')
    if task.get('kind') == 'speaker':
        windows = motion.dialogue_windows(data, task, directory)[0]
        speakers = {r.get('speaker') for r in windows if r.get('onScreen') is not False and r.get('speaker') in roles}
        if len(speakers) == 1 and all(r.get('speaker') in speakers and r.get('onScreen') is not False for r in windows):
            speaker = next(iter(speakers))
            blocks.append(roles[speaker] + ' speaks every line; ' + '; '.join(roles[c] + ' does not speak' for c in roles if c in visible - speakers) + '.')
        blocks.append('Deliver every word exactly in order with clear lip-sync for ON-SCREEN lines only. OFF-SCREEN lines are spoken by an unseen person; UNKNOWN lines have unconfirmed speaker placement. Speak inside each time window, matching source pacing; do not start early.')
        blocks.append('SCRIPT TO DELIVER:\n' + ('\n'.join(f"[{r['start']:g}–{r['end']:g}] {roles.get(r.get('speaker'), 'UNKNOWN')} ({'OFF-SCREEN' if r.get('onScreen') is False else 'ON-SCREEN' if r.get('onScreen') is True else 'UNKNOWN'}): {r['text']}" for r in windows) or task.get('dialogue', '')))
        audio = assets.get(task.get('audio_id'), {})
        if audio:
            blocks.append("@audio1 is the speaker's voice-timbre reference. " + str(audio.get('description') or ''))
    else:
        blocks.append('Create silent B-roll; visible people do not speak to camera.')
    blocks.append('Keep faces, hands, skin, lighting and motion photoreal and stable. Add no subtitles, captions, labels, logos, floating words or on-screen text.')
    performance = '\n'.join(task.get(k) or '' for k in guard.TEXT_FIELDS).strip()
    if performance:
        blocks.append(motion.PERFORMANCE_LABEL + '\n' + performance)
    if task.get('start_state') or task.get('end_state'):
        blocks.append(f"CONTINUITY: begins with {task.get('start_state') or 'the reference state'}; ends with {task.get('end_state') or 'a natural settle'}.")
    if video:
        timeline = english_timeline(data, task, directory) or motion.source_timeline(data, task)
        # Spend the remaining channel budget on action prose, never on removing
        # identity/product contracts or spoken words. Keep every interval.
        fixed = '\n\n'.join(blocks)
        budget = DEFAULT_PROMPT_LIMIT - len(fixed) - 2
        for size in (None, 320, 200, 120, 60, 0):
            compact = [{**r, 'action': r['action'] if size is None else r['action'][:size] if size else 'Follow @video1.'} for r in timeline]
            block = motion.timeline_block(compact)
            if len(block) <= budget:
                break
        blocks.append(block)
    return '\n\n'.join(b for b in blocks if b.strip())
