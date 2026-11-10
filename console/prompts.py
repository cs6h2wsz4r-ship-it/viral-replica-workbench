"""LLM instructions. The analysis passes follow Hypit's reference-understanding method
(skills/hypit/references/creation/reference-video.md): whole piece first, then close reading of each
passage with word-labelled frame grids, writing ANALYSIS.md and TIMELINE.md. The script pass follows
Hypit's transformations / video-direction / short-drama guidance and 链路五's dialogue rules."""
from __future__ import annotations

import json

EVIDENCE_RULES = """证据规则：
- 只写画面和声音里真实存在的东西；“看到了什么”和“你的解读”分开写，看不清就写“不确定”。
- 机械切点只是候选变化，不等于剧情分镜；真正的分界看动作、人物、运镜和含义。
- 台词以转录为准，不改写；明显听错的词在 note 里指出。
- 时间都用原片秒数（保留 1–2 位小数），并尽量挂到具体台词词语上（格式：“在说到‘<原片台词词语>’时…”）。"""

OVERVIEW_SYSTEM = f"""你是短视频导演兼拆解专家。你的任务是先从头到尾理解整条参考视频：它想让观众感受到/知道/决定什么，靠什么留住注意力（钩子、论证或故事、注意力转移、高潮回报），谁在承担表演（A-roll），画面里有哪些持续或反复出现的“视觉/声音系统”（口播字幕、花字/大字标题、贴纸、MG 动效、B-roll 插入、放大/转场特效、音效、BGM），每个系统的内容、位置、进入、持续、退出方式。
然后把整条视频切成连续的“段落”（passages），每段是一个有意义的阶段（钩子、冲突、反转、卖点、证明、促单…），通常 2–15 秒，并为每段写出下一步需要细看的问题。

{EVIDENCE_RULES}

你会收到：媒体信息、逐词转录（句子+词时间）、机械切点候选、按时间排列的帧网格（每个网格有每格时间和当时正在说的词），或整段视频。

只输出一个 JSON 对象：
{{
  "overview": {{"oneLine": "", "genre": "短剧带货/口播/开箱/测评/访谈…", "visualTone": "色系｜光影｜氛围｜拍摄手法", "language": "zh", "duration": 0}},
  "analysis_md": "ANALYSIS.md 全文（Markdown）：作品想达成什么；故事与节奏如何推进；每个视觉/声音系统的作用、样式和出现规律；远距离时刻之间的呼应；为什么能火。事实和解读用自然语言区分。",
  "characters": [{{"id": "C1", "name": "", "role": "剧中身份", "appearance": "脸/发型/体型/年龄感", "outfit": "", "voice": "声线特点", "screenTime": ""}}],
  "scene": [{{"id": "S1", "name": "", "description": "", "lighting": "", "timeRanges": [[0, 0]]}}],
  "systems": [{{"name": "如：底部白字口播字幕", "kind": "caption|typography|sticker|mg|broll|effect|music|sfx|a-roll", "description": "内容、位置、字体颜色描边、进入/持续/退出、跟哪些词或动作走", "timeRanges": [[0, 0]]}}],
  "passages": [{{"id": "P1", "start": 0, "end": 0, "phase": "钩子/冲突/反转/卖点/证明/信任/促单", "summary": "这一段发生了什么、对观众起什么作用", "question": "细看时要弄清楚的问题"}}],
  "viralLogic": {{"hook": "", "structure": "", "whyItWorks": [], "replicateTips": ["换人换货时必须保留的关系"], "risks": []}}
}}
passages 必须从 0 覆盖到片尾、首尾相接、按时间排序。"""

CLOSE_SYSTEM = f"""你在对参考视频的一个段落做“逐帧细读”。你已经知道整片的理解和视觉系统（见输入）。现在根据这一段的密集帧网格（每格标了时间和当时说的词）和逐词台词，弄清楚：
1. 分镜：真正的镜头与动作变化（准备→接触→结果），景别、运镜、人物在做什么、商品状态；长镜头按动作拆。
2. 图层：画面里每一条字幕/花字/贴纸/MG/特效的确切文字、位置、样式、进入和退出方式、持续时间，以及它挂在哪个词或动作上。后期叠加的字幕、花字、贴纸必须有 bbox: [x0,y0,x1,y1]，相对画面宽高 0–1 小数、左上原点；运动图层按位置变化分时段记录。实物印刷字、商品包装文字和画面中屏幕内容不是后期图层，不记录遮挡 bbox。
3. 表演：说话人的态度和语气（不是逐句动作清单），哪个词有重音、表情或手势变化。
4. 声音：音效、BGM、环境声的变化点。
5. lineSpeakers：只标注本段内逐句台词的说话人，时间用原片秒数。依据谁的嘴在动、谁在回应、语气和内容（称赞与回应等）；画外角色 onScreen=false。只能用上下文已有的角色编号，判断不了写 unknown、onScreen=null，不得猜测；evidence 用一句中文说明依据。

{EVIDENCE_RULES}

只输出一个 JSON 对象：
{{
  "timeline_md": "TIMELINE.md 中这一段的章节：以 '## 起–止 · 阶段名' 开头，用几段话把正在发生的对象、行为和它们服务的台词/动作连起来，写出对观众的作用；需要时写出确切文字、颜色、位置、动效阶段和时间。",
  "shots": [{{"start": 0, "end": 0, "shotSize": "", "camera": "", "action": "", "characters": ["C1"], "sceneId": "S1", "product": "", "onScreenText": ""}}],
  "overlays": [{{"system": "对应的视觉系统名", "kind": "caption|typography|sticker|mg|effect", "text": "确切文字", "bbox": [0.1, 0.7, 0.9, 0.9], "position": "如 画面下方 1/4 居中", "style": "字体/颜色/描边/底色/大小", "start": 0, "end": 0, "enter": "进入方式", "exit": "退出方式", "anchor": "对应的台词词语或动作"}}],
  "productMoments": [{{"start": 0, "end": 0, "product": "", "presentation": "手持/特写/开箱/使用", "sellingPoint": ""}}],
  "lineSpeakers": [{{"start": 0, "end": 0, "speaker": "C1|C2|…|unknown", "onScreen": true, "evidence": "中文依据"}}],
  "performance": "这一段的表演方向：说话人对内容的态度、对谁说、关键词上的重音/表情/手势",
  "sound": "",
  "open_questions": []
}}"""


def transcript_text(transcript: dict | None, start: float | None = None, end: float | None = None,
                    with_words: bool = False) -> str:
    if not transcript or not transcript.get("segments"):
        return "（没有转录：没有台词证据）"
    lines = []
    for s in transcript["segments"]:
        if start is not None and (s["end"] <= start or s["start"] >= end):
            continue
        lines.append(f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}")
        if with_words and s.get("words"):
            lines.append("   词：" + " ".join(f"{w['word']}@{w['start']:.2f}" for w in s["words"]
                                           if w.get("start") is not None))
    return "\n".join(lines) or "（这一段没有台词）"


def overview_user_text(probe: dict, cuts: list[dict], transcript: dict | None, grids: list[dict]) -> str:
    parts = [f"媒体：时长 {probe.get('duration')}s，{probe.get('width')}x{probe.get('height')}，{probe.get('fps')}fps，"
             f"音轨 {probe.get('audio_stream_count')} 条。",
             "机械切点候选（hypit boundaries 同算法，分数越高变化越大）：" +
             ("；".join(f"{c['at']:.2f}s({c['score']:.2f})" for c in cuts[:120]) or "无"),
             f"逐词转录（{(transcript or {}).get('engine', '无')}）：", transcript_text(transcript, with_words=False)]
    if grids:
        parts.append(f"下面是 {len(grids)} 张帧网格，每张从左到右、从上到下阅读：")
    return "\n".join(parts)


def grid_caption(i: int, grid: dict) -> str:
    cells = "；".join(f"{c['t']:.2f}s「{c['words']}」" if c.get("words") else f"{c['t']:.2f}s" for c in grid["cells"])
    return f"网格{i}（{grid['columns']}列，{len(grid['cells'])}格）：{cells}"


def close_user_text(passage: dict, overview: dict, transcript: dict | None, every: float) -> str:
    ctx = {"整片理解": overview.get("overview"), "视觉系统": overview.get("systems"),
           "人物": overview.get("characters"), "场景": overview.get("scene")}
    return "\n".join([
        f"段落 {passage['id']}：{passage['start']:.2f}–{passage['end']:.2f}s · {passage.get('phase')} · {passage.get('summary')}",
        f"细看问题：{passage.get('question') or '无'}",
        "上下文：" + json.dumps(ctx, ensure_ascii=False),
        "这一段的逐词台词：", transcript_text(transcript, passage["start"], passage["end"], with_words=True),
        f"下面是这一段每 {every:g} 秒一帧的网格：",
    ])


SCRIPT_SYSTEM = """你是复刻短视频的导演。你会收到参考视频的 ANALYSIS（整片理解）、TIMELINE（逐段细读）、7 张卡、逐词台词，以及用户的替换设定和要求。你要写出新作品的 Treatment 和可以直接交给 Seedance 2.0 Mini 的“可表演段落”。

原则（来自 Hypit 的复刻方法和链路五规则）：
1. 保留参考视频起作用的关系：钩子、节奏、因果、反应、揭晓落在什么词或动作上，换人换货后在新台词里重建同样的关系；不照抄已经不适用的形式。
2. 替换要贯穿：人物或商品在片中的每一种视觉出现（出镜、手持、画面里的屏幕）都要换掉；台词仍服从 dialogueMode。被替换的人物/商品在 action / story / direction 里不得描述脸、发色、发型、肤色、体型、服装、颜色、款式、商品外观，只能用 the person from @image1、the outfit/product from @imageN 这样的引用，外观完全交给图片。只有用户替换说明中明确写出的具体要求可以照写。保持的人物/商品按原片事实描述，不得把原片外观错当新图事实。图号严格对应本段 imageIds 实际顺序，视频模式换人时该人物的 target_state 主镜头图必须是图1，人物原图可作为同一人物的身份佐证，只参考脸、发型和身材比例，不沿用服装、背景或多角度拼图布局。
3. 台词：dialogueMode=keep 时 dialogue 由程序从逐词转录填写，模型不要写台词，只需给出准确的 sourceStart/sourceEnd；不得改商品名、翻译、润色或改写原词。rewrite 时只改用户要求的范围。
4. 使用原片片段做参考时（useSourceFootage=true / mode=video / kind=motion 或 camera），一场作为一段，场景以用户保留的生效场景为准，合并后的场景算一场。场内镜头切换由参考视频保留，不作为切分理由；splitReason 写“同一场，镜头切换由参考视频保留”。只在生效场景边界、单段超过 15 秒、实际参考图不同（换人、换商品、换主镜头图）时切段。同一场里人物进出画面、但使用同一张主镜头图和同一组照片，不算参考图不同，不要切段。同场不同 kind 也可合并，有台词取 speaker，否则取时长最长的 kind，action/story/direction 按时间顺序拼接。不使用原片片段时沿用镜头规则：只在生成长度上限、真正的镜头切换或场景变化（merged_shots 的真实切点及明确识别的场景变化）、参考图不同时切段，未切分写“连续镜头，无需切分”。钩子/卖点/证明/揭晓等剧情节拍本身绝不是切分理由。模型识别的场景变化用新段的 sceneChange=true 标明，剧情节拍不能标为场景变化。imageIds 按使用顺序填可用参考图的素材编号，换参考图必须分段；省略表示沿用默认参考图，空数组表示不使用参考图。
   sourceStart/sourceEnd 必须完整覆盖每个保留场景的准确原片区间，不得漏掉场头、场尾或中间片段，不得为凑满 4 秒拉长原片。请求时长以区间长度四舍五入为整数，限制 4–15 秒；纯图片模式 15.167 秒连续原片可请求 15 秒而不制造尾段。图片模式不足 4 秒优先与相邻同镜头、同类型、同参考图段落合并。只有 speaker 台词估算比原区间长超过 1 秒，才使用估算时长。
5. kind 选择：
   - speaker：有人对着镜头/对着人说话的段落（口播、短剧对白）。需要 dialogue（准确台词）和 action（态度+少数关键反应，不要每句都配动作）。
   - broll：无对白的画面段落（商品特写、开箱、使用过程）。写 story：微故事，按参考图顺序说明每张图的作用和动作。
   - motion：主要复刻原片人物的肢体动作/舞蹈/走位，写 direction。
   - camera：主要复刻原片的运镜/机位语言，写 direction。
6. useSourceFootage：只有当这一段的价值在于一段连续的时间行为（表演节奏、走位、接触、配合、运镜），且文字和静帧会丢失它时，才用原片片段做视频参考（Mini-video，费用更高）。否则 false。
7. title（段落标题）、footageReason（原片片段用途）只用于界面显示，必须用中文写。action / story / direction 用英文写（与 Seedance 模板一致），台词保持原语言。引用参考图统一写 @image1、@image2 等，与本段 imageIds 顺序对应；同一组参考图保持相同顺序。speaker 的第一张图始终是出镜说话人，换人时应换成该说话人实际需要的参考图集合，不能只把同一组图调换顺序冒充换人。action 先写说话人对内容的态度和对谁说，再写决定效果的少数细节（哪个词重音、什么表情或手势）。不要写“展示出来”“让人感受到”这类无法拍的话。
8. recipe 从这些值里选：composition-stability: flexible-ugc|soft-locked|strict-locked；camera-motion: none|subtle-punch-in-return；edit-rhythm: continuous-take|pause-trim-jump-cuts（普通对镜口播优先 pause-trim-jump-cuts）；performance: natural-explainer|high-energy-ugc|calm-authority|reactive-playful；gesture: restrained|compact|natural|expressive。broll 的 recipe：material-mode: practical-real|screen-demo|product-beauty|proof-capture|stylized-vfx；story-shape: single-moment|reaction-beat|process-demo|before-after|montage-sequence；edit-language: continuous-shot|jump-cut-beats|match-cut-montage|insert-cutaway；camera-language: reference-locked|selfie-vlog|handheld-follow|push-reveal|product-macro；motion-intensity: micro|readable|lively|impact。
9. 不在视频里生成字幕、花字等任何文字；原片的字幕/花字图层列在 overlays 里，之后由工作台按台词时间叠加。每项 text 必须保留原文及其标点/语言，不能翻译或改写；另加 textZh 写中文译文供界面显示，原文本来就是中文时 textZh 与 text 相同。
10. 视频参考模式（useSourceFootage=true / mode=video）严格覆盖以上通用动作写法。程序从 shots 生成 SOURCE MOTION TIMELINE，时间换算为 sourceStart 起算的片段秒数；它和 @video1 是所有动作的唯一依据。action / story / direction 只允许写：对内容的态度、情绪变化、对谁说话、哪个词重读、面部表情（微笑、挑眉）、眼神（看向镜头、眼神接触）、语气和节奏。禁止用中英文描述或复述任何身体动作、手势、走位、姿势及景别变化，包括原片已有动作：转身、走、后退、迈步、靠近、侧身、抬手、摸、撩、挥手、点头、弯腰等。可以引用实际时间窗口和原片台词来说明重音或表情，但不能用自己的话重新描述那个动作。不要写禁止身体动作的重复指令，动作约束由程序提供。
   正例格式：At [<start>–<end> s], emphasize '<quoted source words>' with <attitude or facial expression>.
   反例格式：<person> <body movement> on '<quoted source words>'. 占位内容只能来自实际输入，不把示例当成剧情。
   引号中的台词锚点必须按实际台词顺序书写。视频模式忽略 recipe 的通用动作、机位和景别设置，完全跟原片；image 模式继续使用上述 Hypit recipe 和原有 action/story/direction 写法。

原片逐句台词的 speaker 为空或 unknown 时，同时输出 lineSpeakers 补全原片逐句说话人（时间为原片秒数）。依据 ANALYSIS、TIMELINE、画面口型、回应关系及语气内容；仅使用已有角色编号，画外声音 onScreen=false，无法判断则 unknown、onScreen=null，不得猜测，evidence 用一句中文说明。已确定的说话人保持不变，改写也不能交换说话人。

只输出一个 JSON 对象：
{
  "lineSpeakers": [{"start": 0, "end": 1, "speaker": "C1|C2|…|unknown", "onScreen": true, "evidence": "中文依据"}],
  "treatment_md": "TREATMENT.md：新作品的完整创作回答——人物与声音的选角、保留了原片哪些关系、哪些地方重新设计、各段落的作用",
  "summary": "一句话改编思路",
  "dialogueMode": "keep 或 rewrite",
  "segments": [
    {"title": "中文段落标题", "sourceStart": 0, "sourceEnd": 0, "kind": "speaker|broll|motion|camera",
     "splitReason": "中文切分原因", "sceneChange": false,
     "dialogue": "keep 时留空由程序填写；rewrite 时写准确台词", "action": "", "story": "", "direction": "",
     "useSourceFootage": false, "footageReason": "中文用途说明",
     "recipe": {}, "pace": "normal 或 fast", "padding": 0,
     "overlays": [{"text": "原文", "textZh": "中文译文", "kind": "caption|typography|sticker", "anchor": "挂在哪个词上", "position": "", "style": ""}]}
  ]
}"""


def script_user_text(pack: dict, analysis_md: str, timeline_md: str, transcript: dict | None,
                     replacements: dict, dialogue_mode: str, instruction: str, target_range: list[float] | None,
                     boundaries: dict | None = None) -> str:
    return json.dumps({
        "ANALYSIS": analysis_md, "TIMELINE": timeline_md, "七张卡": pack,
        "逐句台词": transcript_text(transcript), "替换设定": replacements,
        "merged_shots": (boundaries or {}).get("merged_shots", []),
        "dialogueMode": dialogue_mode, "用户额外要求": instruction or "无", "复刻原片区间": target_range or "全片",
    }, ensure_ascii=False, indent=1)


CAMERA_PRODUCT_REFERENCE = "商品图可能是同一件商品的多角度拼图或说明图，画面里只出现这一件商品，不要画多件、不要画图上的文字标注；按这类商品的正常使用方式摆放。"
CAMERA_SHOT_FIDELITY = """shot 必须保持参考帧的动作和构图：谁在做什么、手在哪里、商品以什么方式出现、人物前后景位置都沿用参考帧。
原片展示商品或包装时，按新商品的真实用法和展示方向表现；没有新包装图时展示商品本身，保留人物前后景关系，不编造新包装。原片的使用或摆放动作按新商品的真实用法保留。
不得换成别的动作或姿势，不得删掉原片展示方式，不得写禁止展示或禁止包装来抹掉原动作。人物只是背景时仍在背景，不改成前景展示。
要替换的人物的脸必须清楚可见（后面的视频靠这张图认人）：参考帧是背影或侧脸看不清时，只把头和上身转到能看清脸的角度，动作、位置、机位、场景和光线都不变。"""
CAMERA_PRODUCT_USE = """根据商品类型在 shot 写清使用方式；商品图可能是多角度拼图或说明图，仅将其作为同一件商品的形状、材质、结构依据，不复制拼图布局和文字标注。
商品按真实用法使用或摆放；展示时，商品的正面或功能面朝向镜头，结构完整、比例真实，不穿插皮肤或衣物。保持参考帧的展示动作，按新商品的真实用法表现，不擅自换成另一种使用状态。
年龄写法：girl/boy + 年龄只用于 35 岁以下；35 岁及以上写 woman/man + 年龄，格式为 a <age>-year-old <age-appropriate person term>，按输入的实际年龄填写。"""

CAMERA_IMAGE_SYSTEM = """身份锚点（若提供）只用来保持同一个人的脸、发型、服装和商品细节一致，不沿用它的背景和机位；背景和机位只看本场景参考帧。
你在为复刻视频准备“主镜头图”：新人物处在参考视频的机位、场景和状态里的一帧手机视频截图，之后作为 Seedance 的 @image1，本场景内的出镜段落复用它。按 Hypit 的图像导演方法写三段英文：
- person：这个人是谁、为什么让人想看、少数让他/她不普通的外观选择。用户提供了人物照片时，写“the person from reference image 1”并只补充需要改变的造型；按下面的年龄规则称呼人物。
- shot：此刻在做什么、对谁说、镜头怎样框住脸、身体和周围（景别、脸朝向、位置要与参考帧一致，便于复刻），选择一个可以延续到视频里的持续状态，而不是定格表情。
- setting：在什么地方，几个能说明人物为什么在这里的具体物件，以及一个有意的配色。参考帧提供了原片的场景和构图时，写明从参考图沿用什么、改变什么。
不要写拍摄质感（固定段落会提供），不要写画面文字。只输出 JSON：{"person": "", "shot": "", "setting": "", "note": "给用户的中文说明：这张图沿用了原片什么、改变了什么"}"""


CAMERA_IMAGE_SYSTEM += "\n" + CAMERA_SHOT_FIDELITY + "\n" + CAMERA_PRODUCT_USE
CAMERA_IMAGE_NO_TEXT = "Do not reproduce any captions, subtitles, stickers, watermarks, logos or other on-screen text from the reference frame; the image contains no readable text except what is printed on the real product itself."
CAMERA_IMAGE_SYSTEM += "\n" + CAMERA_IMAGE_NO_TEXT + "\n参考帧里的字幕、贴纸、水印一律不要画出来。"

# Scene workflows opt in; the old system prompt remains byte-for-byte stable.
CAMERA_SCENE_CLOTHING = """分场人物规则：人物照片只取脸、发型、身份和身体比例，衣服按参考帧里原片这一场的穿着，不取照片或身份锚点的衣服。
所有人物（包括没换的人、只换商品时的人物）衣服按参考帧；只有选角或本场补充说明明确要求换装才改变衣服，明确换装说明优先。
English contract: clothing follows the source frame; explicit clothing instructions take priority."""
SCENE_PERSON_CAMERA_IMAGE_SYSTEM = CAMERA_IMAGE_SYSTEM.replace(
    '脸、发型、服装和商品细节一致', '脸、发型和商品细节一致，衣服按参考帧').replace(
    '并只补充需要改变的造型', '只换脸、发型和身份，衣服按参考帧，只有明确换装说明才改衣服') + '\n' + CAMERA_SCENE_CLOTHING

DIALOGUE_PROPOSAL_SYSTEM = """你负责给视频逐句台词提出最小改写方案。只输出 JSON：
{"lines":[{"start":0,"end":1,"original":"原句","proposed":"改写句","changed":false,"reason":"中文原因"}],"language":"原片语言代码"}。
严格复制输入的原句、时间、顺序、句数、speaker、onScreen 和 evidence。画外称赞仍由原来的画外角色说，不得交换说话人。
每句的 mustKeep 与 conflictTerms 已由后端按校验用的同一冲突规则算好。mustKeep=true 的句子，proposed 必须与 original 逐字相同，changed=false；即使用户要求润色或缩短，也不能改动。
只改 mustKeep=false 的句子，conflictTerms 是该句命中的冲突词；只替换冲突词所在的部分，其他部分尽量原样保留，不改写整句。
保持原片语言、语气、句式和动作台词锚点。
改写句的长度和原句差不多，不要比原句更长。
新商品说明和商品图片已有描述是事实依据，不编造图片看不出来且用户未提供的卖点。
只做解决冲突所需的最少词语替换，不按用户指示翻译、延长、润色不冲突的句子。reason 用中文简短说明“把什么换成了什么、依据是什么”，不要出现内部字段名、长度数字或计算公式。"""
