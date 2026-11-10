/* 爆款复刻工作台 — vanilla JS front end */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 2) => (n === null || n === undefined || n === "" || isNaN(n) ? "—" : Number(n).toFixed(d));
const clone = (o) => JSON.parse(JSON.stringify(o));

// Presentation only: never use these helpers when saving text or building requests.
function joinDisplayText(parts, separator='；') {
  return parts.map(part=>String(part ?? '').replace(/[\s。；！？.!?;，,]+$/u,'')).filter(Boolean).join(separator);
}
function displayPunctuation(value) {
  return String(value ?? '').replace(/[。；！？.!?;，,]+\s*([；，])/gu,'$1');
}
function issuesByTime(issues=[]) {
  return (issues || []).map((issue,index)=>({issue,index})).sort((a,b)=>Number(a.issue.start)-Number(b.issue.start) || Number(a.issue.end)-Number(b.issue.end));
}
function displayRoleNames() {
  // Match scene_prompt.role_names, including director/segment_plan's added people.
  const words=[['父亲|爸爸|老爸|father|dad','FATHER'],['儿子|son','SON'],
    ['母亲|妈妈|mother|mom','MOTHER'],['女儿|daughter','DAUGHTER'],
    ['丈夫|husband','HUSBAND'],['妻子|wife','WIFE'],
    ['女人|女性|女子|woman','WOMAN'],['男人|男性|男子|man','MAN'],
    ['女孩|girl','GIRL'],['男孩|boy','BOY'],['幼童|儿童|小孩|宝宝|婴儿|child|toddler|baby','CHILD']];
  const people=[...(P.breakdown?.characters || []),...(P.replacements?.characters || []).filter(c=>c.action==='add').map(c=>({id:c.source_id,name:c.name}))];
  const roles=new Map();let unnamed=0;
  for(const person of people){
    if(!person.id)continue;
    const name=String(person.name || '');
    const word=words.find(([names])=>names.split('|').some(word=>/^[a-z]+$/.test(word)
      ?new RegExp('(?<![\\p{L}\\p{N}_])'+word+'(?![\\p{L}\\p{N}_])','iu').test(name):name.includes(word)))?.[1];let label='';
    if(!word){let number=++unnamed;while(number){number--;label=String.fromCharCode(65+number%26)+label;number=Math.floor(number/26);}}
    roles.set(person.id,word?'THE '+word:'PERSON '+label);
  }
  const counts=new Map();for(const role of roles.values())counts.set(role,(counts.get(role)||0)+1);
  return [...roles].map(([id,role])=>({id,role:role+(counts.get(role)>1?' '+id:'')}));
}
function displayPersonHtml(id) {
  const person=P.breakdown?.characters?.find(c=>c.id===id) || {},entry=P.replacements?.characters?.find(c=>c.source_id===id) || {};
  const asset=P.assets?.find(a=>a.id===entry.asset_id && a.type==='image' && !P.missing_asset_files?.includes(a.id));
  const frame=P.assets?.find(a=>a.type==='image' && !P.missing_asset_files?.includes(a.id) && (a.frame_character===id || (!a.frame_character && a.file?.split('/').at(-1)?.startsWith('frame-'+id+'-'))));
  // *_url can request an on-demand thumbnail that writes into projects/. Use existing files only.
  const originals=Object.values(P.scene_cast || {}).flatMap(r=>r.subjects || []).filter(s=>s.key===id);
  const file=['replace','add'].includes(entry.action)?asset?.file:originals.find(s=>s.cast_original_file)?.cast_original_file || originals.find(s=>s.original_file)?.original_file || frame?.file || person.avatar || P.storyboard?.cells?.find(c=>c.characters?.includes(id) && c.file)?.file;
  const description=Array.from(String(entry.description || '').trim()).slice(0,8).join('');
  return `${file?`<img class="display-person-avatar" src="${esc(fileUrl(file))}" width="16" height="16" alt="">`:''}${esc('人物'+id+(description?'（'+description+'）':''))}`;
}
function displayTextHtml(value) {
  const text=displayPunctuation(value);
  const roles=displayRoleNames(),labels=new Map();
  for(const {id,role} of roles)labels.set(role.toUpperCase(),id);
  // Older prompts may carry explicit PERSON C1/N1 aliases.
  for(const {id} of roles)labels.set('PERSON '+id.toUpperCase(),id);
  for(const role of ['THE NEW PRODUCT','THE PRODUCT','NEW PRODUCT','PRODUCT'])labels.set(role,null);
  const escapeRegex=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const pattern=new RegExp('(?<![A-Za-z0-9_])(?:'+[...labels.keys()].sort((a,b)=>b.length-a.length).map(escapeRegex).join('|')+')(?![A-Za-z0-9_])','gi');
  let result='',cursor=0;
  for(const match of text.matchAll(pattern)){
    result+=esc(text.slice(cursor,match.index));
    const id=labels.get(match[0].toUpperCase());result+=id?displayPersonHtml(id):'商品';cursor=match.index+match[0].length;
  }
  return result+esc(text.slice(cursor));
}

let buildMismatchSince = null;
let PAGE_BUILD = null;
let statusTimer = null;
let STATUS = null;
let projectList = [], projectListRead = 0;
let P = null;            // current project from server
let batchBoardState = null;
let S = null;            // editable script draft
let motionCheckReturnFocus = null;
const seen = { breakdown: "", assets: "", rep: "", script: "" };
const dirty = { breakdown: false, rep: false, script: false };
let pollTimer = null;
let projectGeneration = 0, openingProjectId = null;
const deletingProjects = new Set(), deletedProjects = new Set();

const CARD_TITLES = {
  characters: "人物", scene: "场景", systems: "视觉/声音系统", beats: "段落节拍", shots: "分镜", productMoments: "产品出现时刻", dialogue: "台词（逐句）", overlays: "字幕/花字图层", viralLogic: "爆款逻辑",
};
const ROLE_LABELS = { person_identity: "人物", product_truth: "商品", scene: "场景", target_state: "补充参考图", storyboard: "分镜图", reference: "其他参考", voice_identity: "声线" };
const RECIPE_AXES = {
  speaker: { "composition-stability": ["soft-locked", "strict-locked", "flexible-ugc"], "camera-motion": ["none", "subtle-punch-in-return"],
    "edit-rhythm": ["continuous-take", "pause-trim-jump-cuts"], performance: ["natural-explainer", "high-energy-ugc", "calm-authority", "reactive-playful"],
    gesture: ["natural", "restrained", "compact", "expressive"] },
  broll: { "material-mode": ["practical-real", "screen-demo", "product-beauty", "proof-capture", "stylized-vfx"],
    "story-shape": ["single-moment", "reaction-beat", "process-demo", "before-after", "montage-sequence"],
    "edit-language": ["continuous-shot", "jump-cut-beats", "match-cut-montage", "insert-cutaway"],
    "camera-language": ["reference-locked", "selfie-vlog", "handheld-follow", "push-reveal", "product-macro"],
    "motion-intensity": ["readable", "micro", "lively", "impact"] },
};
const RECIPE_AXIS_LABELS = {
  "composition-stability": "构图稳定性", "camera-motion": "镜头运动", "edit-rhythm": "剪辑节奏", performance: "表演风格", gesture: "肢体动作",
  "material-mode": "画面素材类型", "story-shape": "故事结构", "edit-language": "剪辑方式", "camera-language": "运镜方式", "motion-intensity": "运动强度",
};
const RECIPE_VALUE_LABELS = {
  "flexible-ugc": "灵活的创作者视频构图", "soft-locked": "基本锁定构图，允许轻微变化", "strict-locked": "严格锁定构图",
  none: "固定镜头", "subtle-punch-in-return": "重点处轻推近后回到原构图", "continuous-take": "连续口播，保留自然呼吸", "pause-trim-jump-cuts": "去停顿跳剪",
  "natural-explainer": "自然亲切讲解", "high-energy-ugc": "活力充沛的短视频表达", "calm-authority": "沉稳笃定表达", "reactive-playful": "俏皮灵动回应",
  restrained: "克制肢体动作", compact: "躯干附近小幅讲解手势", natural: "自然手势与姿态变化", expressive: "丰富的手势与肩部姿态",
  "practical-real": "真实生活实拍", "screen-demo": "设备屏幕实拍演示", "product-beauty": "商品质感展示", "proof-capture": "凭证与评价等证据展示", "stylized-vfx": "风格化特效比喻",
  "single-moment": "连续瞬间与细微变化", "reaction-beat": "刺激、领会与反应", "process-demo": "分步骤过程演示", "before-after": "前后状态对比", "montage-sequence": "围绕同一主题的多镜头组接",
  "continuous-shot": "连续镜头", "jump-cut-beats": "按动作阶段跳剪", "match-cut-montage": "相似构图或动作匹配剪辑", "insert-cutaway": "插入相关细节特写",
  "reference-locked": "锁定参考构图", "selfie-vlog": "自然晃动的自拍或镜前记录", "handheld-follow": "手持跟拍", "push-reveal": "推近、俯仰或揭示重点", "product-macro": "商品微距与细节运镜",
  micro: "微小运动，突出细节证据", readable: "清晰易懂的动作", lively: "活泼的人物、镜头或物体运动", impact: "飞溅、爆发等强冲击运动",
};
const PACE_LABELS = { normal: "正常", fast: "快速", slow: "慢速" };
const KIND_LABELS = { speaker: "口播/对白（speaker）", broll: "无对白画面（B-roll）", motion: "动作复刻（motion）", camera: "运镜复刻（camera）" };
const PACE_RATE = { zh: { slow: 4.2, normal: 5.25, fast: 6.5625 }, en: { slow: 4.2, normal: 4.6, fast: 5.6 }, ja: { slow: 6, normal: 7.5, fast: 9.375 }, es: { slow: 4.72, normal: 5.9, fast: 7.375 } };
function speechUnits(text) {
  const han = (text.match(/[\u3400-\u9fff\uf900-\ufaff]/g) || []).length;
  const latin = (text.match(/[A-Za-z]+(?:['’][A-Za-z]+)*/g) || []).reduce((n, word) => {
    const w=word.toLowerCase();let groups=(w.match(/[aeiouy]+/g) || []).length;
    if(w.endsWith('e') && groups>1 && !w.endsWith('le') && !w.endsWith('ee'))groups--;
    return n+Math.max(1,groups);
  }, 0);
  return han + latin + (text.match(/\p{Nd}/gu) || []).length;
}
function estimate(t) {
  if (t.kind !== "speaker" || !t.dialogue) return null;
  const r = (PACE_RATE[t.language] || PACE_RATE.zh)[t.pace || "normal"];
  return speechUnits(t.dialogue) / r + Number(t.padding || 0);
}
function targetSeconds(t) {
  if(t.target_seconds != null && Number.isFinite(Number(t.target_seconds)) && Number(t.target_seconds)>0)return Number(t.target_seconds);
  const source=Number(t.source_end || 0)-Number(t.source_start || 0),est=Number(t.estimate);
  return t.kind==='speaker' && Number.isFinite(est) && est>source+1 ? est : source>0 ? source : Number(t.duration || 0);
}
const IMAGE_MODELS = ["gpt-image-2", "nano-banana-2", "nano-banana-2.1", "nano-banana-pro", "nano-banana"];
const REMOTE_STATUS_LABELS = {completed:'已完成',in_progress:'生成中',failed:'失败',queued:'排队中'};

// Generated HTML is cached separately from live DOM (open/value are user state).
function sceneEditorEnabled() { return !!P?.scene_cast || Object.hasOwn(P?.replacements || {}, 'scene_plan'); }

const operationErrors = new Map();
const cameraChannels = new Map();
const dialogueInstructions = new Map();
const dialogueProposalEdits = new Map();
const dialogueFailureKinds = new Map();
const dialogueRequests = new Map();
const dialogueNotices = new Map();
const SCRIPT_WRITE_BUTTONS = '#saveScript, #scriptBtn, #workflowNext, [data-third-retry="script"], [data-dialogue-adopt], [data-dialogue-keep], [data-dialogue-propose], [data-third-retry="dialogue"], [data-fix-camera], [data-fix-image1], [data-camera-use], [data-scene-use], [data-preview], [data-delasset], [data-imgresume], [data-choose]';
const renderedHtml = new WeakMap();
const DOM_KEYS = ["data-library-name", "data-library-photo", "data-fission-slot", "data-fission-combination", "data-fission-product", "data-fission-note", "data-compare-key", "data-segment-outline", "data-edit-sheet", "data-edit-request", "data-edit-change", "data-edit-remove-text", "data-edit-cell-request", "data-edit-material", "data-story-sheet", "data-story-row", "data-cast-card", "data-cast-description", "data-cast-asset", "data-cast-action", "data-cast-name", "id", "data-camera-key", "data-camera-person", "data-camera-record", "data-task", "data-seg-key", "data-live", "data-imgbox", "data-run-key", "data-image-key", "data-fix-code", "data-detail-key", "data-asset", "data-card", "data-char", "data-prod", "data-note-key", "data-replacement-check", "data-preview-reason", "data-motion-timeline", "data-k", "data-m", "data-f", "data-r", "data-recipe", "data-cast-row", "data-cast", "data-cast-desc", "data-scene-subject", "data-scene-on", "data-scene-pick", "data-scene-photo", "data-scene-time", "data-scene-channel", "data-scene-add-choice", "data-scene-prompt", "data-scene-draft", "data-scene-frame-auto", "data-scene-frame-open", "data-scene-frame-at", "data-scene-add", "data-scene-follow", "data-scene-remove", "data-scene-use", "data-scene-merge", "data-scene-split", "data-scene-drop", "data-scene-restore"];
function domKey(node) {
  if (node.nodeType !== 1) return "";
  for(const attr of ['data-director-card','data-director-generation','data-director-run','data-director-brief'])if(node.hasAttribute(attr))return `${node.tagName}:${attr}:${node.getAttribute(attr)}`;
  for (const attr of DOM_KEYS) if (node.hasAttribute(attr)) return `${node.tagName}:${attr}:${node.getAttribute(attr)}`;
  if (["VIDEO", "AUDIO"].includes(node.tagName)) return `${node.tagName}:src:${node.getAttribute("src")}`;
  return "";
}
function domIdentity(node) {
  const keys = [];
  for (let el = node; el; el = el.parentElement) { const key = domKey(el); if (key) keys.unshift(key); }
  return keys.join("/");
}
function preserveView(change) {
  // Minimal offline renderer tests do not implement a browser viewport.
  if (typeof window === "undefined") return change();
  const x = window.scrollX, y = window.scrollY;
  const active = document.activeElement;
  const top = Math.max(0, $(".topbar")?.getBoundingClientRect().bottom || 0) + 16;
  const visible = (el) => { const r = el?.getBoundingClientRect(); return r && r.bottom > top && r.top < window.innerHeight; };
  const dialogScene = active?.closest('[data-scene-dialog]')?.dataset.sceneDialog;
  const sceneCard = active?.closest('[data-scene-camera]') || (dialogScene && $(`[data-scene-camera="${dialogScene}"]`));
  const anchor = sceneCard || (visible(active) && active !== document.body ? active
    : document.elementFromPoint?.(Math.round(window.innerWidth / 2), top)?.closest("[data-scene-camera], [data-detail-key], [data-run-key], [data-task], [data-seg-key], [data-asset], [id]"));
  const anchorKey = anchor && domIdentity(anchor), anchorTop = anchor?.getBoundingClientRect().top;
  const result = change();
  const next = anchor?.isConnected ? anchor : anchorKey && $$("[id], [data-scene-camera], [data-detail-key], [data-run-key], [data-task], [data-seg-key], [data-asset]").find(el => domIdentity(el) === anchorKey);
  const targetY = next ? y + next.getBoundingClientRect().top - anchorTop : y;
  if (window.scrollY !== targetY || window.scrollX !== x) window.scrollTo({ left: x, top: targetY, behavior: "instant" });
  return result;
}
function patchChildren(parent, incoming, retainTaskViews) {
  const old = [...parent.childNodes], used = new Set();
  let cursor = parent.firstChild;
  for (const wanted of [...incoming.childNodes]) {
    const key = domKey(wanted);
    const same = (node) => node.nodeType === wanted.nodeType && node.tagName === wanted.tagName && domKey(node) === key;
    const current = cursor && !used.has(cursor) && same(cursor) ? cursor : old.find(node => !used.has(node) && same(node));
    if (!current) { parent.insertBefore(wanted.cloneNode(true), cursor); continue; }
    used.add(current);
    if (current !== cursor) {
      // moveBefore preserves playback/focus on browsers that support stateful moves.
      if (parent.moveBefore) parent.moveBefore(current, cursor);
      else parent.insertBefore(current, cursor);
    }
    if (current.nodeType === 1) {
      // Task cards contain independently rendered runs and an optional draft form.
      if (!(retainTaskViews && (current.hasAttribute("data-live") || current.hasAttribute("data-imgbox")))) {
        for (const { name } of [...current.attributes]) if (!wanted.hasAttribute(name) && !(name === "open" && current.tagName === "DETAILS")) current.removeAttribute(name);
        for (const { name, value } of [...wanted.attributes]) if (current.getAttribute(name) !== value && !(name === "open" && current.tagName === "DETAILS")) current.setAttribute(name, value);
        renderedHtml.delete(current); // Parent updates invalidate independently cached subregions.
        patchChildren(current, wanted, retainTaskViews);
        if (["INPUT", "TEXTAREA", "SELECT"].includes(current.tagName) && current.type !== "file") {
          if (current.value !== wanted.value) current.value = wanted.value;
          if (current.tagName === "INPUT") current.checked = wanted.checked;
        }
      }
    } else if (current.nodeValue !== wanted.nodeValue) current.nodeValue = wanted.nodeValue;
    cursor = current.nextSibling;
  }
  old.filter(node => !used.has(node)).forEach(node => node.remove());
}
function updateHtml(box, html, retainTaskViews = false) {
  if (!box) return false;
  const previous = renderedHtml.get(box), pid = P?.id;
  if (previous?.html === html && previous.pid === pid) return false;
  preserveView(() => {
    if (!box.querySelectorAll || !document.createElement) { box.innerHTML = html; return; }
    const sameProject = !previous || previous.pid === pid;
    const details = new Map(sameProject ? $$('[data-detail-key]', box).map(el => [domIdentity(el), el.open]) : []);
    const active = document.activeElement;
    const focused = sameProject && box.contains(active) && active.matches("input, textarea, select");
    const input = focused ? { key: domIdentity(active), value: active.value, start: active.selectionStart, end: active.selectionEnd, direction: active.selectionDirection, scroll: active.scrollTop } : null;
    const template = document.createElement("template"); template.innerHTML = html;normalizeErrorMarkup(template.content);
    if (!sameProject) box.innerHTML = "";
    patchChildren(box, template.content, retainTaskViews && sameProject);
    $$('[data-detail-key]', box).forEach(el => { const key = domIdentity(el); if (details.has(key)) el.open = details.get(key); });
    if (input) {
      const next = active.isConnected ? active : $$("input, textarea, select", box).find(el => domIdentity(el) === input.key);
      if (next) {
        next.value = input.value;
        if (document.activeElement !== next) next.focus({ preventScroll: true });
        if (input.start !== null && input.start !== undefined && next.setSelectionRange) next.setSelectionRange(input.start, input.end, input.direction);
        next.scrollTop = input.scroll;
      }
    }
  });
  renderedHtml.set(box, { html, pid });
  if(dialogueRequests.has(`${pid}:script-save:`))updateScriptSaveUi();
  return true;
}

function internalAsset(a) { return a.internal || (a.roles?.includes('scene') && (a.label || '').startsWith('原片参考帧')); }
const PRODUCT_NOTE_WARNING = '建议写清楚图里哪件是商品，例如：粉色针织开衫 + 碎花上衣 + 白色短裙。不写的话，主镜头图和台词改写可能沿用原片商品';
function productNoteWarning() { return P.replacements?.product?.action === 'replace' && !P.replacements.product.description?.trim(); }
function offscreenCharacter(c) {
  return /未见|不出镜|画外|off[- ]screen|not visible/i.test(c.appearance || '') || /画外/.test([c.name,c.role].join(' '));
}

function clientBuildHeaders(method) {
  if (!PAGE_BUILD || !["POST","PUT","PATCH","DELETE"].includes(method)) return {};
  return typeof PAGE_BUILD === 'string' ? {"X-Client-Build":PAGE_BUILD} : {"X-Server-Build":PAGE_BUILD.server_build,"X-Web-Build":PAGE_BUILD.web_build};
}

function publicText(value) { return String(value ?? '').replace(/(?<![\w/\\.\-])jimmy(?![\w/\\.\-])/gi, '三方渠道').replace(/吉米币/g, '点').replace(/吉米/g, '三方渠道')
  .replace(/帧网格/g, '分镜截图').replace(/链路五/g, '生成流程').replace(/精确任务单/g, '生成任务详情'); }
function publicErrorText(value,kind='操作') {
  const raw=String(value?.message || value || '');
  if(/(?:rejected by the safety system|safety|moderation)/i.test(raw) && /(?:400|403|rejected)/i.test(raw))return '官方的安全审核没有通过这次生图。可以点“改用三方渠道重试”，或者改一下要改的内容再试。';
  if(/(?:HTTP[^\n]*[45]\d\d|request[_ -]?id|https?:\/\/)/i.test(raw) && /(?:error|failed|[45]\d\d|拒绝|失败)/i.test(raw) || /[a-z][a-z0-9_:/?=.&%+-]{120}/i.test(raw))return `这次${kind}没有完成。可以重试，或展开“详细信息”查看原因。`;
  return publicText(raw);
}
function errorHtml(value,kind='操作'){
  const raw=String(value?.message || value || ''),summary=publicErrorText(raw,kind),detail=summary!==publicText(raw) || raw.length>40 || raw.includes('\n');
  return `<span class="error-message" role="alert"><span class="error-summary">${esc(detail && summary===publicText(raw)?summary.slice(0,120)+'…':summary)}</span>${detail?`<details class="error-details"><summary>详细信息</summary><button class="small ghost" data-copy-error>复制</button><pre>${esc(raw)}</pre></details>`:''}</span>`;
}
function normalizeErrorMarkup(root){
  for(const el of $$('.action-error,.caption-warning,.library-upload-errors,.dialogue-error,.hint,.st.skipped,[role="alert"]',root)){
    if(el.closest('.error-message') || el.querySelector('.error-message,button,input,textarea,select,details,img,video,a'))continue;
    const text=el.textContent;
    if(publicErrorText(text)!==publicText(text) || el.matches('.action-error,.library-upload-errors,.fission-block-reason,.dialogue-error,[role="alert"]') || el.matches('.caption-warning,.st.skipped') && /失败|错误|拒绝|未完成|没.*完成|连不上|error|HTTP|request[_ -]?id/i.test(text))el.innerHTML=errorHtml(text);
  }
}
function providerLabel(key, value) { return publicText(value?.label || ({openai:'官方',official:'官方',third_party:'三方渠道'}[key]) || key); }
function thirdRetry(kind, attrs = '') { return `<button class="small ghost" data-third-retry="${kind}" data-retry-project="${esc(P.id)}" ${attrs}>改用三方渠道重试</button>`; }
function operationKey(kind, id = '') { return `${P.id}:${kind}:${id}`; }
function operationFailure(kind, error, id = '') { operationErrors.set(operationKey(kind,id), publicText(error.message || error)); }
const cameraRouteSelected = new Set();
function imageChannel(cid, sid, rec) { const key=cameraDraftKey(cid,sid);return (cameraRouteSelected.has(key) ? cameraChannels.get(key) : null) || routeFor('camera_image').channel || STATUS?.default_image_channel || (STATUS?.providers?.openai?.ready ? 'official' : 'third_party'); }
function imageModelLabel(model) {return model==='gpt-image-2.6-sunburst'?'gpt-image-2.6 sunburst':publicText(model || '');}
function imageRecordInfo(rec) {
  const preview=rec?.preview || {},channel=preview.channel || (typeof rec==='string'?rec:rec?.channel) || (rec && typeof rec==='object' && Object.keys(rec).length?'third_party':undefined);
  return {channel,model:preview.model || '',quality:preview.quality || ''};
}
function imageModelAnnotation(rec) {
  const {model,quality}=imageRecordInfo(rec);
  return model?[imageModelLabel(model),({auto:'自动画质',high:'高画质'})[quality]].filter(Boolean).join(' · '):'';
}
function imageModelAnnotationHtml(rec) {const label=imageModelAnnotation(rec);return label?`<small class="image-model-note">${esc(label)}</small>`:'';}
function imageEstimate(rec) {
  const {channel,model,quality}=imageRecordInfo(rec);
  return channel==='official'?({'gpt-image-2:auto':5,'gpt-image-2:high':20,'gpt-image-2.6-sunburst:high':8})[model+':'+quality] ?? null:null;
}
function imageFee(rec) {
  const {channel}=imageRecordInfo(rec),estimate=imageEstimate(rec),label=imageModelAnnotation(rec);
  return channel==='official'?`官方${label?' · '+label:''}，${estimate===null?'按 OpenAI 计费':'约 $'+(estimate/100).toFixed(2)}`:channel==='third_party'?'三方渠道，约 0.02 点':'生成预览后显示费用';
}
function imageBatchFee(records) {
  const groups=new Map();let total=0,known=0,unknown=false;
  for(const rec of records.filter(Boolean)){const info=imageRecordInfo(rec),key=info.channel==='third_party'?'third_party':JSON.stringify(info);if(!groups.has(key))groups.set(key,{rec,count:0});groups.get(key).count++;}
  const parts=[...groups.values()].map(({rec,count})=>{const {channel}=imageRecordInfo(rec),estimate=imageEstimate(rec);
    if(estimate!==null){total+=estimate*count;known++;return `${count} 张官方 · ${imageModelAnnotation(rec)}，约 $${(estimate*count/100).toFixed(2)}`;}
    if(channel==='third_party')return `${count} 张三方渠道，约 ${(count*0.02).toFixed(2)} 点`;
    unknown=true;return `${count} 张${imageFee(rec)}`;
  });
  if(known>1)parts.push(`${unknown?'已知官方费用':'官方费用'}合计约 $${(total/100).toFixed(2)}`);
  return parts.join('；');
}
function storyboardVersionRecord(version,cellId,versions=[]) {
  if(!cellId)return P?.images?.[version?.image_id];
  const seen=new Set();let current=version;
  while(current && !seen.has(current)){
    seen.add(current);
    const rec=P?.images?.[current.image_id],context=rec?.storyboard_context || {},from=current.from || '';
    const cells=from.startsWith('cells:')?from.slice(6).split(','):from.startsWith('cell:')?[from.slice(5)]:context.selected_cells?.length?context.selected_cells:context.cell_id?[context.cell_id]:[];
    if(from==='full' || cells.includes(cellId))return rec;
    if(!cells.length)return;
    const parent=current.base_version || context.base_current,index=versions.indexOf(current);
    current=parent?versions.find(v=>v.id===parent):index>0?versions[index-1]:undefined;
  }
}
function tokenUsageText(usage) {
  const count=value=>typeof value==='number' && Number.isFinite(value) && value>=0 ? value : null;
  const input=count(usage?.input_tokens) ?? count(usage?.prompt_tokens),output=count(usage?.output_tokens) ?? count(usage?.completion_tokens);
  const total=count(usage?.total_tokens) ?? (input!==null || output!==null ? (input || 0)+(output || 0) : null);
  return total===null ? '本次用量未提供' : `本次用了约 ${Math.round(total)} 个 token`;
}
function imageUsage(rec) { return rec?.usage ? `<p class="hint">${tokenUsageText(rec.usage)}</p>` : ''; }
function imageRecovery(rec) { return rec.channel === 'official' && !rec.result ? '<p class="hint">官方生图没有可恢复的远端任务，请重新确认生图</p>' : `<button class="small ghost" data-imgresume="${esc(rec.id)}">${rec.state === 'query_pending' ? '再查一次' : '恢复'}</button>`; }
function queryRetryHtml(record, image = false) {
  if(!record?.query_retry)return '';
  const text = record.query_retry.exhausted ? '三方渠道一直查不到结果，任务可能还在生成'
    : `生成中 · 三方渠道暂时连不上，正在自动重新查询（已重试 ${record.query_retry.attempts || 0} 次）`;
  return `<p class="hint" role="status" data-query-retry>${esc(text)}</p>${image && record.query_retry.exhausted ? imageRecovery(record) : ''}`;
}

function routeFor(step) {
  const defaults = STATUS?.routes || {}, saved = P?.routes || {};
  const follow = {check:'script',dialogue_proposal:'script',camera_prompt:'analysis'};
  const read = (key, seen = []) => {
    if(seen.includes(key)) return {};
    let r=P?.resolved_routes?.[key] || saved[key] || (follow[key] && saved[follow[key]] ? read(follow[key],[...seen,key]) : null) || defaults[key];
    if(typeof r === 'string') {const [channel,...rest]=r.split(':');return channel==='follow' ? read(rest.join(':'),[...seen,key]) : {channel,model:rest.join(':')};}
    return r || {};
  };
  return read(step);
}
function routeName(route = {}) {
  const channel=route.channel || '', provider=STATUS?.providers?.[channel];
  if(provider?.model===route.model && provider?.label?.includes(' · '))return providerLabel(channel,provider);
  const names={openai:'官方',official:'官方',jimmy:'三方渠道',third_party:'三方渠道'};
  const label=providerLabel(channel,provider);
  const name=names[channel] || (provider?.model && label.endsWith(' · '+provider.model) ? label.slice(0,-(provider.model.length+3)) : label);
  const model=route.model==='gpt-5.6-sol' ? 'GPT-5.6 Sol' : publicText(route.model || '未配置');
  return `${name} · ${model}`;
}
function settingsRoute(step) {
  return STATUS?.routes?.[step] || {channel:step==='camera_image' ? STATUS?.default_image_channel || 'third_party' : STATUS?.default_llm || 'jimmy',
    model:step==='camera_image' ? STATUS?.openai_image_model || 'gpt-image-2' : STATUS?.providers?.[STATUS?.default_llm]?.model};
}
function legacyRouteStep(step) {
  if(P?.routes?.[step])return step;
  const parent={check:'script',dialogue_proposal:'script',camera_prompt:'analysis'}[step];
  return parent && P?.routes?.[parent] ? parent : null;
}
function defaultRouteLabel(step) {
  const route=routeFor(step),name=routeName(route.channel ? route : settingsRoute(step));
  return legacyRouteStep(step) ? `这个项目单独用：${name}` : `跟随设置（${name}）`;
}
function legacyRouteNote(step) {
  const saved=legacyRouteStep(step);
  if(!saved)return '';
  const source={analysis:'02',script:'04'}[saved];
  return `这个项目单独设定${saved!==step && source ? `，去第 ${source} 步可恢复跟随设置` : ''}`;
}
function legacyRouteHtml(step) {
  const saved=legacyRouteStep(step);
  const label={check:'检查模型',dialogue_proposal:'改写模型',camera_prompt:'提示词模型',video:'视频模型'}[step];
  // Dialogue already shows its actual model beside the editable proposal.
  if(!saved || saved!==step) {
    if(step==='check' || saved && step==='camera_prompt')return `${label}：${esc(routeName(routeFor(step)))}（${esc(legacyRouteNote(step) || '跟随设置')}）`;
    return '';
  }
  const model=label ? `${label}：${esc(routeName(routeFor(step)))}（${esc(legacyRouteNote(step))}） ` : '';
  return `${model}这个项目之前单独选过这个模型 <button type="button" class="small ghost" data-clear-route="${step}">恢复跟随设置</button>`;
}
function renderProviderSelect(sel,step) {
  const selected=sel.dataset.userSelected==='1' ? sel.value : '';
  updateHtml(sel,providerOptions(selected,step));sel.value=selected;sel.dataset.init='1';
  renderLegacyRoutes();
}
function renderLegacyRoutes() {
  const shown=new Set();
  $$('[data-legacy-route]').forEach(hint=>{
    const step=hint.dataset.legacyRoute,owns=legacyRouteStep(step)===step;
    updateHtml(hint,owns && shown.has(step) ? '' : legacyRouteHtml(step));
    if(owns)shown.add(step);
  });
}

function projectUnavailable(id) { return deletingProjects.has(id) || deletedProjects.has(id); }
function projectRequestGuard(method, url) {
  const pid = url.match(/^projects\/([^/?#]+)(?:[/?#]|$)/)?.[1], generation = projectGeneration;
  const deletion = method === 'DELETE' && url === `projects/${pid}`;
  return () => {
    if (!pid || deletion && !deletedProjects.has(pid)) return;
    if (generation !== projectGeneration || projectUnavailable(pid)) {
      const error = new Error('项目已切换或正在删除，请稍候');
      error.staleProject = true; throw error;
    }
  };
}

// ---------- infra ----------
async function api(method, url, body, options = {}) {
  const checkProject = projectRequestGuard(method, url); checkProject();
  const binary=typeof Blob!=='undefined' && body instanceof Blob;
  const res = await workbenchFetch("/api/" + url, {
    ...(options.signal ? { signal: options.signal } : {}),
    method, headers: { ...(body ? { "Content-Type": binary ? body.type || "application/octet-stream" : "application/json" } : {}), ...clientBuildHeaders(method) }, body: binary ? body : body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  checkProject();
  if (!res.ok) {
    const error = new Error(res.status === 404 && /not found/i.test(data.error || res.statusText) ? "后台是旧版本工作台，没有这个功能：请重新双击 start.command / start.bat 重启" : (data.error || res.statusText));
    error.issues = data.issues; error.errors = data.errors; error.kind = data.error_kind;
    throw error;
  }
  return data;
}
async function workbenchFetch(url, options) {
  try {
    return await fetch(url, options);
  } catch (error) {
    if (error instanceof TypeError) {
      const offline = new Error('连不上工作台：它可能已经停止运行。请在终端重新运行 start.command，然后刷新页面。');
      offline.kind = 'transient';
      throw offline;
    }
    throw error;
  }
}
async function upload(url, file) {
  const checkProject = projectRequestGuard('POST', url); checkProject();
  const res = await workbenchFetch("/api/" + url, { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name), ...clientBuildHeaders("POST") }, body: file });
  const data = await res.json().catch(() => ({}));
  checkProject();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
// Bound the entire operation, including response.json(). Some pending file reads
// cannot be cancelled, so late results must never advance to the upload step.
async function withTimeout(action, milliseconds, message, controller) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(action),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error(message));
          controller?.abort();
        }, milliseconds);
      }),
    ]);
  } finally { clearTimeout(timer); }
}
function libraryRequest(method, url, body) {
  const controller = new AbortController();
  return withTimeout(() => api(method, url, body, {signal:controller.signal}), 30000, '素材库没有响应，请关掉再打开；照片不受影响。', controller);
}
async function uploadPhoto(url, file) {
  if(file.size > 30 * 1024 * 1024)throw new Error(`“${file.name}”超过 30MB，换一张小一点的照片。`);
  const readError=`“${file.name}”读不出来，没有上传。如果照片在 iCloud 或“照片”App 里，先把它存到本机（比如桌面）再选。`;
  let body;
  try { body=await withTimeout(() => file.arrayBuffer(), 20000, readError); }
  catch { throw new Error(readError); }
  const controller=new AbortController(),timeoutMessage=`“${file.name}”上传超过 1 分钟还没完成，已停止。请重试，或换一张照片。`;
  try {
    return await withTimeout(async () => {
      const res=await workbenchFetch('/api/'+url, {method:'POST',signal:controller.signal,
        headers:{'X-Filename':encodeURIComponent(file.name),...(file.type?{'Content-Type':file.type}:{}),...clientBuildHeaders('POST')},body});
      const data=await res.json();
      if(!res.ok)throw new Error(data.error || res.statusText);
      if(!data.item?.id)throw new Error('工作台没有返回照片信息，请重新打开素材库查看。');
      return data;
    }, 60000, timeoutMessage, controller);
  } catch(error) {
    // The server may have stored the photo just before the timeout fired; look it up before reporting failure.
    if(error.message!==timeoutMessage || !globalThis.crypto?.subtle)throw error;
    const found=await (async()=>{try{
      const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',body))].map(b=>b.toString(16).padStart(2,'0')).join('');
      const kind=new URLSearchParams(url.split('?')[1] || '').get('kind');
      return (await libraryRequest('GET','library')).items?.find(i=>i.sha256===sha && (!kind || i.kind===kind));
    }catch{return null;}})();
    if(found)return {item:found,duplicate:false};
    throw error;
  }
}
function toast(msg, err = false) {
  const t = $("#toast");
  const raw=msg;msg = publicText(msg);
  const long = msg.length > 120;
  t.innerHTML = err && (long || publicErrorText(raw)!==publicText(raw)) ? errorHtml(raw) : long ? `${esc(msg.slice(0, 120))}…<details><summary>展开</summary><pre>${esc(msg)}</pre></details>` : esc(msg);
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  const dismiss = () => { toast._t = setTimeout(() => (t.className = "toast"), err ? 6000 : 2600); };
  if (long && $("details", t)) $("details", t).addEventListener("toggle", (e) => { clearTimeout(toast._t); if (!e.target.open) dismiss(); });
  dismiss();
}
async function guard(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { if (!e.staleProject) toast(e.message, true); throw e; }
}
const fileUrl = (rel) => String(rel).startsWith(`/api/projects/${P.id}/thumb?`) ? rel : `/files/${P.id}/${rel}`;
function store(k, v) { try { v === undefined ? localStorage.getItem(k) : localStorage.setItem(k, v); } catch (_) {} }
function storeGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function selectedProvider(sel) {
  if (sel.dataset.userSelected === '1' && sel.value) return sel.value;
  return routeFor(sel.id === 'scriptProvider' ? 'script' : 'analysis').channel || STATUS?.default_llm;
}
function requestRoute(step, sel) {
  const explicit=sel?.dataset.userSelected === '1' && !!sel.value, current=routeFor(step);
  return {provider:explicit ? sel.value : current.channel || selectedProvider(sel), route:!explicit && current.channel ? current : undefined, route_selected:explicit};
}
function finishTemporaryProvider(step,selection) {
  const sel=$(step==='script' || step==='dialogue_proposal' ? '#scriptProvider' : '#analyzeProvider');
  if(selection.route_selected && sel.dataset.userSelected==='1' && sel.value===selection.provider) {
    sel.dataset.userSelected='';sel.value='';renderProviderSelect(sel,sel.id==='scriptProvider' ? 'script' : 'analysis');
    updateSendVideo();updateDialogueModelLabels();
  }
}
function finishTemporaryImage(record,pid=P.id) {
  if(!/^(?:camera:|scene-camera:)/.test(record?.for_task || ''))return;
  const key=record.for_task.startsWith('scene-camera:') ? pid+':scene:'+record.for_task.slice('scene-camera:'.length) : pid+':'+record.for_task.slice('camera:'.length);
  if(cameraChannels.get(key)===record.channel){cameraChannels.delete(key);cameraRouteSelected.delete(key);}
}
function providerOptions(sel) {
  const step=arguments[1] || 'analysis';
  const ps = STATUS?.providers || {};
  return `<option value="" ${!sel ? 'selected' : ''}>${esc(defaultRouteLabel(step))}</option>`+Object.entries(ps).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(providerLabel(k,v))}${v.ready ? "" : "（未配置）"}</option>`).join("");
}

// ---------- boot ----------
function updateBuildBanner() {
  if (!PAGE_BUILD) PAGE_BUILD = STATUS?.server_build && STATUS?.web_build ? {server_build:STATUS.server_build,web_build:STATUS.web_build} : STATUS?.build;
  const mismatch = PAGE_BUILD && (typeof PAGE_BUILD === 'string' ? (STATUS?.build !== PAGE_BUILD || STATUS?.build_on_disk !== PAGE_BUILD) : (PAGE_BUILD.server_build !== STATUS?.server_build || PAGE_BUILD.web_build !== STATUS?.web_build));
  const box = $("#staleBanner");
  box.hidden = !mismatch && !STATUS?.restart_blocked && !STATUS?.restart_pending;
  updateHtml(box, (STATUS?.restart_pending ? '工作台正在更新，请稍候刷新' : '工作台已更新，请刷新页面（Cmd+Shift+R） <button id="refreshPageBtn" class="small">立即刷新</button>')
    + (STATUS?.restart_blocked ? `<p>后台更新检查未通过：${esc(STATUS.restart_blocked.split(/\r?\n/)[0])}</p>` : ''));
}
function updateSendVideo() {
  const enabled = selectedProvider($("#analyzeProvider")) === "jimmy" && STATUS?.providers?.jimmy?.video_native === true;
  const input = $("#sendVideo");
  input.disabled = !enabled;
  if (!enabled) input.checked = false;
  $("#sendVideoHint").textContent = enabled ? "" : "当前模型会把视频切成多张截图来分析（只有三方渠道 Gemini 能直接看整段视频）";
}
async function pollStatus() {
  try {
    STATUS = await api("GET", "status");
    updateBuildBanner();
    updateSendVideo();
    updateDialogueModelLabels();
  } catch (_) {
    // Keep an already detected stale-page warning during a temporary disconnect.
  } finally {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(pollStatus, 5000);
  }
}
function renderProjectOptions(current=P?.id) {
  const select=$('#projectSelect');if(!select)return;
  if(batchBoardState)current='batch:'+batchBoardState.masterId;
  const list=projectList.filter(p=>!projectUnavailable(p.id)),ids=new Set(list.map(p=>p.id)),children=new Map();
  for(const p of list){const parent=p.derived_from?.project_id;if(parent && parent!==p.id && ids.has(parent)){if(!children.has(parent))children.set(parent,[]);children.get(parent).push(p);}}
  const ordered=[],visited=new Set();
  const add=(p,depth=0)=>{if(visited.has(p.id))return;visited.add(p.id);ordered.push({p,depth});for(const child of (children.get(p.id) || []).sort((a,b)=>(a.derived_from?.fission_number || a.derived_from?.row || 0)-(b.derived_from?.fission_number || b.derived_from?.row || 0)))add(child,depth+1);if(children.has(p.id))ordered.push({p,depth:depth+1,board:true});};
  for(const p of list)if(!ids.has(p.derived_from?.project_id))add(p);
  for(const p of list)add(p);
  select.innerHTML=ordered.map(({p,depth,board})=>`<option value="${esc(board?'batch:'+p.id:p.id)}" title="${esc(p.name)}">${depth?'　'.repeat(Math.min(depth,3))+'↳ ':''}${board?'批量看板（'+children.get(p.id).length+' 条）':esc(p.name || '未命名项目')+(children.has(p.id)?' · 母版':'')}</option>`).join('');
  if(batchBoardState && !children.has(batchBoardState.masterId))select.innerHTML+=`<option value="${esc(current)}">批量看板（${batchBoardState.data?.total || 0} 条）${batchBoardState.data?.master?.exists===false?' · 母版已删除':''}</option>`;
  if(current && !ids.has(current) && P?.id===current)select.innerHTML+=`<option value="${esc(current)}" title="${esc(P.name)}">${esc(P.name || '未命名项目')}</option>`;
  if(current)select.value=current;
  select.title=P && P.id===current?P.name || '未命名项目':'';
}
async function refreshProjectList(){
  const ticket=++projectListRead,list=await api('GET','projects');
  if(ticket!==projectListRead || !Array.isArray(list))return;
  projectList=list;renderProjectOptions();if(P)renderFissionBanner();
}
function fissionChangesText(data){
  const roles=new Map((data.breakdown?.characters || []).map(c=>[c.id,[c.id,c.name].filter(Boolean).join(' ')]));
  return (data.derived_from?.changes || []).map(c=>(c.key==='product'?'商品':roles.get(c.key) || c.key)+' → '+c.to_name).join('；');
}
function renderFissionBanner(){
  const box=$('#fissionBanner');if(!box)return;
  const d=P?.derived_from;box.hidden=!d;if(!d)return;
  const master=projectList.find(p=>p.id===d.project_id),exists=P.master_exists ?? d.master_exists ?? projectList.find(p=>p.id===P.id)?.master_exists ?? !!master;
  const name=d.master_name || d.master_snapshot?.name || master?.name || '母版';
  const changes=fissionChangesText(P);
  updateHtml(box,`<span class="fission-banner-copy" title="${esc('裂变项目：照“'+name+'”做的。换了：'+changes+'。原片、拆解、分段方案照搬母版。')}">裂变项目：照“${esc(name)}”做的。换了：${esc(changes || '沿用母版设定')}。原片、拆解、分段方案照搬母版。</span>${exists?`<button class="small ghost" data-open-master="${esc(d.project_id)}">打开母版</button>`:'<span>母版已删除</span>'}<button class="small ghost" data-batch-board="${esc(d.project_id)}">批量看板</button>`);
}
async function boot() {
  const generation = beginProjectView();
  STATUS = await api("GET", "status");
  if (generation !== projectGeneration) return;
  renderStatus();
  updateBuildBanner();
  clearTimeout(statusTimer);
  statusTimer = setTimeout(pollStatus, 5000);
  const list = (await api("GET", "projects")).filter(p => !projectUnavailable(p.id));
  if (generation !== projectGeneration) return;
  const sel = $("#projectSelect");
  projectList=list;renderProjectOptions();
  if (!list.length) { P = null; S = null; $("#emptyState").hidden = false; $("#workspace").hidden = true; clearTimeout(pollTimer); return; }
  if (!STATUS.jimmy && !STATUS.providers?.openai?.ready) setTimeout(() => { toast("第一次使用：先在设置里填三方渠道 Key"); openSettings(); }, 400);
  const last = storeGet("vrc.project");
  const pick = list.find((p) => p.id === last) ? last : list[0].id;
  sel.value = pick;
  await openProject(pick);
}
function renderStatus() {
  const s = STATUS;
  const pills = [
    [s.ffmpeg, "FFmpeg"], [s.asr, s.asr ? (s.asr_engines || []).join(" + ") : "未装转录模型"], [s.jimmy, s.jimmy ? "三方渠道 Key" : "未填三方渠道 Key"],
    ...Object.entries(s.providers).map(([k, v]) => [v.ready, providerLabel(k,v).split("（")[0]]),
  ];
  const rt = s.runtime || {};
  pills.push([rt.running_in_venv, rt.running_in_venv ? `依赖在工作台内 · ${rt.runtime_mb} MB` : "未用工作台环境（先运行 install.sh）"]);
  updateHtml($("#statusPills"), pills.map(([ok, l]) => `<span class="pill ${ok ? "ok" : "bad"}">${esc(l)}</span>`).join(""));
}
async function newProject() {
  const name = prompt("项目名称", "复刻 " + new Date().toLocaleDateString());
  if (name === null) return;
  const p = await guard(() => api("POST", "projects", { name }));
  $("#emptyState").hidden = true;
  const sel = $("#projectSelect");
  sel.insertAdjacentHTML("afterbegin", `<option value="${p.id}">${esc(p.name)} · ${esc(p.created_at)}</option>`);
  sel.value = p.id;
  await openProject(p.id);
}
function beginProjectView(id = null) {
  openingProjectId = id;
  clearTimeout(pollTimer); clearTimeout(replacementCheckTimer);
  ++replacementCheckSequence; replacementCheckPending = '';
  return ++projectGeneration;
}
function projectViewCurrent(pid, generation) {
  return generation === projectGeneration && P?.id === pid && !openingProjectId && !projectUnavailable(pid);
}
async function openProject(id) {
  if (projectUnavailable(id)) return;
  const previousBoard=batchBoardState;
  const generation = beginProjectView(id);
  let next;
  try { next = await api("GET", `projects/${id}`); }
  catch (error) {
    if (error.staleProject || generation !== projectGeneration) return;
    openingProjectId = null;
    if(previousBoard && batchBoardState===previousBoard){previousBoard.generation=generation;renderProjectOptions();}
    schedulePoll(); throw error;
  }
  if (generation !== projectGeneration || projectUnavailable(id)) return;
  closeBatchBoard();
  openingProjectId = null;
  $$('#analyzeProvider, #scriptProvider').forEach(sel=>{sel.dataset.userSelected='';delete sel.dataset.init;});
  store("vrc.project", id);
  Object.keys(seen).forEach((k) => (seen[k] = ""));
  Object.keys(dirty).forEach((k) => (dirty[k] = false));
  P = next;
  dirty.rep=castDrafts.has(id);
  projectList=projectList.map(p=>p.id===id?{...p,name:next.name,derived_from:next.derived_from}:p);
  if(!projectList.some(p=>p.id===id))projectList.push({id,name:next.name,derived_from:next.derived_from});
  renderProjectOptions(id);
  $("#workspace").hidden = false;
  $("#emptyState").hidden = true;
  renderAll();
  schedulePoll();
  return true;
}
async function refresh(forceChecks = false) {
  if(batchBoardState)return refreshBatchBoard();
  if (!P || openingProjectId || projectUnavailable(P.id)) return;
  const pid = P.id, generation = projectGeneration, responseContext = {revision:projectResponseRevisions.get(pid) || 0};
  try {
    const before = S?.tasks?.length ? replacementCheckInput().key : '';
    const next = await api("GET", `projects/${pid}`);
    if (!projectViewCurrent(pid, generation)) return;
    if (!await mergeConcurrentProject(pid, next, responseContext, () => projectViewCurrent(pid, generation)) || !projectViewCurrent(pid, generation)) return;
    renderAll();
    if (forceChecks || S?.tasks?.length && before !== replacementCheckInput().key) scheduleReplacementCheck(true);
  } catch (e) { /* keep last view */ }
  if (projectViewCurrent(pid, generation)) schedulePoll();
}
function busy() {
  const jobs = Object.values(P.jobs || {}).some((j) => j.state === "running");
  const runs = Object.values(P.runs || {}).flat().some((r) => ["submitting", "running"].includes(r.state));
  const imgs = Object.values(P.images || {}).some((r) => r.state === "running");
  return jobs || runs || imgs || P.analysis?.status === "running";
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if(batchBoardState){
    const state=batchBoardState;
    if(state.busy || fissionCurrent() && fissionState.open)return;
    pollTimer=setTimeout(()=>{if(batchBoardState===state)return refreshBatchBoard();},state.data?.active?3000:15000);return;
  }
  if (!P || openingProjectId || projectUnavailable(P.id)) return;
  const pid = P.id, generation = projectGeneration;
  pollTimer = setTimeout(() => { if (projectViewCurrent(pid, generation)) return refresh(); }, busy() ? 3000 : 15000);
}

// ---------- render ----------
function renderAll() {
  preserveView(() => {
    const name = $("#projectName");
    if ((name.dataset.project !== P.id || document.activeElement !== name) && name.textContent !== P.name) name.textContent = P.name;
    name.dataset.project = P.id;name.title=P.name || '';
    renderFissionBanner();
    renderLegacyNotice();
    renderSteps();
    renderSource();
    renderAnalysis();
    renderAssets();
    renderReplacements();
    renderScript();
    renderTasks();
    renderFinal();
    renderLog();
    updateImageWaits();
  });
}

function renderSteps() {
  renderWorkflow();
  renderCompletedSteps();
  renderSegmentPlan();
  renderComparison();
  renderNewStoryboards();
  renderDirector();
  renderDirectorGeneration();
  renderFinalPreview();
  renderFission();
  resumeActiveFixes();
}

function renderSource() {
  const v = $("#sourceView");
  if (!P.source) { updateHtml(v, ""); return; }
  const p = P.source.probe || {};
  updateHtml(v, `<video src="${fileUrl(P.source.file)}" controls preload="metadata"></video>
    <dl class="kv">
      <dt>文件</dt><dd>${esc(P.source.name)}</dd>
      <dt>时长</dt><dd>${fmt(p.duration)} 秒</dd>
      <dt>分辨率</dt><dd>${p.width}×${p.height}（按 ${esc(p.ratio_hint)} 出片）</dd>
      <dt>帧率</dt><dd>${fmt(p.fps, 2)} fps</dd>
      <dt>音轨</dt><dd>${p.audio_stream_count} 条 ${esc(p.audio_codec || "")}</dd>
      <dt>大小</dt><dd>${fmt(p.size_bytes / 1048576)} MB</dd>
      <dt></dt><dd><button class="danger" id="clearSourceBtn">清除这个视频</button>
        <div class="hint">清除后可以重新上传别的视频；素材库里的人物图、商品图会保留。</div></dd>
    </dl>`);
}

function renderAnalysis() {
  const a = P.analysis || {};
  const sel = $("#analyzeProvider");
  renderProviderSelect(sel,'analysis');
  updateSendVideo();
  $("#analyzeBtn").disabled = !P.source || a.status === "running" || P.jobs?.analyze?.state === "running";
  $("#analyzeBtn").textContent = a.status === "running" ? "拆解中…" : P.breakdown ? "用所选模型重新拆解" : "开始拆解";
  const order = ["probe", "transcribe", "boundaries", "overview", "close", "understand", "pack", "storyboard"];
  const steps = a.steps || {};
  const errMsg = operationErrors.get(operationKey("analyze")) || (a.status === "failed" ? (a.error || P.jobs?.analyze?.error) : (["error","failed"].includes(P.jobs?.analyze?.state) ? P.jobs.analyze.error : ""));
  const err = errMsg ? `<div class="runstep"><span class="st skipped">拆解没有完成：${errorHtml(errMsg,'拆解推理')}</span>${thirdRetry("analyze")}<div class="hint">修正后直接点“开始拆解”重新跑；需要换视频就在第 01 步清除。</div></div>` : "";
  updateHtml($("#analysisSteps"), order.filter((k) => steps[k]).map((k) => {
    const s = steps[k];
    const out = s.output ? JSON.stringify(s.output) : s.note || "";
    return `<div class="runstep"><div class="top"><div><b>${esc(publicText(s.title))}</b><div class="tool">${esc(publicText(s.tool || ""))}</div></div>
      <span class="st ${s.status}">${{ done: "done · 100%", running: `运行中…${s.progress ? " " + esc(s.progress) : ""}`, skipped: "已跳过", failed: "失败", partial: "部分完成，请重新拆解重试" }[s.status] || esc(s.status)}</span></div>
      ${out ? `<pre>${esc(publicText(out))}</pre>` : ""}</div>`;
  }).join("") + err);
  const frames = a.shots || [];
  const docs = Object.entries(a.docs || {});
  const passages = a.passages || [];
  const sigA = JSON.stringify([P.id, frames, docs, passages, a.status]);
  if ($("#framesStrip").dataset.sig !== sigA) {
    $("#framesStrip").dataset.sig = sigA;
    updateHtml($("#framesStrip"), `<div class="frames">${frames.map((s) =>
      `<figure><img loading="lazy" src="${fileUrl(s.frame)}"><figcaption>#${s.index} ${fmt(s.start)}–${fmt(s.end)}s</figcaption></figure>`).join("")}</div>
      ${docs.length ? `<div class="row wrap docs"><b>拆解文档</b>${docs.map(([n, f]) => `<a href="#" data-doc="${esc(f)}">${esc(n)}</a> <a class="hint" href="${fileUrl(f)}" download>下载</a>`).join(" · ")}</div><pre class="docview" hidden></pre>` : ""}
      ${passages.length ? `<details data-detail-key="analysis-passages"><summary>逐段分析详情（${passages.length} 段，含台词和截图）</summary>${passages.map((p) =>
        `<div class="passage"><b>${esc(p.id)} ${fmt(p.start, 2)}–${fmt(p.end, 2)}s · ${esc(p.phase || "")}</b> <span class="hint">${esc(p.summary || "")}</span>
         <div class="frames">${(p.grids || []).map((g) => `<a href="${fileUrl(g)}" target="_blank"><img loading="lazy" src="${fileUrl(g)}"></a>`).join("")}</div></div>`).join("")}</details>` : ""}`);
  }
  const vers = P.breakdown_versions || {};
  updateHtml($("#breakdownVersions"), Object.entries(vers).map(([k, v]) => {
    const u = v.usage || {};
    return `<div class="version ${P.breakdown_provider === k ? "current" : ""}"><b>${esc(providerLabel(v.provider || k,STATUS.providers[v.provider || k]))}</b>${v.input ? `（${esc(publicText(v.input))}）` : ""} · ${esc(publicText(v.model))}<br>
      ${fmt(v.seconds)} 秒 · ${tokenUsageText(u)} · ${esc(v.at)}
      ${P.breakdown_provider === k ? " · 当前使用" : ` <button class="small ghost" data-selver="${k}">用这一版</button>`}</div>`;
  }).join(""));
  const key = JSON.stringify(P.breakdown || null);
  if (key !== seen.breakdown && !dirty.breakdown) { seen.breakdown = key; renderCards(); }
  renderStoryboard();
  renderNewStoryboards();
}

function cell(v) {
  if (Array.isArray(v)) return esc(v.map((x) => (Array.isArray(x) ? x.join("–") : typeof x === "object" ? JSON.stringify(x) : x)).join("、"));
  if (v && typeof v === "object") return esc(JSON.stringify(v));
  return esc(v);
}
function table(rows, cols) {
  if (!rows?.length) return `<p class="hint">（空）</p>`;
  return `<table><tr>${cols.map(([, h]) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) =>
    `<tr>${cols.map(([k]) => `<td>${k === "time" ? `${fmt(r.start)}–${fmt(r.end)}` : cell(r[k])}</td>`).join("")}</tr>`).join("")}</table>`;
}
function renderCards() {
  const b = P.breakdown;
  const box = $("#breakdownCards");
  if (!b) { updateHtml(box, ""); return; }
  const o = b.overview || {};
  const v = b.viralLogic || {};
  const views = {
    characters: table(b.characters, [["id", "ID"], ["name", "名称"], ["role", "身份"], ["appearance", "外观"], ["outfit", "服装"]]),
    scene: table(b.scene, [["id", "ID"], ["name", "场景"], ["description", "描述"], ["lighting", "光线"]]),
    beats: table(b.beats, [["time", "时间"], ["function", "功能"], ["summary", "内容"], ["performance", "表演"]]),
    shots: table(b.shots, [["time", "时间"], ["shotSize", "景别"], ["camera", "运镜"], ["action", "动作"], ["product", "商品"]]),
    productMoments: table(b.productMoments, [["time", "时间"], ["product", "商品"], ["presentation", "呈现"], ["sellingPoint", "卖点"]]),
    systems: table(b.systems, [["name", "系统"], ["kind", "类型"], ["description", "内容/位置/样式/进出"]]),
    overlays: table(b.overlays, [["time", "时间"], ["kind", "类型"], ["text", "文字"], ["position", "位置"], ["style", "样式"], ["enter", "进入"], ["anchor", "挂在"]]),
    dialogue: `${b.speech_lines_state?.warning?`<p class="caption-warning" role="status">${esc(b.speech_lines_state.warning)}</p>`:''}${table(b.speech_lines?.length?b.speech_lines:b.dialogue, [["time", "时间"], ["speaker", "说话人"], ["text", "台词"]])}<details data-detail-key="speech-raw"><summary>原转写句子</summary>${table(b.dialogue, [["time", "时间"], ["speaker", "说话人"], ["text", "台词"]])}</details>`,
    viralLogic: `<p><b>钩子：</b>${esc(v.hook)}</p><p><b>结构：</b>${esc(v.structure)}</p>
      <b>为什么能火</b><ul>${(v.whyItWorks || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <b>复刻要保留</b><ul>${(v.replicateTips || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <b>风险</b><ul>${(v.risks || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`,
  };
  updateHtml(box, `<div class="card wide"><h4>总览</h4><p>${esc(o.oneLine)}</p><p class="hint">${esc(o.genre)} · ${esc(o.visualTone)} · ${esc(o.language)}</p></div>` +
    Object.keys(CARD_TITLES).map((k) => `<div class="card ${["shots", "dialogue", "overlays", "beats", "systems"].includes(k) ? "wide" : ""}" data-card="${k}">
      <h4>${CARD_TITLES[k]} ${k==='dialogue'?'':`<button class="small ghost" data-editcard="${k}">编辑</button>`}</h4>
      <div class="body">${views[k]}</div></div>`).join(""));
}
function editCard(k) {
  if(k==='dialogue')return;
  const card = $(`[data-card="${k}"]`);
  dirty.breakdown = true;
  renderedHtml.delete($("#breakdownCards"));
  card.querySelector(".body").innerHTML = `<textarea>${esc(JSON.stringify(P.breakdown[k], null, 2))}</textarea>
    <div class="row end"><button class="small ghost" data-cancelcard>取消</button><button class="small" data-savecard="${k}">保存这张卡</button></div>`;
}
async function saveCard(k) {
  const ta = $(`[data-card="${k}"] textarea`);
  let val;
  try { val = JSON.parse(ta.value); } catch (e) { return toast("JSON 格式不对：" + e.message, true); }
  const b = clone(P.breakdown); b[k] = val;
  P = { ...P, ...(await guard(() => api("PUT", `projects/${P.id}/breakdown`, { breakdown: b }), "已保存")) };
  dirty.breakdown = false; seen.breakdown = ""; renderAnalysis(); scheduleReplacementCheck(true);
}

// ---------- assets / replacements ----------
function renderAssets() {
  const key = JSON.stringify(P.assets);
  if (key === seen.assets) return;
  seen.assets = key;
  const roleOpts = (a) => Object.entries(ROLE_LABELS).filter(([k]) => (a.type === "audio") === (k === "voice_identity"))
    .map(([k, l]) => `<option value="${k}" ${a.roles?.[0] === k ? "selected" : ""}>${l}</option>`).join("");
  const card = (a) => `<div class="asset" data-asset="${a.id}">
      ${a.type === "image" ? `<img src="${fileUrl(a.file)}">` : `<audio controls src="${fileUrl(a.file)}"></audio><span class="tag">${fmt(a.probe?.duration)} 秒</span>`}
      <input data-f="label" value="${esc(a.label)}" title="${esc(a.label)}" placeholder="名称">
      <select data-f="roles">${roleOpts(a)}</select>
      <textarea data-f="description" rows="2" placeholder="${a.type === "audio" ? "声线描述，如：自然男声，25 岁左右" : "外观说明，如：28 岁男性，黑色西装，短发"}">${esc(a.description)}</textarea>
      <div class="row" style="margin:0;justify-content:space-between"><span class="tag">${a.generated ? "模型生成" : "上传"}</span><button class="small ghost" data-delasset="${a.id}">删除</button></div>
    </div>`;
  // Generated images pile up with every storyboard version; keep the user's own photos first.
  const visible = (P.assets || []).filter(a => !internalAsset(a)), own = visible.filter(a => !a.generated), made = visible.filter(a => a.generated);
  updateHtml($("#assetGrid"), own.map(card).join("") + (made.length ? `<details class="generated-assets" data-detail-key="generated-assets"><summary>模型生成的图（${made.length} 张）· 生成分镜时自动存的，一般不用动</summary><div class="assets">${made.map(card).join("")}</div></details>` : "") || `<p class="hint">还没有素材。</p>`);
}
async function patchAsset(el) {
  const box = el.closest("[data-asset]");
  const body = { label: $('[data-f="label"]', box).value, description: $('[data-f="description"]', box).value, roles: [$('[data-f="roles"]', box).value] };
  const np = await guard(() => api("PATCH", `projects/${P.id}/assets/${box.dataset.asset}`, body));
  P = { ...P, ...np }; seen.assets = JSON.stringify(P.assets);
  renderReplacements(true); renderTasks(true); scheduleReplacementCheck(true);
}

function assetOptions(type, selected, roleFilter) {
  return `<option value="">— 选择素材 —</option>` + (P.assets || []).filter((a) => a.type === type && (!roleFilter || roleFilter.includes(a.roles?.[0])))
    .map((a) => `<option value="${a.id}" title="${esc(a.label)}" ${a.id === selected ? "selected" : ""}>${esc(a.label)}</option>`).join("");
}
const cameraDrafts = new Map();
const cameraDraftIds = new WeakMap();
let cameraDraftSequence = 0;
function cameraEditorId(draft, rec) {
  if (!draft) return rec?.id;
  // Draft responses have no server id; keep a stable identity for each response.
  if (!cameraDraftIds.has(draft)) cameraDraftIds.set(draft, `draft-${++cameraDraftSequence}`);
  return cameraDraftIds.get(draft);
}
function cameraEditor(cid, sid, recordId) {
  const card = $$('[data-camera-key]').find(el => el.dataset.cameraKey === `${cid}:${sid}`);
  return card && $$('[data-camera-editor]', card).find(el => el.dataset.cameraRecord === recordId);
}
const cameraBusy = new Set();
const cameraFrameSelections = new Map();
const cameraDraftKey = (cid, sid) => `${P.id}:${cid}:${sid}`;
function cameraRecords(cid, sid) {
  if(cid==='scene' || sceneEditorEnabled())return sceneCameraRecords(sid);
  const legacyScene = firstCharacterScene(cid);
  return Object.values(P.images || {}).filter(r => r.for_task === `camera:${cid}:${sid}` || (sid === legacyScene && r.for_task === `camera:${cid}`));
}
function cameraAssetStale(cid, sid, aid) {
  if (!aid) return false;
  if (P.camera_stale_assets?.includes(aid)) return true;
  return Object.values(P.replacement_checks || {}).some(check => (check.issues || []).some(issue => issue.code === 'camera_image_stale'
    && issue.character_id === cid && issue.scene_id === sid && issue.asset_id === aid));
}
function cameraRecordStale(rec) {
  if (!rec) return false;
  if (P.camera_stale_images?.includes(rec.id)) return true;
  if (rec.anchor_asset_id) {
    const [,cid,sid] = rec.for_task.split(':');
    return !cameraScenes().some(s => s.id !== sid && cameraImageFor(cid,s.id) === rec.anchor_asset_id);
  }
  return false;
}
function cameraSourceFrame(rec, draft) {
  const owner = draft && !draft.error ? draft : rec, ids = owner?.image_ids || [];
  const lastPath = owner?.preview?.images?.at(-1)?.path;
  const input = P.assets?.find(a => a.id === (owner?.frame_asset_id || ids.at(-1)))
    || (lastPath && P.assets?.find(a => lastPath.endsWith('/' + a.file) || lastPath === a.file));
  return input?.derived_from ? P.assets?.find(a => a.id === input.derived_from) : input;
}
function cameraSourceFile(cid, scene, rec, draft) {
  const frame = cameraSourceFrame(rec,draft);
  if (frame?.file && !P.missing_asset_files?.includes(frame.id)) return frame.file;
  // An unrelated analysis thumbnail cannot establish the current frame's identity.
  return '';
}
function cameraThumbnail(aid, label) {
  const asset = P.assets?.find(a => a.id === aid);
  return asset?.file ? `<figure><button class="ghost" data-camera-zoom="${esc(asset.file)}" data-camera-caption="${esc(label)}"><img src="${fileUrl(asset.file)}" alt="${esc(label)}"></button><figcaption>${esc(label)}</figcaption></figure>` : '';
}
function cameraCaptionInfo(rec, draft) {
  const owner = draft || rec || {};
  const ids = owner.image_ids || [];
  const asset = P.assets?.find(a => a.id === (owner.clean_frame_asset_id || ids.at(-1)));
  const clean = asset?.derived_from ? asset : null;
  const overlap = owner.caption_overlap_warning || clean?.caption_overlap_warning;
  const residue = owner.caption_residue || clean?.caption_residue;
  const warning = owner.caption_warning;
  return `${clean?.file ? `<p class="hint" data-clean-frame>已去除字幕后交给生图模型 · <button class="small ghost" data-camera-zoom="${esc(clean.file)}" data-camera-caption="去字幕参考帧">查看去字幕版本</button></p>` : ''}
    ${overlap ? `<p class="caption-warning" data-caption-overlap>${esc(overlap)}</p>` : ''}
    ${residue?.suspected?.length ? '<p class="caption-warning" data-caption-residue>去字幕参考帧仍有疑似字幕残留，生成后请检查这一块</p>' : ''}
    ${residue?.status === 'unavailable' ? `<p class="caption-warning" data-caption-residue>${esc(residue.reason || '字幕残留检查未完成，请人工检查')}</p>` : ''}
    ${warning ? `<p class="caption-warning">${esc(warning)}</p>` : ''}`;
}
function cameraFrameInfo(cid, scene, rec, draft, selected) {
  const owner = draft && !draft.error ? draft : rec;
  const frame = cameraSourceFrame(rec,draft);
  const meta = (draft?.error ? null : draft) || selected || {};
  const at = meta.frame_at ?? frame?.frame_at ?? owner?.frame_at ?? owner?.preview?.frame_at ?? null;
  const shots = (P.breakdown?.shots || []).filter(r => (r.characters || []).includes(cid) && (!scene.timeRanges?.length || scene.timeRanges.some(([a,b]) => Number(r.end) > a && Number(r.start) < b)));
  const first = shots.length ? Math.min(...shots.map(r => Number(r.start))) : Number(scene.timeRanges?.[0]?.[0] || 0);
  const legacy = at !== null && (Math.abs(Number(at) - 0.5) < 0.001 || Math.abs(Number(at) - first - 0.5) < 0.001);
  const reason = meta.frame_reason_code || meta.frame_reason || owner?.frame_reason_code || owner?.frame_reason || owner?.preview?.frame_reason_code || owner?.preview?.frame_reason || frame?.frame_reason_code || frame?.frame_reason || (legacy ? 'legacy_first_appearance' : '');
  const labels = {front_product:'正面、商品清楚',product_moment:'商品展示时刻',legacy_first_appearance:'旧规则：人物第一次出现后 0.5 秒',manual:'手动选择'};
  const belongsToCharacter = r => !r.characters?.length || r.characters.includes(cid);
  const rows = [...shots, ...(P.breakdown?.source_motion_timeline || []), ...(P.script?.tasks || []).filter(belongsToCharacter).flatMap(t => [...(t.source_motion_timeline || []), ...(P.replacement_checks?.[t.id]?.source_motion_timeline || [])].map(r => ({...r, start:Number(r.start) + Number(t.source_start || 0), end:Number(r.end) + Number(t.source_start || 0)})))].filter(belongsToCharacter);
  const back = ['legacy_first_appearance','manual'].includes(reason) && rows.some(r => at !== null && Number(r.start) <= at && Number(r.end) > at && /背对|背面|背影|back[- ]?(?:view|facing)|facing away|from behind/i.test([r.action,r.description,r.subject,r.composition].filter(Boolean).join(' ')));
  const recordedWarning = meta.frame_warning ?? owner?.frame_warning ?? frame?.frame_warning ?? '';
  const warning = back || /背影|背对|背面|back[- ]?(?:view|facing)|facing away|from behind/i.test(recordedWarning)
    ? '这一帧是背影，看不到脸。请在下面换一帧，或点“自动选帧”，然后重新生成预览' : recordedWarning;

  return {at, reason, label:labels[reason] || reason || (at === null ? '尚未选帧' : '未记录截帧原因'), warning};
}
function cameraAnalysisFrames() {
  const standalone = P.analysis?.frames?.length ? P.analysis.frames : null;
  return (standalone || P.analysis?.shots || []).map(f => ({
    // A shot thumbnail is extracted at its midpoint, even in older projects
    // without frame_at. Keep this precision in the selection/request value.
    at:Number(f.frame_at ?? (standalone ? f.at ?? f.time ?? f.start : (Number(f.start) + Number(f.end)) / 2)),
    file:f.file || f.frame || f.path,
  })).filter(f => Number.isFinite(f.at) && f.file);
}
function cameraFrameChanged(cid, sid, frameInfo) {
  const key = cameraDraftKey(cid,sid);
  if (!cameraFrameSelections.has(key)) return false;
  if (!frameInfo) {
    const scene = cameraScenes().find(s => s.id === sid) || {id:sid};
    frameInfo = cameraFrameInfo(cid,scene,cameraRecords(cid,sid).at(-1),cameraDrafts.get(key));
  }
  const selection = cameraFrameSelections.get(key);
  return selection === 'auto' ? frameInfo.reason === 'manual' : frameInfo.at === null || Number(selection) !== Number(frameInfo.at);
}
function cameraConfirmReason(cid, sid, rec = cameraRecords(cid,sid).at(-1)) {
  if(cid==='scene' || sceneEditorEnabled())return sceneConfirmReason(sid,rec);
  const key = cameraDraftKey(cid,sid);
  if (cameraBusy.has(key) || rec?.state !== 'pending_confirmation') return '先完成 ② 生成预览';
  if (cameraFrameChanged(cid,sid)) return '帧已更换，请先重新生成预览';
  if (cameraRouteSelected.has(key) && imageChannel(cid,sid,rec) !== (rec.channel || 'third_party')) return '渠道已切换，请先重新生成预览';
  if (cameraRecordStale(rec)) return '替换设定变了，请先重新生成预览';
  if (cameraDrafts.has(key)) return '先完成 ② 生成预览';
  return '';
}
function cameraVersionTime(asset, record) {
  const value = asset?.generated_at || record?.completed_at || record?.started_at || asset?.created_at || record?.created_at;
  if (!value) return 0;
  return (typeof value === 'number' ? value * (value < 1e12 ? 1000 : 1) : Date.parse(value)) || 0;
}
function cameraVersionsHtml(cid, sid, aid, recs) {
  const ids = [...new Set([...recs.filter(r => r.state === 'completed').map(r => r.asset_id), aid].filter(validCameraAsset))];
  const versions = ids.map((id, index) => {
    const asset = P.assets.find(a => a.id === id), record = recs.find(r => r.asset_id === id);
    return {id, asset, record, index, time:cameraVersionTime(asset,record)};
  }).sort((a,b) => b.time-a.time || b.index-a.index);
  return versions.length ? `<div class="camera-versions">${versions.map(({id,asset,record,time}) => {
    const at = asset.frame_at ?? record?.frame_at, date = time ? new Date(time) : null;
    const pad = n => String(n).padStart(2,'0');
    const short = date ? `${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}` : '';
    return `<figure class="camera-version" data-camera-version="${esc(id)}"><button class="ghost" data-camera-zoom="${esc(asset.file)}" data-camera-caption="${esc(asset.label || id)}"><img src="${fileUrl(asset.file)}" alt="${esc(asset.label || id)}" loading="lazy"></button>
      <figcaption>${materialNameHtml(asset.label || id)}${imageModelAnnotationHtml(record)}${at != null ? `<span class="hint">${fmt(at)} 秒</span>` : ''}${short ? `<time class="hint">${esc(short)}</time>` : ''}
      ${id === aid ? '<b class="tag camera-in-use">在用</b>' : `<button class="small ghost" data-camera-use="${esc(id)}" data-character="${esc(cid)}" data-scene="${esc(sid)}">改用这张</button>`}</figcaption></figure>`;
  }).join('')}</div>` : '<p class="hint">还没有生成好的图</p>';
}
async function useSceneCamera(cid, sid, assetId) {
  const pid = P.id, affected=(P.script?.tasks || []).filter(t=>{
    const reference=taskCameraReference(t);return reference?.cid===cid && reference.sid===sid;
  }).map(t=>t.id);
  return withScriptSave(`[data-camera-use="${assetId}"][data-character="${cid}"][data-scene="${sid}"]`,async()=>{
    const np = await api('POST',`projects/${pid}/camera-image/use`,{character_id:cid,scene_id:sid,asset_id:assetId});
    if (P.id !== pid) return;
    P = {...P,...np};
    renderScript(); renderTasks(true); renderSteps(); renderLog();
    toast(np.camera_selection.message);
    scheduleReplacementCheck(true);
    if (P?.id === pid) $$('[data-camera-key]').forEach(renderCameraFrameSelection);
  },latest=>latest.replacements?.characters?.find(c=>c.source_id===cid)?.camera_assets?.[sid]===assetId
    && affected.every(id=>latest.script?.tasks?.find(t=>t.id===id)?.image_ids?.[0]===assetId));
}
function sceneCameraCard(cid, scene) {
  const sid = scene.id, key = cameraDraftKey(cid, sid), draft = cameraDrafts.get(key);
  const validDraft = draft && !draft.error ? draft : null;
  const recs = cameraRecords(cid, sid), rec = recs.at(-1), aid = cameraImageFor(cid, sid);
  const editorId = cameraEditorId(draft, rec);
  const selected = P.assets?.find(a => a.id === aid);
  const channel = imageChannel(cid,sid,rec);
  const channelChanged = rec?.state === 'pending_confirmation' && cameraRouteSelected.has(key) && channel !== (rec.channel || 'third_party');
  const active = ['pending_confirmation','running'].includes(rec?.state);
  const displayed = !validDraft && !active && aid ? recs.find(r=>r.asset_id === aid) || rec : rec;
  const source = cameraSourceFile(cid, scene, displayed, validDraft);
  const frameInfo = cameraFrameInfo(cid, scene, displayed, validDraft, active || validDraft ? null : selected);
  const frameChanged = cameraFrameChanged(cid,sid,frameInfo), frameSelection = cameraFrameSelections.get(key);
  const confirmReason = cameraConfirmReason(cid,sid,rec);
  const removed = rec?.state === 'completed' && !P.assets?.some(a => a.id === rec.asset_id);
  const stale = channelChanged || (!removed && (cameraRecordStale(rec) || (!active && (cameraAssetStale(cid,sid,aid) || (!aid && recs.some(r=>r.state === 'completed'))))));
  const completed = !!aid && !cameraBusy.has(key) && !draft && !frameChanged && !stale && !cameraAssetStale(cid,sid,aid)
    && !['pending_confirmation','running'].includes(rec?.state);
  const state = completed ? '✓ 已完成，视频会用右边这张' : cameraBusy.has(key) ? '正在起草…' : draft?.error ? '失败' : draft ? '待确认' : removed ? '已从素材库移除' : stale ? '已过期（替换设定变了）' : rec?.state === 'running' ? '生成中' : rec?.state === 'query_pending' ? '等待查询' : rec?.state === 'failed' ? '失败' : rec?.state === 'pending_confirmation' ? '待确认' : frameChanged ? '已换帧，待重新生成预览' : aid ? '已生成' : '未起草';
  const captionInfo = cameraCaptionInfo(displayed, validDraft);
  const tasks = (P.script?.tasks || []).filter(t => taskScene(t) === sid || (t.scene_references || []).some(r => r.scene_id === sid));
  const validPreview = !cameraDrafts.has(key) && !stale && !frameChanged && rec?.state === 'pending_confirmation';
  let currentStep = frameChanged || cameraFrameSelections.has(key) || frameInfo.at !== null || draft || stale ? 2 : 1;
  if (validPreview || (['running','failed'].includes(rec?.state) && !draft && !stale && !frameChanged)) currentStep = 3;
  if (completed) currentStep = 0;
  const frameTime = value => fmt(value);
  const frames = cameraAnalysisFrames().filter(f => !scene.timeRanges?.length || scene.timeRanges.some(([a,b]) => f.at >= a && f.at < b));
  // Identify the current picture by both its time and original asset, rather
  // than marking a different thumbnail at the same time as the current frame.
  const currentAt = frameInfo.at === null ? null : Number(frameInfo.at);
  if (source && !frames.some(f => f.at === currentAt && f.file === source)) {
    frames.unshift({at:currentAt,file:source});
  }
  if (frameChanged && frameSelection !== 'auto' && !frames.some(f => f.at === frameSelection)) {
    const cached = P.assets?.find(a => !a.derived_from && a.file && !P.missing_asset_files?.includes(a.id) && a.frame_at != null && Number(a.frame_at) === frameSelection
      && (!a.frame_character || a.frame_character === cid) && (!a.camera_scene || a.camera_scene === sid)
      && (a.frame_character || a.roles?.includes('scene') || (a.label || '').startsWith('原片参考帧') || recs.some(r => r.frame_asset_id === a.id)));
    if (cached) frames.push({at:frameSelection,file:cached.file});
  }
  const selectedFrameVisible = frames.some(f => f.at === frameSelection);
  const inputAt = frameSelection === 'auto' ? '' : frameSelection ?? frameInfo.at ?? '';
  const editedPreviewButton = `<button class="small ghost" data-campreview="${esc(cid)}" data-scene="${esc(sid)}" data-camera-record="${esc(editorId)}" ${frameChanged ? 'disabled' : ''}>按修改后的提示词重新生成预览</button>`;
  const sourceTitle = `原片参考帧（${frameInfo.at === null ? '时间未记录' : frameTime(frameInfo.at) + ' 秒'}，生成主镜头图时只参考它的机位和构图）`;
  const pair = (file, title) => `<figure><figcaption>${esc(title)}</figcaption>${file ? `<button class="camera-image-button" data-camera-zoom="${esc(file)}" data-camera-caption="${esc(cid + ' · ' + sid + ' ' + scene.name + ' · ' + title)}"><img src="${fileUrl(file)}" alt="${esc(title)}" width="240"></button>` : `<div class="camera-placeholder">${title.startsWith('原片参考帧') ? (displayed || validDraft ? '原片参考帧已缺失，请重新生成预览' : '起草后显示原片参考帧') : '还没有在用的图'}</div>`}</figure>`;
  return `<article class="scene-camera" data-camera-key="${esc(cid + ':' + sid)}" data-scene="${esc(sid)}">
    <h4>${esc(sid)} ${esc(scene.name || '原片场景')} <span class="hint">${(scene.timeRanges || []).map(([a,b]) => `${fmt(a,2)}–${fmt(b,2)} 秒`).join('、')}</span></h4>
    <p class="camera-state${completed ? ' completed' : ''}" role="status">${state}${rec?.state === 'running' ? ` <span data-image-wait="${esc(rec.id)}"></span>` : ''}</p>
    <p class="hint">${tasks.length ? '用于 ' + tasks.map(t => esc(t.id)).join('、') : '生成脚本后自动匹配分段'}</p>
    <div class="camera-pair">${pair(source,sourceTitle)}${pair(selected?.file,'在用（视频会用这张）')}</div>
    <section class="camera-step ${currentStep === 1 ? 'is-current' : ''}" data-camera-step="1">
      <h5>① 选帧</h5>
      <p class="hint" data-frame-reason="${esc(frameInfo.reason)}">${completed ? (frameInfo.at === null ? '在用这张图的原片截帧时间未记录' : `在用这张图截自原片 ${frameTime(frameInfo.at)} 秒`) : frameInfo.at === null ? (source ? '当前预览用的帧：时间未记录' : '尚未选帧，生成预览时会自动挑“正面、商品清楚”的一帧') : `当前预览用的帧：${frameTime(frameInfo.at)} 秒（${esc(frameInfo.label)}）`}</p>
      ${frameInfo.warning ? `<p class="caption-warning" data-frame-warning>${esc(frameInfo.warning)}</p>` : ''}
      <div class="frames camera-frames">${frames.map(f => {
        const at = f.at, current = at === currentAt && f.file === source, picked = at !== null && frameChanged && frameSelection !== 'auto' && at === frameSelection;
        const label = at === null ? '时间未记录' : `${frameTime(at)} 秒`;
        return `<button class="ghost ${current ? 'frame-current' : ''} ${picked ? 'frame-selected' : ''}" ${at === null ? 'disabled' : `data-camera-frame="${esc(at)}"`} aria-pressed="${picked}" aria-label="${at === null ? '' : '选择 '}${esc(label)}"><img src="${fileUrl(f.file)}" alt="${esc(label)}"><span>${esc(label)}</span>${current ? '<b>当前</b>' : ''}${picked ? '<b>新选</b>' : ''}</button>`;
      }).join('')}</div>
      <div class="row wrap"><label>截帧时间（秒）<input type="number" min="0" step="0.01" data-camera-at value="${inputAt===''?'':fmt(inputAt)}" ${frameSelection === 'auto' ? 'placeholder="自动选帧"' : ''}></label>
        <button class="small ghost" data-camera-auto="${esc(cid)}" data-scene="${esc(sid)}" aria-pressed="${frameSelection === 'auto'}">自动选帧</button></div>
      ${frameChanged ? `<p class="hint camera-frame-pending" role="status">已选${frameSelection === 'auto' ? '自动选帧' : ` ${esc(frameTime(frameSelection))} 秒`}，还没生效，请点 ② 重新生成预览${frameSelection !== 'auto' && !selectedFrameVisible ? '；生成预览后才能看到这一帧' : ''}</p>` : ''}
    </section>
    <section class="camera-step ${currentStep === 2 ? 'is-current' : ''}" data-camera-step="2">
      <h5>② 生成预览（不出图）</h5>
      ${!completed && (validDraft || rec?.state === 'pending_confirmation' || rec?.state === 'completed' && !aid) ? captionInfo : ''}
      <label>生图渠道 <select data-camera-channel="${esc(cid)}" data-scene="${esc(sid)}" ${cameraBusy.has(key) || rec?.state === 'running' ? 'disabled' : ''}><option value="" ${!cameraRouteSelected.has(key) ? 'selected' : ''}>${esc(defaultRouteLabel('camera_image'))}</option><option value="official" ${cameraRouteSelected.has(key) && channel === 'official' ? 'selected' : ''}>官方 · ${esc(STATUS?.openai_image_model || 'gpt-image-2')}</option><option value="third_party" ${cameraRouteSelected.has(key) && channel === 'third_party' ? 'selected' : ''}>三方渠道 · gpt-image-2</option></select></label>
      <span class="legacy-route" data-legacy-route="camera_image">${legacyRouteHtml('camera_image')}</span>
      ${channelChanged ? '<p class="hint">渠道已切换，请重新生成预览后确认生图</p>' : ''}
      ${productNoteWarning() ? `<p class="caption-warning">${PRODUCT_NOTE_WARNING}</p>` : ''}
      <p><button class="small" data-camdraft="${esc(cid)}" data-scene="${esc(sid)}" ${cameraBusy.has(key) ? 'disabled' : ''}>${completed || validPreview ? '重新生成预览' : '生成预览'}</button></p>
      ${draft?.error ? `<p class="hint">${esc(publicText(draft.error))}</p>${thirdRetry('camera',`data-character="${esc(cid)}" data-scene="${esc(sid)}"`)}` : ''}
      ${!cameraBusy.has(key) && draft?.prompt ? `<details data-detail-key="camera-draft" data-camera-editor data-camera-record="${esc(editorId)}"><summary>提示词（可编辑）</summary><p class="hint">${esc(draft.note || '')}</p><textarea data-cam="prompt" rows="8">${esc(draft.prompt)}</textarea>
        <div class="row wrap"><label>模型 <select data-cam="model">${(channel === 'official' ? [STATUS?.openai_image_model || 'gpt-image-2'] : IMAGE_MODELS).map(m=>`<option ${draft.model === m ? "selected" : ""}>${m}</option>`).join('')}</select></label><label>分辨率 <select data-cam="resolution">${["2k","1k","4k"].map(r=>`<option ${draft.resolution === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>${editedPreviewButton}</div></details>` : ''}
      ${!cameraBusy.has(key) && !draft && !stale && rec?.state === 'pending_confirmation' ? `<details data-detail-key="camera-prompt" data-camera-editor data-camera-record="${esc(editorId)}"><summary>提示词（可编辑）</summary><textarea data-cam="prompt" rows="8">${esc(rec.preview?.prompt)}</textarea>${editedPreviewButton}</details>` : ''}
    </section>
    <section class="camera-step ${currentStep === 3 ? 'is-current' : ''}" data-camera-step="3">
      <h5>③ 确认生图（付费）</h5>
      ${completed ? '<p class="hint">如需换一张，先在 ① 换帧或在 ② 重新生成预览</p>' : `<div class="row wrap"><button class="small pay" data-imgconfirm="${confirmReason ? '' : esc(rec.id)}" ${confirmReason ? 'disabled' : ''}>确认生图（${esc(imageFee(rec))}）</button>${confirmReason ? `<span class="hint" role="status">${confirmReason}</span>` : ''}</div>`}
      ${rec?.state === 'running' ? `<p class="hint">生成中 <span data-image-wait="${esc(rec.id)}"></span></p>` : ''}${queryRetryHtml(rec,true)}
      ${!draft && !stale && rec?.state === 'failed' ? `<p class="hint">${errorHtml(rec.error || '生成失败','生图')}</p>${imageRecovery(rec)}${rec.channel === 'official' ? thirdRetry('camera-image',`data-character="${esc(cid)}" data-scene="${esc(sid)}"`) : ''}` : ''}
      ${imageUsage(rec)}
    </section>
    <section class="camera-step camera-result" data-camera-result>
      <h5>已生成的版本</h5>
      ${cameraVersionsHtml(cid,sid,aid,recs)}
      ${P.camera_notice?.[`${cid}:${sid}`] ? `<p class="caption-warning" data-camera-notice role="status">${esc(P.camera_notice[`${cid}:${sid}`])}</p>` : ''}
      ${removed ? `<p class="hint">已从素材库移除</p><button class="small ghost" data-imgresume="${esc(rec.id)}">恢复</button>${rec.restore_error ? `<p class="hint">恢复未完成：${esc(publicText(rec.restore_error))}</p>` : ''}` : ''}
    </section>
    ${completed && captionInfo.trim() ? `<details data-detail-key="camera-generation"><summary>生成细节</summary>${captionInfo}</details>` : ''}
  </article>`;
}
function cameraGroupHtml(cid) {
  const scenes = cameraScenes(), c = (P.replacements?.characters || []).find(c => c.source_id === cid) || {};
  const generated = scenes.filter(s => cameraImageFor(cid, s.id)).length;
  const first = cameraImageFor(cid, scenes[0].id), referenceAnchor = first || scenes.map(s=>cameraImageFor(cid,s.id)).find(Boolean), anchor = first && !cameraAssetStale(cid, scenes[0].id, first);
  const remaining = scenes.slice(1).filter(s => !cameraImageFor(cid, s.id) || cameraAssetStale(cid,s.id,cameraImageFor(cid,s.id)));
  const pending = remaining.filter(s => !cameraConfirmReason(cid,s.id));
  return `<tr class="rep-camera" data-camera-person="${esc(cid)}"><td colspan="5"><h4>主镜头图</h4>
    <div class="camera-references">${cameraThumbnail(c.asset_id,'人物照')}${P.replacements?.product?.action === 'replace' ? cameraThumbnail(P.replacements.product.asset_id,'商品图') : ''}${scenes.length > 1 ? cameraThumbnail(referenceAnchor,first ? '身份锚点（首场景）' : '身份锚点') : ''}</div>
    ${scenes.length > 1 ? `<div class="row wrap"><span>共 ${scenes.length} 个场景，已生成 ${generated} 张</span>
      <button class="small ghost" data-camera-batch-draft="${esc(cid)}" ${!anchor || !remaining.length || cameraBusy.has(cameraDraftKey(cid,'batch')) ? 'disabled' : ''}>生成其余 ${remaining.length} 个场景的预览</button>
      <button class="small pay" data-camera-batch-confirm="${esc(cid)}" ${!anchor || !pending.length || cameraBusy.has(cameraDraftKey(cid,'batch')) ? 'disabled' : ''}>确认生成其余 ${pending.length || remaining.length} 张（${esc(imageBatchFee(pending.map(s=>cameraRecords(cid,s.id).at(-1))) || imageFee(null))}）</button></div>
      ${!anchor ? '<p class="hint">先生成并选用第一个场景的图，其余场景会以它为身份锚点，保证同一个人</p>' : ''}` : ''}
    <div class="scene-camera-list">${scenes.map(s => sceneCameraCard(cid,s)).join('')}</div></td></tr>`;
}
async function draftSceneCamera(cid, sid, preview = true) {
  const key = cameraDraftKey(cid,sid), pid = P.id;
  if (dirty.rep) await saveReplacements();
  if (P.id !== pid) return;
  const selection = cameraFrameSelections.get(key), at = selection === 'auto' ? undefined : selection;
  cameraChannels.set(key,imageChannel(cid,sid,cameraRecords(cid,sid).at(-1)));
  cameraBusy.add(key); cameraDrafts.delete(key); renderReplacements(true);
  try {
    const route=requestRoute('camera_prompt',$('#analyzeProvider'));
    const draft = await api('POST',`projects/${pid}/camera-image/draft`,{character_id:cid,scene_id:sid,at,...route});
    if (P.id !== pid) return;
    finishTemporaryProvider('camera_prompt',route);
    cameraDrafts.set(key,draft);
    if (cameraFrameSelections.get(key) === selection) cameraFrameSelections.delete(key);
    const updated = await api('GET',`projects/${pid}`);
    if (P.id !== pid) return;
    P = updated; renderFinal();
    if (preview) await previewSceneCamera(cid,sid,{draft});
    if (P.id === pid) renderAssets();
  } catch(error) { if(P.id === pid) cameraDrafts.set(key,{error:publicText(error.message)}); throw error; }
  finally { cameraBusy.delete(key); if (P.id === pid) renderReplacements(true); }
}
async function previewSceneCamera(cid,sid, options = {}) {
  const key = cameraDraftKey(cid,sid), rec = cameraRecords(cid,sid).at(-1), pid = P.id;
  const currentDraft = cameraDrafts.get(key);
  const draft = options.draft || currentDraft || (rec && {...rec.preview, image_ids:rec.image_ids, for_task:rec.for_task, camera_binding:rec.camera_binding, frame_reason_code:rec.frame_reason_code || rec.frame_reason});
  if (!draft?.prompt) return;
  let editor;
  if (options.recordId !== undefined) {
    if (cameraBusy.has(key) || cameraFrameChanged(cid,sid) || options.recordId !== cameraEditorId(currentDraft,rec)) return;
    editor = cameraEditor(cid,sid,options.recordId);
    if (!editor) return;
  }
  // Only an explicit edit action can read DOM values. Automatic previews use
  // this draft response for both the prompt and its reference images.
  const value = field => editor ? $(`[data-cam="${field}"]`,editor)?.value : undefined;
  const channel=imageChannel(cid,sid,rec);
  const body = {channel,route_selected:cameraRouteSelected.has(key),prompt:value('prompt') ?? draft.prompt, model:channel === 'official' ? (routeFor('camera_image').channel==='official' && routeFor('camera_image').model) || STATUS?.openai_image_model || 'gpt-image-2' : value('model') || draft.model || 'gpt-image-2',
    resolution:value('resolution') || draft.resolution || '2k',image_ids:draft.image_ids,ratio:draft.ratio,for_task:draft.for_task,camera_binding:draft.camera_binding,frame_reason_code:draft.frame_reason_code || draft.frame_reason};
  await api('POST',`projects/${pid}/images/preview`,body);
  cameraDrafts.delete(key);
  if (P.id !== pid) return;
  const updated = await api('GET',`projects/${pid}`);
  if (P.id !== pid) return;
  P = updated; renderReplacements(true); renderFinal();
}
async function batchSceneCameras(cid, confirmImages) {
  const pid = P.id, busyKey = cameraDraftKey(cid,'batch');
  if (dirty.rep) await saveReplacements();
  if (P.id !== pid) return;
  const scenes = cameraScenes();
  if (!cameraImageFor(cid,scenes[0].id) || cameraAssetStale(cid,scenes[0].id,cameraImageFor(cid,scenes[0].id))) return;
  const remaining = scenes.slice(1).filter(s => !cameraImageFor(cid,s.id) || cameraAssetStale(cid,s.id,cameraImageFor(cid,s.id)));
  const pending = remaining.filter(s=>!cameraConfirmReason(cid,s.id)).map(s => cameraRecords(cid,s.id).at(-1));
  if (confirmImages && (!pending.length || !await pageConfirm(`确认生成其余 ${pending.length} 张主镜头图？${imageBatchFee(pending)}。`))) return;
  cameraBusy.add(busyKey); renderReplacements(true);
  try {
    if (confirmImages) {
      for (const rec of pending) {
        if (P.id !== pid) return;
        const sid = rec.for_task.split(':')[2];
        // A later scene may change while an earlier paid request is in flight.
        if (cameraRecords(cid,sid).at(-1)?.id !== rec.id || cameraConfirmReason(cid,sid,rec)) continue;
        await api('POST',`projects/${pid}/images/${rec.id}/confirm`);
        finishTemporaryImage(rec,pid);
      }
    } else {
      for (const scene of remaining) { if (P.id !== pid) return; await draftSceneCamera(cid,scene.id,true); }
    }
    if (P.id !== pid) return;
    const updated = await api('GET',`projects/${pid}`);
    if (P.id === pid) P = updated;
  } finally { cameraBusy.delete(busyKey); if (P.id === pid) renderReplacements(true); }
}
function openCameraLightbox(file, caption) {
  const box = $('#cameraLightbox');
  updateHtml(box,`<button class="camera-lightbox-close" data-camera-close aria-label="关闭大图">×</button><figure><img src="${fileUrl(file)}" alt="${esc(caption)}"><figcaption>${esc(caption)}</figcaption></figure>`);
  box.hidden = false;
}

function renderCameraFrameSelection(card) {
  const cid = card.closest('[data-camera-person]').dataset.cameraPerson, sid = card.dataset.scene;
  const scene = cameraScenes().find(s => s.id === sid);
  if (!scene) return;
  // Update this card only, so unsaved replacement settings outside it survive.
  const edits = $$('[data-camera-editor] [data-cam]',card).map(el => ({record:el.closest('[data-camera-editor]').dataset.cameraRecord,field:el.dataset.cam,value:el.value}));
  const html = sceneCameraCard(cid,scene);
  updateHtml(card,html.slice(html.indexOf('>')+1,html.lastIndexOf('</article>')));
  for (const edit of edits) {
    const editor = cameraEditor(cid,sid,edit.record), input = editor && $(`[data-cam="${edit.field}"]`,editor);
    if (input) input.value = edit.value;
  }
  if(P.routes?.camera_image)renderLegacyRoutes();
  const row = card.closest('[data-camera-person]'), batch = $('[data-camera-batch-confirm]',row);
  if (batch) {
    // Keep the batch button in sync without replacing unsaved form controls.
    const template = document.createElement('template'); template.innerHTML = cameraGroupHtml(cid);
    const next = $('[data-camera-batch-confirm]',template.content);
    batch.textContent = next.textContent;
    if (next.hasAttribute('disabled')) batch.setAttribute('disabled',''); else batch.removeAttribute('disabled');
  }
}
// ---------- per-scene casting (03) ----------
// The server owns detection/migration; local edits only change the effective choices.
const sceneEdited = new Set(), sceneFrameOpen = new Set(), sceneFrameErrors = new Map(), sceneUnsaved = new Map();
const sceneSettingBaselines = new Map(), sceneEditBusy = new Map(), sceneEditErrors = new Map();
let scenePhotoChoice = null, sceneAddChoice = null, sceneRenderedProject = null;
function scenePlanEntry(sid) { return P.replacements?.scene_plan?.[sid] || {}; }
function sceneGlobalSubject(key) {
  if (key === 'product') return {key, name:'商品',kind:'product',...(P.replacements?.product || {})};
  const original = P.breakdown?.characters?.find(c => c.id === key) || {};
  return {key, source_id:key, name:original.name || key,kind:'character',...(P.replacements?.characters?.find(c => c.source_id === key) || {})};
}
function sceneEditorRows(includeDropped=false) {
  return cameraScenes(includeDropped).map(scene => {
    if(scene.dropped)return {scene_id:scene.id,name:scene.name,time_ranges:scene.timeRanges || [],dropped:true,subjects:[]};
    const sid=scene.id, derived=P.scene_cast?.[sid] || {}, plan=scenePlanEntry(sid);
    const ranges=derived.time_ranges || scene.timeRanges || [];
    // Compatibility for a response from an older server; current responses carry scene_cast.
    const shots=(P.breakdown?.shots || []).filter(s=>!ranges.length || ranges.some(([a,b])=>Number(s.end)>a && Number(s.start)<b));
    let subjects=derived.subjects;
    if(!subjects) {
      const keys=new Set(shots.flatMap(s=>s.characters || []));
      const hasProduct=(P.breakdown?.productMoments || []).some(m=>!ranges.length || ranges.some(([a,b])=>Number(m.end)>a && Number(m.start)<b))
        || shots.some(s=>s.product && !/^(?:无|没有|未|尚未|不含|none|no product)/i.test(String(s.product).trim()));
      subjects=[...(P.breakdown?.characters || []).filter(c=>keys.has(c.id) && !offscreenCharacter(c)).map(c=>({key:c.id,original_file:''})),...(hasProduct?[{key:'product'}]:[])];
    }
    const entries=subjects.filter(s=>!s.added || (plan.added || []).includes(s.key));
    for(const key of plan.added || [])if(!entries.some(s=>s.key===key))entries.push({key,added:true});
    return {...derived,merged_from:scene.merged_from || derived.merged_from,scene_id:sid,name:derived.name || scene.name,time_ranges:ranges,subjects:entries.map(subject=>{
      const key=subject.key || subject.source_id, global=sceneGlobalSubject(key), override=plan.subjects?.[key] || {};
      const aid=Object.hasOwn(override,'asset_id') ? override.asset_id : global.asset_id;
      return {...global,...subject,key,name:subject.name || global.name,kind:subject.kind || global.kind,
        on:Object.hasOwn(override,'on') ? override.on : (plan.added || []).includes(key) && (subject.added || !subjects.some(s=>s.key===key)) ? true : global.action==='replace' && !!aid,
        asset_id:aid,
        description:Object.hasOwn(override,'description') ? override.description : global.description || '',
        override:Object.hasOwn(override,'asset_id') || Object.hasOwn(override,'description'),added:(plan.added || []).includes(key)};
    })};
  });
}
function scenePicture(file,name,button='') {
  return button ? `<button class="scene-photo ghost" ${button} aria-label="更换${esc(name)}的本场照片">${file ? `<img src="${fileUrl(file)}" alt="${esc(name)}">` : `<span>${esc(name)}</span>`}</button>`
    : `<span class="scene-photo">${file ? `<img src="${fileUrl(file)}" alt="${esc(name)}" onerror="this.hidden=true">` : `<span>${esc(name)}</span>`}</span>`;
}
function sceneOriginal(key) {
  const rows=Object.values(P.scene_cast || {}).flatMap(r=>r.subjects || []).filter(s=>s.key===key);
  return rows.find(s=>s.cast_original_file)?.cast_original_file || rows.find(s=>s.cast_original_url)?.cast_original_url || rows.find(s=>s.original_file)?.original_file || '';
}
function sceneOriginalDescription(key,sid,at) {
  if(key!=='product') {
    const person=P.breakdown?.characters?.find(c=>c.id===key) || {};
    return joinDisplayText([person.appearance,person.outfit],'，');
  }
  const ranges=sceneEditorRows().find(r=>r.scene_id===sid)?.time_ranges || [];
  const matches=row=>(!ranges.length || ranges.some(([a,b])=>Number(row.end)>a && Number(row.start)<b))
    && (at==null || Number(row.start)<=at && at<Number(row.end));
  return (P.breakdown?.productMoments || []).find(row=>matches(row) && row.product)?.product
    || (P.breakdown?.shots || []).find(row=>matches(row) && row.product && !/^(?:无|没有|未|尚未|不含|none|no product)/i.test(String(row.product).trim()))?.product || '';
}
function closeScenePhotoDialog() {
  const choice=scenePhotoChoice;scenePhotoChoice=null;renderReplacements(true);
  if(choice)$(`[data-scene-camera="${choice.sid}"] [data-scene-photo="${choice.key}"]`)?.focus({preventScroll:true});
}
function sceneSettingSignature(row) {
  return JSON.stringify([row.subjects.map(s=>s.on ? [s.key,true,s.asset_id,s.description || ''] : [s.key,false]),P.replacements?.scene || '']);
}
function sceneSettingsChanged(sid) {
  const row=sceneEditorRows().find(r=>r.scene_id===sid),key=`${P.id}:${sid}`;
  return !!row && sceneSettingBaselines.has(key) && sceneSettingBaselines.get(key)!==sceneSettingSignature(row);
}
function scenePhotoOptions(kind,selected,cast=false) {
  const roles=kind==='product'?['product_truth','target_state']:['person_identity','target_state'];
  return `<option value="">${cast?'不换（保持原片）':'— 选择照片 —'}</option>`+(P.assets || []).filter(a=>a.type==='image' && !internalAsset(a) && !a.camera_character && !a.scene_camera_binding && !a.scene_camera_scene && (!a.roles?.length || a.roles.some(r=>roles.includes(r))))
    .map(a=>`<option value="${esc(a.id)}" title="${esc(a.label || a.id)}" ${a.id===selected?'selected':''}>${esc(a.label || a.id)}</option>`).join('')+(cast && kind==='character'?`<option value="__remove__" ${selected==='__remove__'?'selected':''}>去掉这个人</option>`:'');
}
function sceneReadCasting() {
  const old=P.replacements || {}, characters=(P.breakdown?.characters || []).map(c=>{
    const previous=old.characters?.find(r=>r.source_id===c.id) || {}, row=$(`[data-cast-row="${c.id}"]`), select=row && $('[data-cast]',row);
    if(offscreenCharacter(c))return {...previous,source_id:c.id,action:'keep',asset_id:null,description:''};
    const value=select?.value ?? (previous.action==='remove'?'__remove__':previous.action==='replace'?previous.asset_id:'');
    return {...previous,source_id:c.id,action:value==='__remove__'?'remove':value?'replace':'keep',asset_id:value && value!=='__remove__'?value:null,description:$(`[data-cast-desc="${c.id}"]`)?.value ?? previous.description ?? ''};
  });
  const selected=$('[data-cast="product"]')?.value ?? (old.product?.action==='replace'?old.product.asset_id:'');
  return {...old,characters,product:{...old.product,action:selected?'replace':'keep',asset_id:selected || null,description:$('[data-cast-desc="product"]')?.value ?? old.product?.description ?? ''},scene:$('#sceneRep')?.value ?? old.scene ?? '',scene_plan:clone(old.scene_plan || {})};
}
function sceneCaptureCasting() { P.replacements=sceneReadCasting(); sceneUnsaved.set(P.id,P.replacements); }
function sceneChangePlan(sid,change) {
  sceneCaptureCasting();P.replacements.scene_plan ||= {};const plan=P.replacements.scene_plan[sid] ||= {};
  change(plan);sceneEdited.add(`${P.id}:${sid}`);cameraDrafts.delete(cameraDraftKey('scene',sid));dirty.rep=true;renderReplacements(true);scheduleReplacementCheck();
}
function sceneCameraRecords(sid) {
  const candidates=P.scene_cast?.[sid]?.camera_candidates || [],archived=new Set(P.replacements?.scene_edits?.archived_images || []);
  return Object.values(P.images || {}).filter(r=>!archived.has(r.asset_id || r.id) && (r.for_task===`scene-camera:${sid}` || r.state==='completed' && candidates.includes(r.asset_id)));
}
function sceneCurrentCamera(sid) { const row=P.scene_cast?.[sid];return row && Object.hasOwn(row,'camera_asset') ? row.camera_asset : scenePlanEntry(sid).camera_asset || null; }
function sceneConfirmReason(sid,record=sceneCameraRecords(sid).at(-1)) {
  const key=cameraDraftKey('scene',sid);
  if(cameraBusy.has(key) || record?.state!=='pending_confirmation')return '先完成 ② 生成预览';
  if(sceneSettingsChanged(sid) || P.camera_stale_images?.includes(record.id))return '设定已变，需要重新生成';
  if(cameraDrafts.has(key))return '先完成 ② 生成预览';
  if(cameraRouteSelected.has(key) && imageChannel('scene',sid,record)!==(record.channel || 'third_party'))return '渠道已切换，请重新生成预览';
  return '';
}
function sceneFramePresence(at) {
  if(at===null || at===undefined)return '';
  const shots=(P.breakdown?.shots || []).filter(s=>Number(s.start)<=Number(at) && Number(s.end)>Number(at));
  const visible=new Set(shots.flatMap(s=>s.characters || []));
  const names=(P.breakdown?.characters || []).filter(c=>visible.has(c.id) && !offscreenCharacter(c)).map(c=>{
    const description=String(c.appearance || c.outfit || '').split('，')[0];return (c.name || c.id)+(description?`（${description}）`:'');
  });
  const product=(P.breakdown?.productMoments || []).some(m=>Number(m.start)<=at && at<Number(m.end)) || shots.some(s=>s.product && !/^(?:无|没有|未|尚未|不含|none|no product)/i.test(String(s.product).trim()));
  if(product){const description=sceneOriginalDescription('product',null,at);names.push('商品'+(description?`（${description}）`:''));}
  return '画面里：'+(names.length ? names.join('、') : '没有识别到人物或商品');
}
function sceneFrameReason(reason) {
  const text=({manual:'手动选择',scene_auto:'按本场勾选的对象自动选帧',front_product:'正面、商品清楚',product_moment:'商品展示最完整',legacy_first_appearance:'旧规则：人物第一次出现后 0.50 秒',first_suitable:'首次合适出镜后 0.50 秒',scene_default:'缺少镜头证据，采用场景默认时间',unknown:'选取方式未记录'})[reason] || reason || '';
  return text.replace(/(\d+(?:\.\d+)?)\s*秒/g,(_,time)=>fmt(time)+' 秒');
}
function sceneSentLabels(ids,frameId) {
  return ids.map(id=>{const a=P.assets?.find(a=>a.id===id);return id===frameId || a?.derived_from || a?.internal || a?.roles?.includes('scene')?'参考帧':a?.label || id;});
}
function sceneThumbnailUrl(at,ranges=[]) {
  const candidates=[Number(Number(at).toFixed(2)),Math.floor(at*100)/100,Math.ceil(at*100)/100];
  const time=candidates.find(time=>!ranges.length || ranges.some(([a,b])=>time>=a && time<b)) ?? Number(at);
  return `/api/projects/${P.id}/thumb?at=${time===Number(time.toFixed(2))?fmt(time):time.toFixed(6)}`;
}
function sceneEditControls(row) {
  const sid=row.scene_id,rows=cameraScenes(true),index=rows.findIndex(s=>s.id===sid),next=rows[index+1];
  const busy=sceneEditBusy.has(P.id),action=sceneEditBusy.get(P.id),current=action?.sid===sid;
  const error=sceneEditErrors.get(`${P.id}:${sid}`),span=(row.time_ranges || []).map(([a,b])=>`${fmt(a)}–${fmt(b)} 秒`).join('、');
  if(row.dropped)return `<header class="scene-plan-heading"><span>${esc(sid)} ${esc(row.name)} ${span} · 已剪掉，成片里没有这一场</span><button class="small ghost" data-scene-restore="${esc(sid)}" ${busy?'disabled':''}>${current?'正在恢复…':'恢复'}</button></header>${error?`<p class="caption-warning" role="status">${esc(error)}</p>`:''}`;
  const adjacent=next && !next.dropped && Math.abs(Number(row.time_ranges?.at(-1)?.[1])-Number(next.timeRanges?.[0]?.[0]))<.000001;
  const onlyOne=cameraScenes().length<=1;
  return `<div class="scene-edit-actions">${adjacent?`<button class="small ghost" data-scene-merge="${esc(sid)}" data-next-scene="${esc(next.id)}" title="两场的人物、商品、场地一样时合并，只需生成一张主镜头图" ${busy?'disabled':''}>${current && action.action==='merge'?'正在合并…':'和下一场合并'}</button>`:''}<button class="small ghost" data-scene-drop="${esc(sid)}" ${busy || onlyOne?'disabled':''} ${onlyOne?'title="至少要保留一场"':''}>${current && action.action==='drop'?'正在剪掉…':'成片里剪掉这一场'}</button>${onlyOne?'<span class="hint">至少要保留一场</span>':''}</div>`;
}
async function editPlannedScenes(action,sid,nextSid) {
  const pid=P.id;if(sceneEditBusy.has(pid))return;
  const row=sceneEditorRows(true).find(r=>r.scene_id===sid);if(!row)return;
  if(action==='merge') {
    const hasCamera=[sid,nextSid].some(id=>sceneCurrentCamera(id) || P.scene_cast?.[id]?.display_camera_asset || scenePlanEntry(id).camera_asset || sceneCameraRecords(id).some(r=>r.state==='completed'));
    if(!await pageConfirm(`把 ${sid} 和 ${nextSid} 合并成一场？${hasCamera?'这两场已生成的主镜头图会失效，需要重新生成。':''}`))return;
  }
  if(action==='drop') {
    if(cameraScenes().length<=1)return;
    const range=(row.time_ranges || []).map(([a,b])=>`${fmt(a)}–${fmt(b)}`).join('、');
    if(!await pageConfirm(`剪掉 ${sid}（${range} 秒）后，这一段不生成视频，成片里也没有，后面的场景往前接。之后可以恢复。`))return;
  }
  sceneEditBusy.set(pid,{sid,action});sceneEditErrors.delete(`${pid}:${sid}`);renderReplacements(true);
  try {
    if(dirty.rep) {
      const updated=await api('PUT',`projects/${pid}/replacements`,readReplacements());if(P?.id!==pid)return;
      P={...P,...updated};dirty.rep=false;sceneUnsaved.delete(pid);
    }
    const updated=await api('POST',`projects/${pid}/scene-edits`,{action,scene_id:sid,...(nextSid?{next_scene_id:nextSid}:{})});if(P?.id!==pid)return;
    P={...P,...updated};sceneUnsaved.delete(pid);dirty.rep=false;
    for(const key of sceneEdited)if(key.startsWith(pid+':'))sceneEdited.delete(key);
    for(const key of sceneSettingBaselines.keys())if(key.startsWith(pid+':'))sceneSettingBaselines.delete(key);
    for(const key of cameraDrafts.keys())if(key.startsWith(pid+':'))cameraDrafts.delete(key);
    renderCameraImages();renderSteps();renderFinal();scheduleReplacementCheck(true);
  } catch(error) {
    sceneEditErrors.set(`${pid}:${sid}`,publicText(error.message || '这次没保存成功，请重试'));
  } finally {
    sceneEditBusy.delete(pid);if(P?.id===pid)renderReplacements(true);
  }
}
function scenePlanCard(row) {
  if(row.dropped)return `<article class="scene-camera scene-plan-card scene-dropped" data-camera-key="scene:${esc(row.scene_id)}" data-scene-camera="${esc(row.scene_id)}" data-scene="${esc(row.scene_id)}">${sceneEditControls(row)}</article>`;
  const sid=row.scene_id,key=cameraDraftKey('scene',sid),records=sceneCameraRecords(sid),rec=records.at(-1),draft=cameraDrafts.get(key),busy=cameraBusy.has(key),editing=sceneEditBusy.has(P.id);
  const current=sceneCurrentCamera(sid),currentAsset=P.assets?.find(a=>a.id===current),active=['pending_confirmation','running'].includes(rec?.state);
  const selected=current || row.display_camera_asset || scenePlanEntry(sid).camera_asset,selectedAsset=P.assets?.find(a=>a.id===selected);
  const hasImage=(!!current || !!selectedAsset?.file) && !P.missing_asset_files?.includes(selected);
  const hasPreview=rec?.state==='pending_confirmation' || !!draft?.prompt;
  const stale=(hasImage || hasPreview) && (sceneSettingsChanged(sid) || hasPreview && P.camera_stale_images?.includes(rec?.id)
    || hasImage && P.camera_stale_assets?.includes(selected));
  const state=busy?'正在处理…':rec?.state==='running'?'生成中':rec?.state==='query_pending'?'等待查询':stale?'设定已变，需要重新生成':draft?.error?'失败':rec?.state==='pending_confirmation'?'预览待确认':draft?.prompt?'预览待确认':current?'✓ 已完成，视频会用这张':rec?.state==='failed'?'失败':'待生成';
  const owner=draft?.prompt?draft:active?rec:records.find(r=>r.asset_id===current) || rec;
  const source=cameraSourceFrame(owner,null),frame=row.frame?.asset_id || row.frame?.file || row.frame?.frame_mode==='auto' && row.frame.at!==source?.frame_at ? row.frame : source ? {asset_id:source.id,file:source.file,at:source.frame_at ?? owner?.frame_at,present_text:source.frame_present_text,reason:source.scene_frame_reason || source.frame_reason,warning:source.frame_warning} : row.frame;
  const frameFile=frame?.file || P.assets?.find(a=>a.id===(frame?.asset_id || row.frame_asset_id))?.file;
  const frameAt=frame?.at ?? frame?.frame_at ?? row.frame_at;
  const validTime=at=>at!=null && Number.isFinite(Number(at)) && (!(row.time_ranges || []).length || row.time_ranges.some(([a,b])=>Number(at)>=a && Number(at)<b)) && Math.abs(Number(at)-frameAt)<=.3 && sceneFramePresence(at)===sceneFramePresence(frameAt);
  const nearest=frameAt==null?null:cameraAnalysisFrames().filter(f=>validTime(f.at)).sort((a,b)=>Math.abs(a.at-frameAt)-Math.abs(b.at-frameAt))[0];
  const suggestedFile=!frameFile && (validTime(frame?.suggested_at) && frame?.suggested_file || nearest?.file
    || (frameAt!=null && sceneThumbnailUrl(frameAt,row.time_ranges))),displayFile=frameFile || suggestedFile;
  const info=owner?.preview || owner || {}, ids=owner?.image_ids || info.image_ids || row.subjects.filter(s=>s.on && s.asset_id && (!row.image_subject_keys || row.image_subject_keys.includes(s.key))).map(s=>s.asset_id).concat(frame?.asset_id || row.frame_asset_id || []);
  const sent=sceneSentLabels(ids,owner?.clean_frame_asset_id || owner?.frame_asset_id || frame?.asset_id || row.frame_asset_id);
  const blocked=busy || editing || rec?.state==='running',confirmReason=sceneConfirmReason(sid,rec), versions=[...new Set([...(row.camera_candidates || []),...records.filter(r=>r.state==='completed').map(r=>r.asset_id),selected].filter(id=>P.assets?.some(a=>a.id===id)))];
  const frameChoices=(row.frame_choices || cameraAnalysisFrames()).filter(f=>!(row.time_ranges || []).length || row.time_ranges.some(([a,b])=>Number(f.at)>=a && Number(f.at)<b));
  return `<article class="scene-camera scene-plan-card" data-camera-key="scene:${esc(sid)}" data-camera-person="scene" data-scene-camera="${esc(sid)}" data-scene="${esc(sid)}">
    <header class="scene-plan-heading"><h4>${esc(sid)} ${esc(row.name || '原片场景')} <span class="hint">${(row.time_ranges || []).map(([a,b])=>`${fmt(a,2)}–${fmt(b,2)} 秒`).join('、')}</span></h4>${sceneEditControls(row)}<span class="camera-state ${current && !stale && !active && !busy?'completed':''}" role="status">${state}</span></header>
    ${row.merged_from?.length?`<p class="hint scene-merged">由 ${row.merged_from.map(esc).join('、')} 合并 <button class="small ghost" data-scene-split="${esc(sid)}" ${busy || editing?'disabled':''}>${sceneEditBusy.get(P.id)?.action==='split' && sceneEditBusy.get(P.id)?.sid===sid?'正在拆开…':'拆开'}</button></p>`:''}
    ${sceneEditErrors.get(`${P.id}:${sid}`)?`<p class="caption-warning" role="status">${esc(sceneEditErrors.get(`${P.id}:${sid}`))}</p>`:''}
    <div class="scene-plan-layout"><section class="scene-reference"><h5>① 原片参考帧</h5>${displayFile?`<button class="camera-image-button" data-camera-zoom="${esc(displayFile)}" data-camera-caption="${esc(sid+(suggestedFile?' 预计参考帧':' 原片参考帧'))}"><img src="${fileUrl(displayFile)}" alt="${suggestedFile?'预计参考帧':'原片参考帧'}"></button>`:'<div class="camera-placeholder scene-media-empty">点“自动选帧”查看画面</div>'}
      ${suggestedFile?'<p class="hint">预计用这一帧，点“自动选帧”截取准确画面</p>':''}${frameAt!=null?`<p class="hint">${frameFile?'':'预计 '}${fmt(frameAt)} 秒</p>`:''}<p class="hint" data-scene-present>${esc(sceneFramePresence(frameAt) || frame?.present_text)}</p>${frame?.warning?`<p class="caption-warning">${esc(frame.warning)}</p>`:''}${frame?.reason?`<p class="hint">${esc(sceneFrameReason(frame.reason))}</p>`:''}
      <div class="row wrap"><button class="small ghost" data-scene-frame-auto="${esc(sid)}" ${blocked?'disabled':''}>${busy?'正在处理…':'自动选帧'}</button><button class="small ghost" data-scene-frame-open="${esc(sid)}" ${blocked?'disabled':''}>换一帧</button></div>
      <p class="hint">优先选勾选对象都在画面里的一帧</p>
      <details data-detail-key="scene-frames-${esc(sid)}" ${sceneFrameOpen.has(key)?'open':''}><summary>可选的原片画面</summary><div class="frames camera-frames">${frameChoices.map(f=>`<button class="ghost" data-scene-frame-at="${esc(f.at)}" data-scene="${esc(sid)}" ${blocked?'disabled':''}>${f.file || f.url?`<img loading="lazy" src="${fileUrl(f.file || f.url)}" alt="${fmt(f.at)} 秒">`:''}<span>${fmt(f.at)} 秒</span></button>`).join('')}</div><label>输入秒数 <input type="number" step="0.01" data-scene-time="${esc(sid)}" value="${frameAt==null?'':fmt(frameAt)}" ${blocked?'disabled':''}></label></details>
      ${sceneFrameErrors.get(key)?`<p class="caption-warning" role="status">${esc(sceneFrameErrors.get(key))}</p>`:''}
    </section><section class="scene-subjects"><h5>这一场要换的：</h5>${row.subjects.length?'':`<p class="hint">这一场画面里没有识别到人物或商品，可以点“+ 添加”</p>`}
      ${row.subjects.map(subject=>{const asset=P.assets?.find(a=>a.id===subject.asset_id),description=subject.original_description || sceneOriginalDescription(subject.key,sid),original='原片：'+(description || '拆解中未说明外观');return `<div class="scene-subject ${subject.on?'':'is-kept'}" data-scene-subject="${esc(subject.key)}"><label class="scene-subject-check"><input type="checkbox" data-scene-on="${esc(subject.key)}" data-scene="${esc(sid)}" ${subject.on?'checked':''} ${blocked?'disabled':''}><span class="sr-only">这一场替换${esc(subject.name)}</span></label><span class="scene-subject-name">${esc(subject.name)}<small class="hint scene-original-description" data-scene-original title="${esc(displayPunctuation(original))}">${displayTextHtml(original)}</small>${subject.override?`<small class="hint">本场单独设定 <button class="small ghost" data-scene-follow="${esc(subject.key)}" data-scene="${esc(sid)}" ${blocked?'disabled':''}>恢复跟随选角</button></small>`:''}</span><span aria-hidden="true">→</span>${scenePicture(asset?.file,asset?.label || '选照片',`data-scene-photo="${esc(subject.key)}" data-scene="${esc(sid)}" ${blocked?'disabled':''}`)}${subject.added?`<button class="small ghost" data-scene-remove="${esc(subject.key)}" data-scene="${esc(sid)}" ${blocked?'disabled':''}>移除</button>`:''}</div>`;}).join('')}
      <button class="small ghost" data-scene-add="${esc(sid)}" ${blocked?'disabled':''}>+ 添加</button>${sceneAddChoice===sid?`<label>加进这一场 <select data-scene-add-choice="${esc(sid)}"><option value="">— 选择人物或商品 —</option>${[...(P.breakdown?.characters || []).filter(c=>!offscreenCharacter(c)).map(c=>({key:c.id,name:c.name})),{key:'product',name:'商品'}].filter(c=>!row.subjects.some(s=>s.key===c.key)).map(c=>`<option value="${esc(c.key)}">${esc(c.name)}</option>`).join('')}</select></label>`:''}
      ${row.subjects.some(s=>s.on)?'':'<p class="hint" data-scene-unchanged>这一场没换任何人或商品，成片会和原片一样</p>'}</section>
    <section class="scene-result"><h5>主镜头图</h5>${row.camera_frame_notice?`<p class="hint" data-camera-frame-notice>${esc(row.camera_frame_notice)}</p>`:''}${selectedAsset?.file?`<button class="camera-image-button" data-camera-zoom="${esc(selectedAsset.file)}" data-camera-caption="${esc(sid+' 在用的主镜头图')}"><img src="${fileUrl(selectedAsset.file)}" alt="在用的主镜头图"></button>${stale?'<p class="caption-warning">设定已变，需要重新生成</p>':''}`:'<div class="camera-placeholder scene-media-empty">还没有在用的图</div>'}
      <label>生图渠道 <select data-scene-channel="${esc(sid)}" ${blocked?'disabled':''}><option value="" ${!cameraRouteSelected.has(key)?'selected':''}>${esc(defaultRouteLabel('camera_image'))}</option><option value="official" ${cameraRouteSelected.has(key) && imageChannel('scene',sid,rec)==='official'?'selected':''}>官方 · ${esc(STATUS?.openai_image_model || 'gpt-image-2')}</option><option value="third_party" ${cameraRouteSelected.has(key) && imageChannel('scene',sid,rec)==='third_party'?'selected':''}>三方渠道 · gpt-image-2</option></select></label>
      ${row.subjects.some(s=>s.kind==='product' && s.on) && !P.replacements?.product?.description?.trim()?'<p class="hint" data-product-note-warning>商品说明还没填，生图更容易走样。写清楚是什么商品、怎么戴/怎么用 <a href="#" data-product-note-link>去填写商品说明</a></p>':''}
      <p><button class="small" data-scene-draft="${esc(sid)}" ${blocked?'disabled':''}>${busy?'正在生成预览…':rec?.state==='running'?'生成中…':'② 生成预览'}</button> <button class="small pay" data-scene-confirm="${esc(sid)}" data-image-id="${esc(rec?.id || '')}" ${confirmReason || blocked?'disabled':''}>③ 确认生图（${esc(imageFee(rec))}）</button></p>
      <p class="hint">预览会调用推理模型写提示词，不收生图费</p><p class="hint" data-scene-sent>${sent.length?'送去生图：'+sent.map(esc).join(' + '):'送去生图：先选参考帧'}</p>${confirmReason && rec?.state==='pending_confirmation'?`<p class="hint">${esc(confirmReason)}</p>`:''}
      ${draft?.error?`<p class="caption-warning">${esc(publicText(draft.error))}</p>${thirdRetry('scene-camera',`data-scene="${esc(sid)}"`)}`:''}
      ${!busy && (draft?.prompt || rec?.state==='pending_confirmation' && rec.preview?.prompt)?`<details data-detail-key="scene-prompt-${esc(sid)}"><summary>提示词（可编辑）</summary><textarea data-scene-prompt="${esc(sid)}" rows="8">${esc(draft?.prompt || rec.preview.prompt)}</textarea><button class="small ghost" data-scene-repreview="${esc(sid)}" ${blocked || stale?'disabled':''}>按修改后的提示词重新生成预览</button></details>`:''}
      ${cameraCaptionInfo(owner,null)}${rec?.state==='running'?`<p class="hint">生成中 <span data-image-wait="${esc(rec.id)}"></span></p>`:''}${queryRetryHtml(rec,true)}${rec?.state==='failed'?`<p class="caption-warning">${esc(publicText(rec.error || '生成失败，请重新生成预览'))}</p>${imageRecovery(rec)}`:''}${imageUsage(rec)}
      ${P.camera_notice?.[`scene:${sid}`]?`<p class="caption-warning" data-camera-notice role="status">${esc(P.camera_notice[`scene:${sid}`])}</p>`:''}${row.legacy_notice?`<p class="hint">${esc(row.legacy_notice)}</p>`:''}<details data-detail-key="scene-versions-${esc(sid)}"><summary>已生成的版本（${versions.length}）</summary><div class="camera-versions">${versions.reverse().map(aid=>{const a=P.assets.find(a=>a.id===aid),expired=P.camera_stale_assets?.includes(aid);return `<figure class="camera-version" data-camera-version="${esc(aid)}"><button class="ghost" data-camera-zoom="${esc(a.file)}" data-camera-caption="${esc(a.label || aid)}"><img src="${fileUrl(a.file)}" alt="${esc(a.label || aid)}" loading="lazy"></button><figcaption>${materialNameHtml(a.label || aid)}${imageModelAnnotationHtml(records.find(r=>r.asset_id===aid))}${expired?'<span class="hint">设定已变，需要重新生成</span>':aid===current?'<b class="tag camera-in-use">在用</b>':`<button class="small ghost" data-scene-use="${esc(aid)}" data-scene="${esc(sid)}" ${blocked?'disabled':''}>改用这张</button>`}</figcaption></figure>`;}).join('')}</div></details>
    </section></div></article>`;
}
function scenePhotoDialog() {
  if(!scenePhotoChoice)return '';
  const subject=sceneGlobalSubject(scenePhotoChoice.key);
  return `<div class="scene-photo-picker" data-detail-key="scene-photo-dialog" data-scene-dialog="${esc(scenePhotoChoice.sid)}" role="dialog" aria-modal="true" aria-label="选择本场照片"><div><h4>为${esc(subject.name)}选择这一场用的照片</h4><label>照片 <select data-scene-pick="${esc(scenePhotoChoice.key)}" data-scene="${esc(scenePhotoChoice.sid)}">${scenePhotoOptions(subject.kind,'')}</select></label><button class="small ghost" data-scene-pick-cancel>取消</button></div></div>`;
}
function renderScenePlanReplacements(force) {
  if(!P.breakdown){updateHtml($('#replacementForm'),'<p class="hint">完成拆解后，这里会列出原片里的人物。</p>');return;}
  if(sceneRenderedProject!==P.id){scenePhotoChoice=null;sceneAddChoice=null;sceneRenderedProject=P.id;}
  if(dirty.rep && sceneUnsaved.has(P.id))P.replacements=sceneUnsaved.get(P.id);
  const box=$('#replacementForm'),rows=sceneEditorRows(true),rep=P.replacements || {};
  for(const row of rows){const key=`${P.id}:${row.scene_id}`;if(!sceneSettingBaselines.has(key) || !dirty.rep && !sceneEdited.has(key))sceneSettingBaselines.set(key,sceneSettingSignature(row));}
  const signature=JSON.stringify([rep,P.scene_cast,P.assets,P.images,P.camera_stale_assets,P.camera_stale_images,rows,[...cameraDrafts],[...cameraBusy],[...cameraChannels],[...sceneFrameErrors],scenePhotoChoice,sceneAddChoice,[...sceneEditBusy],[...sceneEditErrors]]);
  if(!force && signature===seen.rep)return;
  seen.rep=signature;
  const casting=[...(P.breakdown?.characters || []).map(c=>({key:c.id,name:c.name,kind:'character',offscreen:offscreenCharacter(c)})),{key:'product',name:'商品',kind:'product'}];
  const castHtml=casting.map(c=>{const global=sceneGlobalSubject(c.key),value=global.action==='remove'?'__remove__':global.action==='replace'?global.asset_id:'',photo=global.action==='replace' && P.assets?.find(a=>a.id===global.asset_id);
    return `<div class="scene-cast-row" data-cast-row="${esc(c.key)}">${scenePicture(sceneOriginal(c.key),c.name)}<span aria-hidden="true">→</span>${c.offscreen?'<span class="hint">画外角色，不出镜</span>':`<label>换成 <select data-cast="${esc(c.key)}">${scenePhotoOptions(c.kind,value,true)}</select></label>${photo?.file?`<span data-cast-photo="${esc(c.key)}">${scenePicture(photo.file,photo.label || c.name)}</span>`:''}`}<b>${esc(c.name)}</b>${c.offscreen?'':`<details data-detail-key="cast-note-${esc(c.key)}" data-cast-note="${esc(c.key)}" ${global.description?'open':''}><summary>${c.kind==='product'?'商品说明':'补充说明'}</summary><textarea data-cast-desc="${esc(c.key)}" rows="3" placeholder="${c.kind==='product'?'写清楚图里哪一件是商品、品名和主推卖点':'补充照片里看不出的设定；不要写原片人物的外观'}">${esc(global.description || '')}</textarea></details>`}</div>`;}).join('');
  updateHtml(box,`<section class="scene-casting"><h3>选角</h3>${castHtml}<label class="scene-setting">场景 <input id="sceneRep" type="text" value="${esc(rep.scene || '')}" placeholder="留空 = 保持原片场景；或写：换成明亮的直播间"></label></section><section class="scene-plan"><h3>分场表</h3><p class="hint">每一场都要复刻。勾选这一场要换的人和商品。</p><div class="scene-camera-list">${rows.map(scenePlanCard).join('')}</div></section>${scenePhotoDialog()}`);
  $('#repHint').textContent=dirty.rep?'设定已修改，生成预览前会自动保存':'';
}
async function captureSceneFrame(sid,at) {
  const pid=P.id,key=cameraDraftKey('scene',sid);
  if(cameraBusy.has(key))return;
  if(dirty.rep)await saveReplacements();if(P?.id!==pid)return;
  cameraBusy.add(key);sceneFrameErrors.delete(key);renderReplacements(true);
  try {
    const frame=await api('POST',`projects/${pid}/scene-camera/frame`,{scene_id:sid,...(at===undefined?{}:{at})});
    if(P?.id!==pid)return;
    P.scene_cast ||= {};P.scene_cast[sid] ||= {};P.scene_cast[sid].frame=frame;P.scene_cast[sid].frame_at=frame.at;P.scene_cast[sid].frame_asset_id=frame.asset_id;
    P.scene_cast[sid].image_subject_keys=frame.image_subject_keys;
    P.replacements.scene_plan ||= {};P.replacements.scene_plan[sid] ||= {};Object.assign(P.replacements.scene_plan[sid],{frame_at:Number(Number(frame.at).toFixed(2)),frame_asset_id:frame.asset_id,frame_mode:frame.frame_mode || (at===undefined?'auto':'manual')});
    const asset={id:frame.asset_id,type:'image',file:frame.file,internal:true,frame_at:frame.at};if(!P.assets.some(a=>a.id===asset.id))P.assets.push(asset);
    sceneEdited.add(`${pid}:${sid}`);cameraDrafts.delete(key);
  } catch(error) {if(P?.id===pid)sceneFrameErrors.set(key,publicText(error.message));throw error;}
  finally{cameraBusy.delete(key);if(P?.id===pid)renderReplacements(true);}
}
async function draftPlannedScene(sid) {
  const pid=P.id,key=cameraDraftKey('scene',sid);if(cameraBusy.has(key))return;
  if(dirty.rep)await saveReplacements();if(P?.id!==pid)return;
  cameraBusy.add(key);cameraDrafts.delete(key);renderReplacements(true);
  try {
    const route=requestRoute('camera_prompt',$('#analyzeProvider'));
    const draft=await api('POST',`projects/${pid}/scene-camera/draft`,{scene_id:sid,...route});if(P?.id!==pid)return;
    finishTemporaryProvider('camera_prompt',route);cameraDrafts.set(key,draft);
    const updated=await api('GET',`projects/${pid}`);if(P?.id!==pid)return;P=updated;
    await previewPlannedScene(sid,draft);if(P?.id===pid)renderAssets();
  }catch(error){if(P?.id===pid)cameraDrafts.set(key,{...(cameraDrafts.get(key) || {}),error:publicText(error.message)});throw error;}
  finally{cameraBusy.delete(key);if(P?.id===pid)renderReplacements(true);}
}
async function previewPlannedScene(sid,draft) {
  const pid=P.id,key=cameraDraftKey('scene',sid),record=sceneCameraRecords(sid).at(-1);
  const source=draft || cameraDrafts.get(key) || (record && {...record.preview,image_ids:record.image_ids,for_task:record.for_task,camera_binding:record.camera_binding});
  if(!source?.prompt)return;
  const channel=imageChannel('scene',sid,record),prompt=draft?source.prompt:$(`[data-scene-prompt="${sid}"]`)?.value ?? source.prompt;
  await api('POST',`projects/${pid}/images/preview`,{...source,prompt,channel,route_selected:cameraRouteSelected.has(key),model:channel==='official'?STATUS?.openai_image_model || 'gpt-image-2':source.model || 'gpt-image-2',resolution:source.resolution || '2k',ratio:source.ratio || '9:16'});
  if(P?.id!==pid)return;cameraDrafts.delete(key);if(!dirty.rep)sceneEdited.delete(`${pid}:${sid}`);
  const updated=await api('GET',`projects/${pid}`);if(P?.id!==pid)return;P=updated;renderReplacements(true);
}
async function usePlannedSceneCamera(sid,aid) {
  const pid=P.id;
  return withScriptSave(`[data-scene-use="${aid}"]`,async()=>{
    const updated=await api('POST',`projects/${pid}/scene-camera/use`,{scene_id:sid,asset_id:aid});if(P?.id!==pid)return;
    P={...P,...updated};renderScript();renderTasks(true);renderReplacements(true);scheduleReplacementCheck(true);
  },latest=>latest.scene_cast?.[sid]?.camera_asset===aid || latest.replacements?.scene_plan?.[sid]?.camera_asset===aid);
}

// ---------- source storyboard (step 2, entirely local) ----------
const storyboardBusy = new Set(), storyboardErrors = new Map();
function storyboardFile(file) {return file+'?v='+encodeURIComponent(JSON.stringify([P.storyboard?.built_at,P.storyboard?.source_sig,P.storyboard?.merges || []]));}
function renderStoryboard() {
  const box=$('#storyboardView');if(!box)return;
  const board=P.storyboard,busy=storyboardBusy.has(P.id) || P.analysis?.steps?.storyboard?.status==='running',stale=P.storyboard_status?.stale;
  const action=(value,label)=>`<button class="small ghost" data-story-action="${value}" ${busy || P.analysis?.status==='running' || P.jobs?.analyze?.state==='running'?'disabled':''}>${label}</button>`;
  const names=new Map((P.breakdown?.characters || []).map(c=>[c.id,c.name]));
  const cells=board?.cells || [],byId=new Map(cells.map(c=>[c.id,c]));
  const sheets=(board?.sheets || []).map(sheet=>{
    const chunk=sheet.cells.map(id=>byId.get(id)).filter(Boolean);
    return `<section data-story-sheet="${esc(sheet.id)}" class="storyboard-page storyboard-columns ${((P.source?.probe?.width || 0)>(P.source?.probe?.height || 1))?'landscape':''}"><div class="story-preview-source-sheet"><div class="storyboard-sheet"><img src="${fileUrl(storyboardFile(sheet.file))}" alt="原片分镜拼图 ${esc(sheet.id)}" loading="lazy"><div class="storyboard-hotspots">${Array.from({length:9},(_,i)=>chunk[i]?`<button data-story-cell="${esc(chunk[i].id)}" aria-label="放大第 ${chunk[i].index} 格"></button>`:'<span></span>').join('')}</div></div><button class="small ghost story-preview-sheet" data-story-preview-sheet="${esc(sheet.id)}">看整张分镜图</button></div>
      <div class="storyboard-list">${chunk.map(c=>`<article data-story-row="${esc(c.id)}" tabindex="0" role="button" aria-label="放大第 ${c.index} 格"><div class="row wrap"><b>第 ${c.index} 格</b><span>${fmt(c.start,1)}–${fmt(c.end,1)}s</span>${c.split_reason?`<span class="hint">${esc(c.split_reason)}</span>`:''}${c.part===1 && cells[cells.indexOf(c)+1]?.part===2 && c.shot_index!=null && cells[cells.indexOf(c)+1]?.shot_index===c.shot_index?`<button class="small ghost" data-story-merge="${esc(c.id)}" ${busy || stale?'disabled':''}>这两格其实是同一个镜头，合回去</button>`:''}</div>
      <p>${esc(c.characters.map(id=>(id+' '+(names.get(id)||'')).trim()).join('、') || '没有人物')}${c.product?' · 商品：'+esc(c.product):''}</p>${c.product_later?'<p class="hint">这一格后半段出现商品</p>':''}${c.action?`<p title="${esc(c.action_full || c.action)}">动作：${esc(c.action)}</p>${c.action_full?`<details data-story-detail><summary>详细</summary><p>${esc(c.action_full)}</p></details>`:''}`:''}${c.dialogue?`<p>台词：${esc(c.dialogue)}</p>`:''}</article>`).join('')}</div></section>`;
  }).join('');
  updateHtml(box,`<h3>原片分镜</h3><p class="hint">按镜头切点截帧，9 格一张。镜头中途有人进出、商品出现，或镜头超过 3 秒，会自动拆成两格。后面改图、分段都按这里的格号来。</p>
    ${board && (board.version || 1)<3?`<p class="caption-warning">原片分镜的规则更新了，重新生成会按截帧那一刻判断商品，并避开字幕切换的画面 ${action('build','重新生成（免费）')}</p>`:''}
    ${stale?`<p class="caption-warning">拆解结果变了，原片分镜要重新生成 ${action('build','重新生成原片分镜（免费）')}</p>`:''}
    ${board?.merges?.length?`<p>已手动合并 ${board.merges.length} 处 · ${action('restore','恢复自动切格')}</p>`:''}
    ${busy?'<p role="status">正在截帧…</p>':''}${storyboardErrors.has(P.id)?`<p class="caption-warning" role="status">${esc(storyboardErrors.get(P.id))} ${action('build','重试')}</p>`:''}
    ${!board?action('build','生成原片分镜（免费）'):`<details data-detail-key="source-storyboard"><summary>展开原片分镜和逐格文字 · ${cells.length} 镜</summary>${sheets}</details>`+(!stale?action('build','重新生成原片分镜（免费）'):'')}`);
}
async function editStoryboard(action,cell_id) {
  const pid=P.id;if(storyboardBusy.has(pid))return;
  storyboardBusy.add(pid);storyboardErrors.delete(pid);renderStoryboard();
  try {
    const next=await api('POST',`projects/${pid}/storyboard`,{action,cell_id});
    if(P?.id!==pid)return;
    P={...P,...next,storyboard_status:{stale:false,needs_rebuild:false}};renderSteps();
  } catch(error) {storyboardErrors.set(pid,publicText(error.message));}
  finally {storyboardBusy.delete(pid);if(P?.id===pid)renderStoryboard();}
}

// ---------- segment plan (step 04) ----------
const segmentPlanBusy=new Map(),segmentPlanErrors=new Map();
let segmentPlanConfirmation=null;
function segmentSheets(){return (P.segments_plan?.segments || []).map(s=>P.segment_boards?.segments?.[s.id]?.source).filter(Boolean);}
function segmentPlanConfirmationCurrent(){
  return segmentPlanConfirmation?.pid===P?.id && !!P.segments_plan && segmentPlanConfirmation.plan===JSON.stringify(P.segments_plan);
}
function segmentReplanHtml(busy){
  if(!P.segments_plan)return '';
  const generating=segmentPlanBusy.get(P.id)==='generate';
  const error=segmentPlanErrors.has(P.id)?`<span class="action-error" role="alert">${esc(segmentPlanErrors.get(P.id))}。请重新生成分段方案。</span>`:'';
  if(segmentPlanConfirmationCurrent() && !busy){
    const speech=P.breakdown?.dialogue?.length && !['done','fallback','skipped'].includes(P.breakdown?.speech_lines_state?.status);
    return `<div class="segment-replan-action" data-segment-confirmation><span class="segment-replan-message">重新生成分段方案：将调用推理模型，口播分界不合适时会自动重试。未改变的段保留可用新分镜，改动的段需重新生成新分镜；已有提示词都会过期，后续生成视频前需重写。历史视频保留，分段范围改变后需重新生成或选用匹配的视频。${speech?'会先自动整理一次台词断句。':''}点击“确认”开始，或“取消”返回。</span><span class="segment-replan-controls"><button class="small" data-segment-generate data-segment-confirm="${esc(P.id)}">确认</button><button class="ghost small" data-segment-cancel>取消</button></span></div>`;
  }
  return `<div class="segment-replan-action"><button class="ghost small" ${generating?'data-segment-generate':'data-segment-replan'} ${busy?'disabled':''}>${generating?'正在重新分段…':'重新生成分段方案'}</button>${error}</div>`;
}
function renderSegmentPlan(){
  const box=$('#segmentPlanView');if(!box)return;
  const plan=P.segments_plan,busy=segmentPlanBusy.has(P.id) || comparisonBatches.get(P.id)?.busy || v5PreparingOrConfirming(P.id);
  if(!segmentPlanConfirmationCurrent() || busy)segmentPlanConfirmation=null;
  updateHtml($('#comparisonBatchActions'),`${segmentReplanHtml(busy)}<button class="ghost small" data-comparison-batch="boards" ${busy || !plan || P.segments_plan_status?.stale?'disabled':''}>全部生成新分镜</button><button class="ghost small" data-comparison-batch="videos" ${busy || !plan?'disabled':''}>全部生成视频</button>`);
  updateHtml(box,`${!plan?`<p class="hint">先生成分段方案，再对照修改每一段。</p><button data-segment-generate ${busy || !P.storyboard?.cells?.length?'disabled':''}>${busy?'正在处理…':'生成分段方案（一次推理）'}</button>`:''}${(P.segments_plan_status?.speech_warnings || []).map(w=>`<p class="caption-warning" data-speech-boundary-warning role="status">${esc(w)}</p>`).join('')}${!plan && segmentPlanErrors.has(P.id)?`<p class="action-error" role="alert">${esc(segmentPlanErrors.get(P.id))}。请重新生成分段方案。</p>`:''}${comparisonBatches.get(P.id)?.message?`<p role="status">${esc(comparisonBatches.get(P.id).message)}</p>`:''}`);
}

let pageConfirmation=null;
function pageConfirm(message,title='确认操作'){
  if(pageConfirmation)finishPageConfirmation(false);
  const box=$('#pageConfirmation'),pid=P?.id,previous=document.activeElement;
  updateHtml(box,`<section class="page-confirmation-card"><h3 id="pageConfirmationTitle">${esc(title)}</h3><p>${esc(message)}</p><div class="row end"><button class="ghost" data-page-confirm="cancel">取消</button><button class="pay" data-page-confirm="accept">确认</button></div></section>`);
  box.hidden=false;if($('#app'))$('#app').inert=true;$('[data-page-confirm="cancel"]',box)?.focus();
  return new Promise(resolve=>{pageConfirmation={resolve,pid,previous};});
}
function finishPageConfirmation(accepted){
  const pending=pageConfirmation;if(!pending)return;
  pageConfirmation=null;$('#pageConfirmation').hidden=true;if($('#app'))$('#app').inert=false;
  if(pending.previous?.isConnected)pending.previous.focus({preventScroll:true});
  pending.resolve(accepted && pending.pid===P?.id);
}
function comparisonNeedsBoard(s){
  if(Object.values(P.images || {}).some(r=>r.storyboard_context?.sheet_id===s.id && ['running','submitting','query_pending'].includes(r.state) && !r.storyboard_context?.cell_id && r.storyboard_context?.plan_sig===editSheetState(s.id).plan?.sig && r.storyboard_context?.storyboard_sig===editSheetState(s.id).storyboard_sig) && !P.segment_board_status?.[s.id]?.source_stale)return false;
  if(!comparisonBoardReady(s.id) || P.segment_board_status?.[s.id]?.warnings?.length)return true;
  const state=editSheetState(s.id),draft=editDraft(s.id),current=state.versions?.find(v=>v.id===state.current);
  const savedMaterials={...Object.fromEntries(editMaterials(s.id).map(m=>[m.key,m.enabled])),...(P.segment_board_status?.[s.id]?.active_materials || state.materials)};
  return current?.plan_sig!==state.plan?.sig || draft.request!==(state.request || '') || Object.keys(draft.materials).some(key=>draft.materials[key]!==savedMaterials[key]) || JSON.stringify(draft.plan)!==JSON.stringify(state.plan || null);
}
function comparisonNeedsPrompt(s){
  const prompt=P.director?.segments?.find(row=>row.id===s.id),status=P.director_status?.segments?.find(row=>row.id===s.id);
  return !prompt?.prompt?.trim() || directorLegacy(P.director) || prompt.needs_rewrite || status?.stale;
}
function comparisonNeedsVideo(s){
  const status=P.director_status?.segments?.find(row=>row.id===s.id);
  return !(P.runs?.[s.id] || []).some(r=>['completed','running','submitting','query_pending'].includes(r.state) && (r.state!=='completed' || r.result) && !r.director_stale && !status?.stale && !status?.run_warnings?.[r.rev] && !P.director_status?.run_warnings?.[s.id]?.[r.rev]);
}
async function comparisonBatch(kind){
  const pid=P.id;if(v5PreparingOrConfirming(pid) || comparisonBatches.get(pid)?.busy || directorBusy.has(pid) || segmentPlanBusy.has(pid) || [...newStoryboardBusy].some(k=>k.startsWith(pid+':')))return;
  if(warnUnsavedCasting())return;
  const state={busy:true,errors:{},message:'正在逐段准备…'};comparisonBatches.set(pid,state);renderSteps();
  const pending=[];let processed=0,skipped=0;
  const fail=(id,error)=>{state.errors[id]=publicText(error?.message || String(error));};
  try{
    if(P?.id!==pid)return;
    if(kind==='videos' && directorDraft().dirty){await directorAction('save');if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);}
    if(P?.id!==pid)return;
    const segments=[...(P.segments_plan?.segments || [])];
    for(const s of segments){
      if(P?.id!==pid)break;
      directorErrors.delete(pid);
      try{
        if(kind==='boards'){
          if(!comparisonNeedsBoard(s)){skipped++;continue;}
          const boardStatus=P.segment_board_status?.[s.id];
          if(!editDraft(s.id).plan || boardStatus?.plan_stale || boardStatus?.casting_stale || boardStatus?.source_stale || boardStatus?.warnings?.some(w=>w.startsWith('选角或商品'))){await performStoryboardEdit(s.id,'plan');if(newStoryboardErrors.has(`${pid}:${s.id}`))throw Error(newStoryboardErrors.get(`${pid}:${s.id}`));}
          if(P?.id!==pid)break;
          await performStoryboardEdit(s.id,'preview');
          if(P?.id!==pid)break;
          if(newStoryboardErrors.has(`${pid}:${s.id}`))throw Error(newStoryboardErrors.get(`${pid}:${s.id}`));
          const rec=newStoryboardPreviews.get(`${pid}:${s.id}`);if(!rec)throw Error('没有得到预览，请重试');
          pending.push({id:s.id,rec});
        }else if(kind==='prompts'){
          if(!comparisonNeedsPrompt(s)){skipped++;continue;}
          if(!comparisonBoardReady(s.id))throw Error('先完成这一段的新分镜');
          await directorAction('rewrite',{segment_id:s.id});if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);processed++;
        }else{
          if(!comparisonNeedsVideo(s)){skipped++;continue;}
          if(!comparisonBoardReady(s.id))throw Error('先完成这一段的新分镜');
          if(comparisonNeedsPrompt(s))await directorAction('rewrite',{segment_id:s.id});
          if(P?.id!==pid)break;if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);
          await directorVideoAction('preview',s.id);if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);
          const rec=P.runs?.[s.id]?.findLast(r=>r.state==='pending_confirmation');if(!rec)throw Error('没有得到预览，请重试');
          pending.push({id:s.id,rec});
        }
      }catch(error){fail(s.id,error);}
    }
    if(P?.id!==pid)return;
    if(pending.length){
      const cost=kind==='boards'?imageBatchFee(pending.map(item=>item.rec)):`约 ${fmt(pending.reduce((total,item)=>total+Number(item.rec.billing_seconds || 0),0))} 计费秒（输出与参考视频合计；单价以渠道账户为准）`;
      const names=pending.map(item=>'第 '+((P.segments_plan?.segments || []).findIndex(s=>s.id===item.id)+1)+' 段').join('、');
      state.message=`${pending.length} 段预览已就绪，等待一次确认。`;renderSegmentPlan();
      const accepted=await pageConfirm(`${names}\n共 ${pending.length} 段，合计计费：${cost}。\n已跳过 ${skipped} 段；${Object.keys(state.errors).length} 段未能准备，其他段可继续。`,kind==='boards'?'全部生成新分镜':'全部生成视频');
      if(accepted && P?.id===pid){
        for(const {id,rec} of pending){
          if(P?.id!==pid)break;
          try{
            if(kind==='boards'){
              await performStoryboardEdit(id,'confirm',{image_id:rec.id},{confirmed:true});
              if(newStoryboardErrors.has(`${pid}:${id}`))throw Error(newStoryboardErrors.get(`${pid}:${id}`));
            }else{
              await directorVideoAction('confirm',id,{rev:rec.rev,job_sha256:rec.job_sha256},{confirmed:true});
              if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);
            }
            processed++;
          }catch(error){fail(id,error);}
        }
      }else state.message='已取消付费提交，预览保留在各段。';
    }
    if(!state.message.startsWith('已取消'))state.message=`已处理 ${processed} 段，跳过 ${skipped} 段${Object.keys(state.errors).length?'；'+Object.keys(state.errors).length+' 段失败，选中对应段查看原因':''}。`;
  }catch(error){state.message=publicText(error.message);}
  finally{state.busy=false;if(P?.id===pid)renderSteps();}
}

// ---------- storyboard image inspection (read-only, separate from Step 03) ----------
let storyboardLightbox=null,storyboardPointerSnapshot=null,storyboardPointerCandidate=null,storyboardPan=null,storyboardSuppressImageClick=false;
function storyboardPreviewCell(id,side){
  const cell=P?.storyboard?.cells?.find(c=>c.id===id),segments=P?.segments_plan?.segments || [],segment=segments.find(s=>s.cells.includes(id));
  const board=P?.segment_boards?.segments?.[segment?.id],version=board?.versions?.find(v=>v.id===board.current);
  const file=side==='source'?(cell?.file?storyboardFile(cell.file):null):directorCellFile(id);
  return cell && file?{id,side,file,cell,segment,version,imageRecord:side==='target'?storyboardVersionRecord(version,id,board.versions):null,segmentIndex:segments.indexOf(segment)+1}:null;
}
function storyboardPreviewTitle(item){
  if(item.title)return item.title;
  return `第 ${item.cell.index} 镜 · ${fmt(item.cell.start,1)}–${fmt(item.cell.end,1)} 秒 · ${item.side==='source'?'原片':'新分镜 第 '+Number(item.version?.id?.replace(/^v/,''))+' 版'}${item.segmentIndex?' · 第 '+item.segmentIndex+' 段':''}`;
}
function storyboardPreviewMagnifier(id,side){return `<button type="button" class="story-preview-magnifier" data-story-preview-cell="${esc(id)}" data-story-preview-side="${side}" aria-label="看大图" title="看大图"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"></circle><path d="m15 15 6 6M10 7v6M7 10h6"></path></svg></button>`;}
function captureStoryboardPointer(event){
  if(!P || event.button!==0 || event.target.closest?.('[data-story-preview-cell],#storyboardLightbox'))return;
  const cell=event.target.closest?.('[data-compare-cell]') || event.target.closest?.('[data-story-preview-holder]')?.querySelector('[data-compare-cell]');
  storyboardPointerCandidate=null;
  if(!cell || cell.disabled || !storyboardPreviewCell(cell.dataset.compareCell,cell.dataset.compareColumn)){storyboardPointerSnapshot=null;return;}
  // Each pointerdown is a candidate. click.detail, not a timer, identifies the
  // first click of the browser's actual double-click sequence.
  storyboardPointerCandidate={pid:P.id,key:cell.dataset.compareKey,selection:clone(comparisonSelection()),changes:[]};
}
function selectStoryboardComparisonCell(cell,event){
  const d=cell.dataset;
  if((event.detail || 1)===1)storyboardPointerSnapshot=storyboardPointerCandidate?.pid===P.id && storyboardPointerCandidate.key===d.compareKey?storyboardPointerCandidate:null;
  const snapshot=storyboardPointerSnapshot;
  if(snapshot?.pid!==P.id || snapshot.key!==d.compareKey)return selectComparisonCell(d.compareCell,d.compareColumn,event);
  if(event.detail>1)return;
  const caches=[newStoryboardCells,newStoryboardCellRequests,v5Actions,comparisonViews].map(map=>({map,before:new Map([...map].filter(([key])=>key.startsWith(P.id+':')).map(([key,value])=>[key,map===v5Actions?value:clone(value)]))}));
  selectComparisonCell(d.compareCell,d.compareColumn,event);
  // Only undo synchronous mutations made by selection. Async actions keep the
  // exact object held by their running coroutine; untouched entries stay live.
  snapshot.changes=[];
  for(const {map,before} of caches){
    const keys=new Set([...before.keys(),...[...map.keys()].filter(key=>key.startsWith(P.id+':'))]);
    for(const key of keys){const hadBefore=before.has(key),hadAfter=map.has(key),old=before.get(key),after=map.get(key),same=map===v5Actions?old===after:JSON.stringify(old)===JSON.stringify(after);
      if(hadBefore!==hadAfter || !same)snapshot.changes.push({map,key,hadBefore,before:old,hadAfter,after,afterValue:map===v5Actions?null:JSON.stringify(after)});
    }
  }
}
function restoreStoryboardPointer(cell){
  const snapshot=storyboardPointerSnapshot;if(!snapshot || snapshot.pid!==P?.id || snapshot.key!==cell.dataset.compareKey || snapshot.marquee)return false;
  comparisonSelections.set(P.id,snapshot.selection);
  for(const change of snapshot.changes){
    const {map,key,hadBefore,before,hadAfter,after,afterValue}=change;
    if(map.has(key)!==hadAfter || (map===v5Actions?map.get(key)!==after:JSON.stringify(map.get(key))!==afterValue))continue;
    if(hadBefore)map.set(key,before);else map.delete(key);
  }
  storyboardPointerSnapshot=null;storyboardPointerCandidate=null;renderSteps();return true;
}
function openStoryboardLightbox(item,origin){
  if(!item?.file)return;
  if(storyboardLightbox)closeStoryboardLightbox(false);
  storyboardLightbox={pid:P?.id,item,origin,originKey:origin && domIdentity(origin),pair:false,zoom:false,x:0,y:0,previousOverflow:document.body.style.overflow || ''};
  document.body.style.overflow='hidden';$('#storyboardLightbox').hidden=false;renderStoryboardLightbox();$('[data-story-preview-close]')?.focus();
}
function closeStoryboardLightbox(restoreFocus=true){
  const state=storyboardLightbox;if(!state)return;
  $('#storyboardLightbox').hidden=true;document.body.style.overflow=state.previousOverflow;storyboardLightbox=null;storyboardPan=null;storyboardSuppressImageClick=false;
  if(restoreFocus){const origin=state.origin?.isConnected?state.origin:$$('[data-compare-cell],button,[data-story-row]').find(el=>domIdentity(el)===state.originKey);(origin?.disabled?origin.closest('.comparison-preview-cell')?.querySelector('[data-story-preview-cell]'):origin)?.focus({preventScroll:true});}
}
function storyboardPreviewItems(){return (P?.storyboard?.cells || []).slice().sort((a,b)=>a.index-b.index).map(c=>storyboardPreviewCell(c.id,storyboardLightbox.item.side)).filter(Boolean);}
function renderStoryboardLightbox(){
  const state=storyboardLightbox;if(!state)return;
  if(state.pid!==P?.id){closeStoryboardLightbox(false);return;}
  const item=state.item,other=item.id?storyboardPreviewCell(item.id,item.side==='source'?'target':'source'):null;
  if(!other)state.pair=false;
  const items=item.id?storyboardPreviewItems():[],index=items.findIndex(i=>i.id===item.id),pairLabel=item.side==='source'?'和新分镜并排':'和原片并排';
  const images=state.pair?[item.side==='source'?item:other,item.side==='target'?item:other]:[item];
  const missing=item.id && !other?(item.side==='source'?'这一镜还没有新分镜。':'这一镜的原片图片不可用。'):'';
  updateHtml($('#storyboardLightbox'),`<header class="story-preview-header"><h2 id="storyboardLightboxTitle">${esc(storyboardPreviewTitle(item))}${imageModelAnnotationHtml(item.imageRecord)}</h2><div class="story-preview-modes"><button data-story-preview-mode="single" aria-pressed="${!state.pair}">只看这张</button>${item.id?`<button data-story-preview-mode="pair" aria-pressed="${state.pair}" ${other?'':'disabled aria-describedby="storyPreviewMissing"'}>${pairLabel}</button>`:''}</div><button data-story-preview-close aria-label="关闭大图">关闭</button></header><div class="story-preview-stage ${state.pair?'is-pair':''}"><button class="story-preview-nav story-preview-prev" data-story-preview-prev aria-label="上一镜" ${index>0?'':'disabled'}>←</button><button class="story-preview-nav story-preview-next" data-story-preview-next aria-label="下一镜" ${index>=0 && index<items.length-1?'':'disabled'}>→</button>${images.map(image=>`<figure class="story-preview-pane" data-story-preview-pane="${image.side}"><figcaption>${image.side==='source'?'原片':image.side==='target'?'新分镜':'参考图'}${image.side==='target' && image.version?' · 第 '+Number(image.version.id.replace(/^v/,''))+' 版':''}</figcaption><img data-story-preview-image src="${esc(image.url || fileUrl(image.file))}" alt="${esc(storyboardPreviewTitle(image))}" draggable="false" tabindex="0" role="button" aria-label="切换适合窗口和 100% 大小"></figure>`).join('')}</div><footer class="story-preview-footer"><span class="story-preview-hint"><span data-story-preview-zoom>适合窗口</span> · 点图缩放，拖动或滚动查看${missing?`<span id="storyPreviewMissing" role="status"> · ${missing}</span>`:''}</span>${item.boardProject?`<button data-batch-open-project="${esc(item.boardProject)}" data-segment="${esc(item.boardSegment || '')}">打开项目</button>`:''}${item.id && item.side==='target'?`<button data-story-preview-edit ${comparisonSelectable(item.segment?.id,item.id) && !comparisonSelectionLocked()?'':'disabled'}>改这一镜</button>`:''}</footer>`);
  layoutStoryboardLightbox();
}
function layoutStoryboardLightbox(){
  const state=storyboardLightbox;if(!state)return;
  const panes=$$('#storyboardLightbox [data-story-preview-pane]'),metrics=panes.map(pane=>{const image=$('img',pane),rect=pane.getBoundingClientRect(),width=rect.width || globalThis.innerWidth/(state.pair?2:1) || 600,height=rect.height || globalThis.innerHeight*.9 || 720;return {image,width,height,ratio:(image.naturalWidth || 9)/(image.naturalHeight || 16)};});
  const fit=Math.min(...metrics.map(m=>Math.min(m.height,m.width/m.ratio))),primary=metrics.find((_,i)=>panes[i].dataset.storyPreviewPane===state.item.side) || metrics[0];
  const height=state.zoom?(primary.image.naturalHeight || fit):fit;
  state.metrics=metrics;state.height=height;
  for(const m of metrics){const x=Math.max(0,(height*m.ratio-m.width)/2)*state.x,y=Math.max(0,(height-m.height)/2)*state.y;Object.assign(m.image.style,{height:height+'px',width:height*m.ratio+'px',transform:`translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`});}
  $('[data-story-preview-zoom]').textContent=state.zoom?'100%':'适合窗口';
  $('#storyboardLightbox').dataset.zoom=state.zoom?'actual':'fit';
}
function panStoryboardLightbox(dx,dy){
  const state=storyboardLightbox;if(!state?.zoom)return;
  const metric=state.metrics.find(m=>m.image.closest('[data-story-preview-pane]').dataset.storyPreviewPane===state.item.side) || state.metrics[0];
  const maxX=Math.max(0,(state.height*metric.ratio-metric.width)/2),maxY=Math.max(0,(state.height-metric.height)/2);
  state.x=maxX?Math.max(-1,Math.min(1,state.x+dx/maxX)):0;state.y=maxY?Math.max(-1,Math.min(1,state.y+dy/maxY)):0;layoutStoryboardLightbox();
}
function navigateStoryboardLightbox(offset){
  const state=storyboardLightbox;if(!state?.item.id)return;
  const items=storyboardPreviewItems(),index=items.findIndex(i=>i.id===state.item.id),next=items[index+offset];
  if(next){state.item=next;state.zoom=false;state.x=state.y=0;renderStoryboardLightbox();}
}
function storyboardLightboxClick(event){
  const target=event.target,modal=target.closest?.('#storyboardLightbox');
  if(modal && storyboardLightbox){
    event.preventDefault();
    if(storyboardLightbox.pid!==P?.id){closeStoryboardLightbox(false);return true;}
    if(target.closest('[data-batch-open-project]')){const button=target.closest('[data-batch-open-project]');batchOpenProject(button.dataset.batchOpenProject,button.dataset.segment);}
    else if(target.closest('[data-story-preview-close]'))closeStoryboardLightbox();
    else if(target.closest('[data-story-preview-prev]')){if(!target.closest('button').disabled)navigateStoryboardLightbox(-1);}
    else if(target.closest('[data-story-preview-next]')){if(!target.closest('button').disabled)navigateStoryboardLightbox(1);}
    else if(target.closest('[data-story-preview-mode]')){const button=target.closest('button');if(!button.disabled){storyboardLightbox.pair=button.dataset.storyPreviewMode==='pair';storyboardLightbox.x=storyboardLightbox.y=0;renderStoryboardLightbox();}}
    else if(target.closest('[data-story-preview-edit]')){if(!target.closest('button').disabled){const {item}=storyboardLightbox;closeStoryboardLightbox(false);setComparisonCells(item.segment.id,[]);selectComparisonCell(item.id,'target');$('[data-edit-cell-request]')?.focus();}}
    else if(target.closest('[data-story-preview-image]')){if(storyboardSuppressImageClick){storyboardSuppressImageClick=false;return true;}storyboardLightbox.zoom=!storyboardLightbox.zoom;storyboardLightbox.x=storyboardLightbox.y=0;layoutStoryboardLightbox();}
    else if(target===modal || target.matches('.story-preview-stage,.story-preview-pane'))closeStoryboardLightbox();
    return true;
  }
  const source=target.closest?.('button,a,summary,[data-story-row]');
  if(source?.dataset.storyCell || source?.dataset.storyRow){event.preventDefault();openStoryboardLightbox(storyboardPreviewCell(source.dataset.storyCell || source.dataset.storyRow,'source'),source);return true;}
  const button=target.closest?.('[data-story-preview-cell],[data-story-preview-sheet],[data-story-preview-version],[data-story-preview-file]');
  if(!button)return false;event.preventDefault();
  if(button.dataset.storyPreviewFile)openStoryboardLightbox({file:button.dataset.storyPreviewFile,side:'reference',title:button.dataset.storyPreviewCaption || '参考图原图'},button);
  else if(button.dataset.storyPreviewCell)openStoryboardLightbox(storyboardPreviewCell(button.dataset.storyPreviewCell,button.dataset.storyPreviewSide || 'source'),button.closest('.comparison-preview-cell')?.querySelector('[data-compare-cell]') || button);
  else if(button.dataset.storyPreviewSheet){const sheet=P?.storyboard?.sheets?.find(s=>s.id===button.dataset.storyPreviewSheet);if(sheet?.file)openStoryboardLightbox({file:storyboardFile(sheet.file),side:'source',title:'原片分镜整图 · '+sheet.id},button);}
  else {const sid=button.dataset.sheet,version=editSheetState(sid).versions?.find(v=>v.id===button.dataset.storyPreviewVersion);if(version?.file)openStoryboardLightbox({file:editVersionFile(version),side:'target',imageRecord:storyboardVersionRecord(version),title:`新分镜 第 ${Number(version.id.replace(/^v/,''))} 版 · 第 ${P.segments_plan.segments.findIndex(s=>s.id===sid)+1} 段 · 整图`},button);}
  return true;
}
function storyboardLightboxKey(event){
  if(!storyboardLightbox)return false;
  if(event.key==='Escape'){event.preventDefault();closeStoryboardLightbox();}
  else if(event.key==='ArrowLeft' || event.key==='ArrowRight'){event.preventDefault();navigateStoryboardLightbox(event.key==='ArrowLeft'?-1:1);}
  else if(['Enter',' '].includes(event.key) && event.target.hasAttribute?.('data-story-preview-image'))storyboardLightboxClick(event);
  else if(event.key==='Tab'){const controls=$$('button,[data-story-preview-image]',$('#storyboardLightbox')).filter(el=>!el.disabled);const index=controls.indexOf(document.activeElement);event.preventDefault();controls[(index+(event.shiftKey?-1:1)+controls.length)%controls.length]?.focus();}
  return true;
}

// ---------- paired storyboard workspace (UI state only, per project) ----------
const comparisonSelections=new Map(),comparisonBatches=new Map();
function comparisonBoardReady(id){
  const state=P.segment_boards?.segments?.[id],status=P.segment_board_status?.[id] || {};
  return !!state?.versions?.some(v=>v.id===state.current) && !status.source_stale && !status.plan_stale && !status.casting_stale && !P.segments_plan_status?.stale;
}
function comparisonSegmentState(segment){
  const board=P.segment_board_status?.[segment.id] || {},prompt=P.director?.segments?.find(s=>s.id===segment.id),status=P.director_status?.segments?.find(s=>s.id===segment.id) || {};
  const chosen=(P.runs?.[segment.id] || []).find(r=>r.rev===prompt?.selected_run && r.state==='completed' && r.result);
  const warning=P.segments_plan_status?.stale || board.source_stale || board.plan_stale || board.casting_stale || board.warnings?.length || status.stale || prompt?.needs_rewrite || chosen?.director_stale || status.run_warnings?.[chosen?.rev] || chosen?.review?.verdict==='fix' || comparisonBatches.get(P.id)?.errors?.[segment.id];
  if(warning)return {color:'warning',label:'有提示，请检查'};
  if(chosen)return {color:'done',label:'已选定可用版本'};
  if(comparisonBoardReady(segment.id))return {color:'ready',label:'新分镜好了，待生成视频'};
  return {color:'empty',label:'待生成新分镜'};
}
function comparisonSelection(){
  const segments=P.segments_plan?.segments || [];
  let selection=comparisonSelections.get(P.id);
  if(!segments.some(s=>s.id===selection?.sid)){
    const segment=segments.find(s=>comparisonSegmentState(s).color!=='done') || segments[0];
    selection={sid:segment?.id || null,cell:null,cells:[]};comparisonSelections.set(P.id,selection);
  }
  if(!selection.cell)selection.cells=[];
  else if(!selection.cells?.includes(selection.cell))selection.cells=[selection.cell];
  if(selection.cell && (!segments.find(s=>s.id===selection.sid)?.cells.includes(selection.cell) || !comparisonBoardReady(selection.sid))){selection.cell=null;selection.cells=[];newStoryboardCells.delete(`${P.id}:${selection.sid}`);}
  return selection;
}
function comparisonSelectedCells(){const selection=comparisonSelection();return selection.cells || (selection.cell?[selection.cell]:[]);}
function comparisonCellNumbers(ids){return ids.map(id=>P.storyboard?.cells?.find(c=>c.id===id)?.index || id).join('、');}
function comparisonSelectionLocked(){const state=P && v5Actions.get(editSheetKey(comparisonSelection().sid));return state?.kind==='cell' && state.cells?.length>1 && state.status==='preparing';}
function comparisonSelectable(sid,id){return !P.segments_plan?.skip_cells?.includes(id) && comparisonBoardReady(sid) && !!directorCellFile(id) && !v5CellGenerating(sid,id);}
function comparisonTouch(){return globalThis.matchMedia?.('(hover: none)').matches || false;}
function setComparisonCells(sid,ids){
  const selection=comparisonSelection(),oldSid=selection.sid,key=editSheetKey(oldSid),state=v5Actions.get(key);
  if(oldSid===sid && state?.status==='confirming' && state.kind==='cell')v5Actions.delete(key);
  selection.sid=sid;selection.cells=(P.segments_plan?.segments?.find(s=>s.id===sid)?.cells || []).filter(id=>ids.includes(id));selection.cell=selection.cells[0] || null;
  if(!selection.cell)selection.touchAdd=false;
  for(const key of newStoryboardCells.keys())if(key.startsWith(P.id+':'))newStoryboardCells.delete(key);
  if(selection.cell)newStoryboardCells.set(editSheetKey(sid),selection.cell);
}
function clearComparisonCells(){if(!P)return;const selection=comparisonSelection();setComparisonCells(selection.sid,[]);selection.hint='';renderSteps();}
function selectComparisonCell(id,side,event={}){
  if(comparisonSelectionLocked() || P.segments_plan?.skip_cells?.includes(id))return;
  const segment=P.segments_plan?.segments.find(s=>s.cells.includes(id));if(!segment)return;
  const selection=comparisonSelection(),add=event.shiftKey || event.metaKey || event.ctrlKey || comparisonTouch() && selection.touchAdd;
  if(side==='target' && !comparisonSelectable(segment.id,id))return;
  if(side==='target' && add && selection.cell && selection.sid!==segment.id){selection.hint='一次只能选同一段里的镜头';renderSteps();return;}
  v5View(segment.id).view='board';selection.hint='';
  if(side!=='target' || selection.sid!==segment.id && !add)setComparisonCells(segment.id,[]);
  else {const before=comparisonSelectedCells();setComparisonCells(segment.id,add?(before.includes(id)?before.filter(c=>c!==id):[...before,id]):before.length===1 && before[0]===id?[]:[id]);}
  renderSteps();
}
let comparisonDrag=null,comparisonSuppressClick=false;
function startComparisonDrag(event){
  captureStoryboardPointer(event);
  if(event.target.closest?.("[data-story-preview-cell],#storyboardLightbox"))return;
  comparisonSuppressClick=false;
  if(comparisonSelectionLocked() || event.button!==0 || event.pointerType==='touch' || comparisonTouch() || !P)return;
  const target=event.target.closest?.('[data-compare-column="target"][data-compare-cell]');
  if(!target || target.disabled || !comparisonSelectable(target.dataset.compareSegment,target.dataset.compareCell))return;
  comparisonDrag={pid:P.id,pointer:event.pointerId,sid:target.dataset.compareSegment,x:event.clientX,y:event.clientY,target,grid:target.closest('.comparison-board'),active:false,ids:[]};
}
function moveComparisonDrag(event){
  const drag=comparisonDrag;if(!drag || drag.pid!==P?.id || event.pointerId!==drag.pointer)return;
  if(!drag.active && Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<5)return;
  event.preventDefault();drag.active=true;drag.grid.classList.add('is-dragging');
  const rect={left:Math.min(event.clientX,drag.x),right:Math.max(event.clientX,drag.x),top:Math.min(event.clientY,drag.y),bottom:Math.max(event.clientY,drag.y)},base=drag.grid.getBoundingClientRect();
  if(!drag.box){drag.box=document.createElement('div');drag.box.className='comparison-marquee';drag.box.setAttribute('aria-hidden','true');drag.grid.appendChild(drag.box);}
  Object.assign(drag.box.style,{left:(rect.left-base.left)+'px',top:(rect.top-base.top)+'px',width:(rect.right-rect.left)+'px',height:(rect.bottom-rect.top)+'px'});
  let crossed=false;drag.ids=[];
  for(const cell of $$('[data-compare-column="target"][data-compare-cell]')){
    const box=cell.getBoundingClientRect(),hit=box.left<rect.right && box.right>rect.left && box.top<rect.bottom && box.bottom>rect.top;
    if(hit && cell.dataset.compareSegment && cell.dataset.compareSegment!==drag.sid)crossed=true;
    const selected=hit && cell.dataset.compareSegment===drag.sid && comparisonSelectable(drag.sid,cell.dataset.compareCell);
    if(selected)drag.ids.push(cell.dataset.compareCell);
    cell.classList[selected?'add':'remove']('is-cell');
  }
  drag.crossed=crossed;
  const hint=$('[data-compare-selection-hint]');if(hint)hint.textContent=crossed?'一次只能选同一段里的镜头':'';
  const count=$('[data-compare-selection-count]');if(count)count.textContent=`已选 ${drag.ids.length} 镜`;
}
function finishComparisonDrag(event,cancel=false){
  const drag=comparisonDrag;if(!drag || event.pointerId!==drag.pointer)return;
  comparisonDrag=null;drag.box?.remove();drag.grid.classList.remove('is-dragging');
  if(drag.active){if(storyboardPointerCandidate)storyboardPointerCandidate.marquee=true;if(storyboardPointerSnapshot)storyboardPointerSnapshot.marquee=true;comparisonSuppressClick=true;if(!cancel && drag.pid===P?.id){setComparisonCells(drag.sid,drag.ids);comparisonSelection().hint=drag.crossed?'一次只能选同一段里的镜头':'';}renderSteps();}
}
// V5 keeps preparation, confirmation and viewing state local to each project/segment.
const completedStepViews=new Map(),segmentAdjustments=new Map(),v5Actions=new Map(),comparisonViews=new Map(),v5Regenerate=new Set(),v5CancelledPreviews=new Map();
function v5PreviewScope(sid,cell){return JSON.stringify([P.id,sid,cell || null]);}
function renderCompletedSteps(){
  const rows=workflowState(),names=new Map((P.breakdown?.characters || []).map(c=>[c.id,c.name]));
  const actions={keep:'保留原人物',replace:'换成新人物',add:'新增人物',remove:'移除人物'};
  const characters=visibleCastingCharacters(P.replacements).map(c=>`${c.name || names.get(c.source_id) || c.source_id}：${actions[c.action] || '待设定'}`);
  if(P.replacements?.product)characters.push(`商品：${P.replacements.product.action==='replace'?'替换商品':'保留原商品'}`);
  const dialogue=(P.breakdown?.dialogue || []).some(row=>typeof row==='string'?row.trim():row.text || row.line || row.dialogue) || (P.storyboard?.cells || []).some(c=>c.dialogue?.trim());
  const summaries=[`${P.source?.name || ''} · ${fmt(P.source?.probe?.duration,1)} 秒 · ${P.source?.probe?.width || '—'}×${P.source?.probe?.height || '—'}`,`${P.storyboard?.cells?.length || 0} 镜 · ${dialogue?'有台词':'无台词'} · ${P.segments_plan?.summary || P.breakdown?.summary || P.breakdown?.viralLogic?.structure || P.breakdown?.viralLogic?.hook || ''}`,characters.join(' · ')];
  rows.slice(0,3).forEach((row,i)=>{
    const section=$('#step-'+(i+1)),header=section && $('header',section);if(!header)return;
    let summary=$('[data-step-summary]',header);if(!summary){summary=document.createElement('span');summary.setAttribute('data-step-summary',String(i+1));header.appendChild(summary);}
    const key=`${P.id}:${i+1}`,open=!row.done || completedStepViews.get(key)===true;
    section.setAttribute('class',[...(section.getAttribute('class') || '').split(' ').filter(c=>!['is-completed','is-collapsed'].includes(c)),row.done?'is-completed':'',open?'':'is-collapsed'].filter(Boolean).join(' '));
    updateHtml(summary,row.done?`<span class="step-complete">✓ 已完成</span><span class="step-summary-text">${esc(summaries[i])}</span><button class="ghost small" data-step-toggle="${i+1}" aria-expanded="${open}">${open?'收起':'展开'}</button>`:'');
  });
}
function v5PreparingOrConfirming(pid){return [...v5Actions].some(([key,state])=>key.startsWith(pid+':') && ['preparing','confirming'].includes(state.status));}
function v5VideoVersions(sid){return (P.runs?.[sid] || []).filter(r=>!['superseded','cancelled','pending_confirmation'].includes(r.state));}
function v5View(sid){
  const key=editSheetKey(sid),runs=v5VideoVersions(sid),latest=runs.filter(r=>r.state==='completed' && r.result).at(-1)?.rev,attempt=runs.at(-1)?.rev;
  let state=comparisonViews.get(key);
  if(!state){state={view:attempt?'video':'board',rev:attempt || null,lastCompleted:latest,lastAttempt:attempt};comparisonViews.set(key,state);}
  else {
    if(attempt && attempt!==state.lastAttempt){state.view='video';state.rev=attempt;state.lastAttempt=attempt;}
    if(latest && latest!==state.lastCompleted){state.view='video';state.rev=latest;state.lastCompleted=latest;v5Regenerate.delete(key);}
  }
  return state;
}

function v5CurrentRun(sid){
  const action=v5Actions.get(editSheetKey(sid));
  if(action?.kind==='video' && action.status==='running'){const pending=(P.runs?.[sid] || []).find(r=>r.rev===action.rec?.rev);if(pending?.state==='pending_confirmation')return {...pending,state:'submitting'};}
  const runs=v5VideoVersions(sid),view=v5View(sid);
  return runs.find(r=>r.rev===view.rev) || runs.at(-1);
}
function v5ActionState(sid){
  const key=editSheetKey(sid),state=v5Actions.get(key);
  if(state && ['preparing','confirming'].includes(state.status) && !validFixVideo(sid,state))v5Actions.delete(key);
  if(state?.status==='running'){
    const record=state.kind==='video'?(P.runs?.[sid] || []).find(r=>r.rev===state.rec?.rev):P.images?.[state.rec?.id];
    if(record && ['completed','failed','cancelled','superseded'].includes(record.state)){if(record.state==='completed' && state.kind!=='video')v5View(sid).view='board';v5Actions.delete(key);}
  }
  return v5Actions.get(key);
}
function v5CellGenerating(sid,cid){
  const flow=[...fixFlows.entries()].find(([key,value])=>key.startsWith(`${P.id}:${sid}:`)&&value.status==='running')?.[1];
  if(flow?.cell===cid && !['completed','failed'].includes(P.images?.[flow.imageId]?.state))return true;
  const state=v5Actions.get(editSheetKey(sid)),record=state?.rec && P.images?.[state.rec.id];
  if(state?.status==='running' && state.kind!=='video' && (state.cells?.length?state.cells.includes(cid):!state.cell || state.cell===cid) && !['completed','failed','cancelled'].includes(record?.state))return true;
  return Object.values(P.images || {}).some(r=>r.storyboard_context?.sheet_id===sid && ['running','submitting','query_pending'].includes(r.state) && (r.storyboard_context.selected_cells?.length?r.storyboard_context.selected_cells.includes(cid):!r.storyboard_context.cell_id || r.storyboard_context.cell_id===cid));
}
function v5HasBoard(sid){const board=editSheetState(sid);return !!board.versions?.some(v=>v.id===board.current);}
function v5ActionLabel(kind,sid,cell){
  if(kind==='video')return '生成视频';
  if(kind==='cell')return `重画第 ${Array.isArray(cell)?comparisonCellNumbers(cell):P.storyboard?.cells?.find(c=>c.id===cell)?.index || ''} 镜`;
  return v5HasBoard(sid) && !P.segment_board_status?.[sid]?.casting_stale?'整段重画':'生成新分镜';
}
function v5PreparationInputs(sid){
  const board=editSheetState(sid),current=board.versions?.find(v=>v.id===board.current);
  return JSON.stringify([castingDraft(),P.source,P.storyboard?.source_sig,P.storyboard?.cells,P.storyboard?.merges,P.segments_plan?.segments?.find(s=>s.id===sid),board.current,current]);
}
function v5Binding(kind,sid,cell,cells=null){
  return JSON.stringify([...(cells?.length>1?[cells]:[]),P.replacements,castingDraft(),dirty.rep,P.segments_plan?.segments?.find(s=>s.id===sid),P.storyboard?.source_sig,editSheetState(sid).current,editSheetState(sid).storyboard_sig,editSheetState(sid).versions?.find(v=>v.id===editSheetState(sid).current)?.resliced_at,editDraft(sid),kind==='video'?directorDraft().value?.segments?.find(s=>s.id===sid):null,cell,kind==='cell'?newStoryboardCellRequests.get(editSheetKey(sid)):null,kind==='cell'?editSheetState(sid).cell_revisions?.[cell]?.sig:null]);
}
function v5BoardPreview(sid,cell,cells=null){
  const key=editSheetKey(sid),board=editSheetState(sid),current=board.versions?.find(v=>v.id===board.current);
  if(newStoryboardInvalidPreviews.has(key))return null;
  return [newStoryboardPreviews.get(key),...Object.values(P.images || {}).reverse()].find(r=>r?.state==='pending_confirmation' && r.storyboard_context?.sheet_id===sid && (cells?.length>1?JSON.stringify(r.storyboard_context.selected_cells)===JSON.stringify(cells):!r.storyboard_context.selected_cells?.length && (r.storyboard_context.cell_id || null)===(cell || null)) && r.storyboard_context.plan_sig===board.plan?.sig && r.storyboard_context.storyboard_sig===board.storyboard_sig && (!cell || r.storyboard_context.base_current===current?.id && current.slicer===2 && (r.storyboard_context.base_resliced_at ?? null)===(current.resliced_at ?? null) && (cells?.length>1 || (r.storyboard_context.cell_revision_sig ?? null)===(board.cell_revisions?.[cell]?.sig ?? null))));
}
function v5ConfirmationText(sid,state){
  if(state.kind!=='video'){
    const subject=state.kind==='cell'?`${v5ActionLabel('cell',sid,state.cells?.length>1?state.cells:state.cell)}${state.cells?.length>1?'':state.request?.trim()?'（按你写的要求）':''}`:'生成这一段的新分镜';
    return `${subject}：1 张图，${imageFee(state.rec)}`;
  }
  const rec=state.rec,note=String(rec.billing_note || '').split('；')[0].replace(/(\d+(?:\.\d+)?)\s*s\b/g,'$1 秒');
  const duration=rec.preview?.tasks?.[0]?.duration;
  return `${state.fixPlan?'按方案重新生成这一段':'生成这一段的视频'}：计费约 ${Number(rec.billing_seconds)} 秒${note?'（'+note+'）':Number.isFinite(Number(duration))?'（输出 '+Number(duration)+' 秒）':''}`;
}
function v5InlineAction(sid,state){
  if(!state)return '';
  if(state.status==='preparing')return `<span class="action-progress" role="status">${state.kind==='cell'?(state.cells?.length>1?'正在整理这几镜的改法…':'正在整理这一镜的改法…'):'正在准备…'}</span>`;
  if(state.status==='confirming'){
    const revision=state.kind==='cell'?(state.rec.storyboard_context?.cells_revision || state.rec.storyboard_context?.cell_revision):null;
    return `<div class="inline-confirm" role="group" aria-label="付费确认">${revision?`<span>按你的要求：${esc(revision.why)}</span><details data-detail-key="cell-revision:${esc(sid)}:${esc(state.rec.id)}"><summary>展开完整改法</summary><p>${esc(revision.changes || revision.cells?.map(c=>c.cell+'：'+c.changes).join('；'))}</p>${revision.keep?`<p>${esc(revision.keep)}</p>`:''}</details>`:''}<span>${esc(v5ConfirmationText(sid,state))}${state.rec.warnings?.length?`<span class="action-error"> ${esc(joinDisplayText(state.rec.warnings.filter(w=>!/(过期|重新生成预览|选角或商品改过)/.test(w))))}</span>`:''}</span><button class="small pay" data-v5-confirm="${esc(sid)}">确认</button><button class="small ghost" data-v5-cancel="${esc(sid)}">取消</button></div>`;
  }
  if(state.status==='running')return `<span class="action-progress" role="status">${state.kind==='video'?'正在生成视频，约 2–4 分钟…':state.kind==='cell'?`正在${v5ActionLabel('cell',sid,state.cells?.length>1?state.cells:state.cell)}…`:'正在生成新分镜…'}</span>`;
  return '';
}
// Whole-project responses can arrive out of order while different segments work.
// If another response was applied meanwhile, read the latest snapshot before merging.
const projectResponseRevisions=new Map();
function noteProjectResponse(pid,context){
  const revision=(projectResponseRevisions.get(pid) || 0)+1;
  projectResponseRevisions.set(pid,revision);if(context)context.revision=revision;
}
async function mergeConcurrentProject(pid,next,context,current=()=>P?.id===pid){
  let revision=context.revision;
  while(current() && (projectResponseRevisions.get(pid) || 0)!==revision){
    revision=projectResponseRevisions.get(pid) || 0;
    next=await api('GET',`projects/${pid}`);
  }
  if(!current())return false;
  P={...P,...next};noteProjectResponse(pid,context);return true;
}
const segmentModeFeedback=new Map(),segmentModeErrors=new Map(),segmentModeCancelledVideos=new Set();
function rememberCancelledSegmentPreview(sid,kind,rec,cell){
  if(kind==='video'){
    if(rec?.rev)segmentModeCancelledVideos.add(`${editSheetKey(sid)}:${rec.rev}`);
  }else if(rec?.id){
    const scope=v5PreviewScope(sid,cell),cancelled=v5CancelledPreviews.get(scope) || new Set();
    cancelled.add(rec.id);v5CancelledPreviews.set(scope,cancelled);
  }
}
function segmentModeCanDismissPending(sid){
  const key=editSheetKey(sid),active=['preparing','confirming','running'];
  return segmentModeDisabledReason(sid)==='本段正在等待付费确认，请先确认或取消再切换。'
    && !active.includes(v5Actions.get(key)?.status)
    && ![...fixFlows].some(([scope,flow])=>scope.startsWith(key+':') && active.includes(flow.status));
}
function dismissSegmentModePending(sid){
  if(!segmentModeCanDismissPending(sid))return;
  for(const rec of P.runs?.[sid] || [])if(rec.state==='pending_confirmation')rememberCancelledSegmentPreview(sid,'video',rec);
  for(const rec of Object.values(P.images || {}))if(rec.state==='pending_confirmation' && rec.storyboard_context?.sheet_id===sid)rememberCancelledSegmentPreview(sid,'board',rec,rec.storyboard_context.cell_id);
  renderSteps();
}
function segmentModeDisabledReason(sid){
  if(dirty.rep)return '有未保存的选角，请先保存选角再切换。';
  if(segmentPlanBusy.has(P.id))return segmentPlanBusy.get(P.id)==='generate'?'正在生成分段方案，完成后再切换。':'正在保存本段设置，请稍候。';
  if(comparisonBatches.get(P.id)?.busy)return '正在批量处理，完成或取消后再切换。';
  const operation=directorOperations.get(P.id);
  if(directorBusy.has(P.id)){
    const index=P.segments_plan?.segments?.findIndex(s=>s.id===operation?.sid),subject=index>=0?`第 ${index+1} 段`:'全部段';
    // A prompt rewrite commits against the complete director snapshot, even for one segment.
    if(!operation || operation.kind!=='video')return `${subject}正在处理提示词，完成后再切换。`;
    if(operation.sid===sid)return `${subject}正在处理视频，完成后再切换。`;
  }
  const key=editSheetKey(sid),state=v5Actions.get(key);
  const record=state?.kind==='video'?(P.runs?.[sid] || []).find(r=>r.rev===state.rec?.rev):P.images?.[state?.rec?.id];
  const actions=state && !(state.status==='running' && ['completed','failed','cancelled','superseded'].includes(record?.state))?[state]:[];
  const flows=[...fixFlows].filter(([scope])=>scope.startsWith(key+':')).map(([,flow])=>flow);
  const images=Object.values(P.images || {}).filter(r=>r.storyboard_context?.sheet_id===sid),runs=P.runs?.[sid] || [];
  const pendingImage=images.some(r=>r.state==='pending_confirmation' && !v5CancelledPreviews.get(v5PreviewScope(sid,r.storyboard_context.cell_id))?.has(r.id));
  const pendingVideo=runs.some(r=>r.state==='pending_confirmation' && !segmentModeCancelledVideos.has(`${key}:${r.rev}`));
  if([...actions,...flows].some(s=>s.status==='confirming') || pendingImage || pendingVideo)return '本段正在等待付费确认，请先确认或取消再切换。';
  if([...actions,...flows].some(s=>s.status==='preparing'))return '本段正在准备付费预览，完成后再切换。';
  if(newStoryboardBusy.has(key))return '本段正在准备新分镜或改图方案，完成后再切换。';
  if([...fixPlanBusy].some(scope=>scope.startsWith(key+':')))return '本段正在生成修改方案，完成后再切换。';
  if(flows.some(s=>s.status==='running'))return '本段正在按方案修改，完成后再切换。';
  if(actions.some(s=>s.status==='running' && s.kind!=='video') || images.some(r=>['running','submitting','query_pending'].includes(r.state)))return '本段正在生成新分镜或等待结果，完成后再切换。';
  if(actions.some(s=>s.status==='running' && s.kind==='video') || runs.some(r=>['running','submitting','query_pending'].includes(r.state)))return '本段正在生成视频或等待结果，完成后再切换。';
  return '';
}
function segmentModeHtml(segment){
  const reason=segmentModeDisabledReason(segment.id),suggested=segment.mode_suggested || segment.mode;
  const warning=segment.mode===suggested?'':segment.mode==='video'?'原片里旧人或旧商品很显眼，可能漏回来；生成后点“检查视频”看一下。':'动作和特效只靠分镜还原，可能偏弱。';
  // The reference clip is stretched to the output duration before MINI_VIDEO billing.
  const duration=segment.duration,route=routeFor('video');
  const extra=route.model==='seedance2.0-mini-video' && Number.isInteger(duration) && duration>=4 && duration<=15?`约多 ${duration} 计费秒`:'计费更高';
  const withVideo=`带原片视频：动作、运镜和特效更像原片；原片里的旧人或旧商品可能漏回来；${extra}（参考视频的秒数另算）。`;
  const withoutVideo='不带原片视频：换人换货更干净；动作和特效只靠分镜和文字还原，会偏弱。';
  const feedback=segmentModeFeedback.get(editSheetKey(segment.id)),current=feedback?.mode===segment.mode && feedback?.generation===(P.segments_plan.generation_id || P.segments_plan.generated_at);
  return `<div class="segment-mode-control"><div class="segment-mode-switch" role="group" aria-label="本段是否带原片视频">${[['video','带原片视频',withVideo],['image','不带原片视频',withoutVideo]].map(([mode,label,tip])=>`<button type="button" class="small ${segment.mode===mode?'on':'ghost'}" data-segment-mode-choice="${mode}" data-segment="${esc(segment.id)}" aria-pressed="${segment.mode===mode}" title="${esc(tip+(mode===suggested && segment.mode_reason?' 推荐理由：'+segment.mode_reason:''))}" ${reason?'disabled':''}>${label}${mode===suggested?'<small class="segment-mode-recommended" data-mode-recommended>推荐</small>':''}</button>`).join('')}</div>${warning?`<span class="segment-mode-warning" data-segment-mode-warning>${warning}</span>`:''}</div>`+
    `${reason?`<span class="hint segment-mode-notice" data-segment-mode-disabled role="status">${esc(reason)}${segmentModeCanDismissPending(segment.id)?` <button class="small ghost" data-segment-mode-cancel-pending="${esc(segment.id)}">取消待确认</button>`:''}</span>`:''}${current?`<span class="segment-mode-notice" data-segment-mode-feedback role="status">${esc(feedback.message)}</span>`:''}${segmentModeErrors.has(editSheetKey(segment.id))?`<span class="segment-mode-notice action-error" data-segment-mode-error role="alert">保存未完成：${esc(segmentModeErrors.get(editSheetKey(segment.id)))}</span>`:''}`;
}
function renderSegmentBar(){
  const segments=P.segments_plan?.segments || [],selection=comparisonSelection(),segment=segments.find(s=>s.id===selection.sid),box=$('#segmentBar');if(!box)return;
  box.hidden=!segment;if(!segment){updateHtml(box,'');return;}
  const sid=segment.id,key=editSheetKey(sid),cell=selection.cell && P.storyboard?.cells?.find(c=>c.id===selection.cell),run=v5CurrentRun(sid),prompt=P.director?.segments?.find(s=>s.id===sid),view=v5View(sid);
  let state=v5ActionState(sid);
  const activeImage=Object.values(P.images || {}).findLast(r=>r.storyboard_context?.sheet_id===sid && ['running','submitting','query_pending'].includes(r.state));
  const busy=comparisonBatches.get(P.id)?.busy || directorBusy.has(P.id) || segmentPlanBusy.has(P.id) || newStoryboardBusy.has(key),disabled=busy?'disabled':'';
  const activeVideo=(P.runs?.[sid] || []).some(r=>['running','submitting','query_pending'].includes(r.state));
  const activeFix=[...fixFlows].some(([scope,flow])=>scope.startsWith(key+':') && ['preparing','confirming','running'].includes(flow.status)) || [...fixPlanBusy].some(scope=>scope.startsWith(key+':'));
  const status=P.segment_board_status?.[sid] || {};
  if(!v5Actions.has(key) && !busy && !activeImage && !activeVideo && !activeFix && !dirty.rep && !status.casting_stale && !status.plan_stale && !status.source_stale){
    const selectedCell=selection.cell || null,selectedCells=comparisonSelectedCells(),rec=v5BoardPreview(sid,selectedCell,selectedCells);
    if(rec && !v5CancelledPreviews.get(v5PreviewScope(sid,selectedCell))?.has(rec.id) && validFixVideo(sid,{rec})){
      const kind=selectedCell?'cell':'board';
      if(selectedCell)newStoryboardCellRequests.set(key,(rec.storyboard_context?.cells_revision || rec.storyboard_context?.cell_revision)?.revised_by_request || '');
      state={kind,status:'confirming',cell:selectedCell,...(selectedCells.length>1?{cells:selectedCells}:{}),request:selectedCell?newStoryboardCellRequests.get(key):null,rec,binding:v5Binding(kind,sid,selectedCell,selectedCells)};
      v5Actions.set(key,state);
    }
  }
  let action=state && state.kind!=='cell'?v5InlineAction(sid,state):fixInlineAction(sid);
  if(!action && run && ['running','submitting','query_pending'].includes(run.state))action='<span class="action-progress" role="status">正在生成视频，约 2–4 分钟…</span>';
  if(!action && activeImage && !cell)action=`<span class="action-progress" role="status">正在生成新分镜…</span>`;
  if(!action && cell)action=`<span class="hint">正在改第 ${comparisonCellNumbers(comparisonSelectedCells())} 镜（在下面写要改的地方）</span><button class="ghost small" data-compare-cancel-cell>取消选镜</button>`;
  if(!action && (!v5HasBoard(sid) || P.segment_board_status?.[sid]?.casting_stale))action=`<button data-edit-preview="${esc(sid)}" ${disabled}>生成新分镜</button>`;
  if(!action && (!run || run.state!=='completed' || comparisonNeedsPrompt(segment) || v5Regenerate.has(key)))action=`<button class="ghost" data-edit-preview="${esc(sid)}" ${disabled}>整段重画</button><button data-director-preview="${esc(sid)}" ${disabled}>生成视频</button>`;
  if(!action)action=`<button class="ghost" data-director-preview="${esc(sid)}" ${disabled}>再生成一版</button><button data-director-use="${esc(sid)}" data-rev="${esc(run.rev)}" ${disabled}>${prompt?.selected_run===run.rev?'✓ 已选用这一版':'选用这一版'}</button>`;
  const currentCells=segment.cells.map(id=>P.storyboard?.cells?.find(c=>c.id===id)).filter(Boolean);
  const error=state?.status==='failed'?state.error:comparisonBatches.get(P.id)?.errors?.[sid] || newStoryboardErrors.get(key) || ((directorErrors.get(P.id)?.segment_id || directorErrors.get(P.id)?.extra?.segment_id)===sid?publicText(directorErrors.get(P.id).message):'');
  const latestImage=Object.values(P.images || {}).findLast(r=>r.storyboard_context?.sheet_id===sid && !['pending_confirmation','superseded','cancelled'].includes(r.state)),failedImage=latestImage?.state==='failed'?latestImage:null,recoverImage=latestImage && ['failed','query_pending'].includes(latestImage.state)?latestImage:null;
  const failure=error || (failedImage?`生成失败：${publicText(failedImage.error || '请重试')}。请重新生成或查询结果。`:'');
  updateHtml(box,`<nav class="segment-tabs" aria-label="选择段落">${segments.map((s,i)=>`<button class="small ${s.id===sid?'on':''}" data-compare-segment-tab="${esc(s.id)}" ${comparisonSelectionLocked()?'disabled title="正在整理所选镜头的改法，完成后再切段"':''} aria-pressed="${s.id===sid}">第 ${i+1} 段${v5ChosenRun(s.id)?' ✓':''}${reviewWarn(s.id)?`<i class="review-warning-dot" data-review-warning="${esc(s.id)}" title="检查有问题或没完成"></i>`:''}</button>`).join('')}</nav><span class="segment-bar-title">${esc(segment.title)}</span><span class="segment-bar-meta">原片 ${Number(segment.source_start)}–${Number(segment.source_end)} 秒 · 第 ${currentCells[0]?.index || ''}–${currentCells.at(-1)?.index || ''} 镜</span>${segmentModeHtml(segment)}${run?`<span class="comparison-views"><button class="small ${view.view==='board'?'on':''}" data-compare-view="board" aria-pressed="${view.view==='board'}">分镜对照</button><button class="small ${view.view==='video'?'on':''}" data-compare-view="video" aria-pressed="${view.view==='video'}">视频对照</button></span>`:''}<div id="segmentAction" class="segment-action">${action}${failure?`<span class="action-error" role="alert">${errorHtml(failedImage?.error || failure,'生图')}</span>${failedImage?.channel==='official'?`<button class="small ghost" data-edit-third-retry="${esc(sid)}">改用三方渠道重试</button>`:''}`:''}${recoverImage?`<button class="small ghost" data-edit-resume="${esc(recoverImage.id)}">重新查询结果</button>`:''}</div>${dirty.rep?`<button class="casting-unsaved ghost" data-casting-save-focus>${esc(CASTING_UNSAVED_MESSAGE)}</button>`:''}`);
  for(const select of $$('[data-segment-mode]')){
    const row=segments.find(s=>s.id===select.dataset.segmentMode);
    select.disabled=!!segmentModeDisabledReason(select.dataset.segmentMode);if(row)select.value=row.mode;
  }
}
function v5ChosenRun(sid){
  const segment=P.segments_plan?.segments?.find(s=>s.id===sid),prompt=P.director?.segments?.find(s=>s.id===sid);
  if(!segment || !prompt || directorLegacy(P.director) || !['cells','source_start','source_end'].every(k=>JSON.stringify(segment[k])===JSON.stringify(prompt[k])))return null;
  return (P.runs?.[sid] || []).find(r=>r.rev===prompt.selected_run && r.state==='completed' && r.result);
}
function renderSegmentFoot(){
  const selection=comparisonSelection(),s=P.segments_plan?.segments?.find(s=>s.id===selection.sid),foot=$('#segmentFoot'),editor=$('#segmentCellEditor');if(!foot || !editor)return;
  if(!s){updateHtml(foot,'');updateHtml(editor,'');return;}
  const key=editSheetKey(s.id),selectedCells=comparisonSelectedCells(),numbers=comparisonCellNumbers(selectedCells),cell=P.storyboard?.cells?.find(c=>c.id===selection.cell),expanded=segmentAdjustments.get(key)===true;
  $('#segmentAdvanced').hidden=!expanded || !!cell;foot.hidden=!!cell;editor.hidden=!cell;
  updateHtml(foot,`<b>本段脚本：</b><span>${displayTextHtml(P.director?.segments?.find(row=>row.id===s.id)?.what || s.what || '')}</span><button class="ghost small" data-segment-adjust="${esc(s.id)}" aria-expanded="${expanded}">${expanded?'收起':'调整这一段'}</button>`);
  if(!cell){updateHtml(editor,'');return;}
  const board=editSheetState(s.id),revision=board.cell_revisions?.[cell.id],status=P.segment_board_status?.[s.id] || {};
  const currentRevision=revision && revision.base_plan_sig===board.plan?.sig && revision.storyboard_sig===board.storyboard_sig && revision.settings_sig===board.settings_sig && !status.plan_stale && !status.casting_stale && !status.source_stale?revision:null;
  const changes=currentRevision?.changes || editDraft(s.id).plan?.cells?.find(c=>c.cell===cell.id)?.changes || '暂无改图方案',candidate=v5ActionState(s.id),state=candidate?.cell===cell.id?candidate:null,action=state?.kind==='cell'?v5InlineAction(s.id,state):'',busy=['preparing','running','confirming'].includes(candidate?.status) || newStoryboardBusy.has(key),disabled=busy?'disabled':'';
  updateHtml(editor,`<section class="edit-cell-detail" data-edit-cell-detail><div class="cell-editor-title"><b>第 ${numbers} 镜要改什么</b><span class="hint">${selectedCells.length>1?'只重画这几镜，其他镜不动。':'只重画这一镜，其他镜不动。'}写清你想改的地方，模型按你的意思改。</span></div><div class="cell-editor-input"><textarea rows="2" data-edit-cell-request="${esc(s.id)}" aria-label="第 ${numbers} 镜要改什么" ${disabled}>${esc(newStoryboardCellRequests.get(key) || '')}</textarea><button class="small ghost" data-edit-mic="${esc(s.id)}" data-edit-mic-cell="${esc(cell.id)}" ${disabled}>${storyboardRecorder?.key===key?'■ 结束录音':'语音输入'}</button><div class="cell-action">${action || `<button data-edit-cell-preview="${esc(s.id)}" ${disabled}>重画第 ${numbers} 镜</button>`}${state?.kind==='cell' && state.status==='failed'?`<span class="action-error" role="alert">${esc(state.error)}</span>`:''}</div></div><p class="hint">这一镜当前的改图方案：${esc(Array.from(changes).slice(0,60).join(''))}${Array.from(changes).length>60?'…':''} <button class="small ghost" data-cell-plan="${esc(s.id)}">展开完整方案</button></p><details data-detail-key="cell-plan:${esc(s.id)}:${esc(cell.id)}"><summary>完整方案</summary><p>${esc(changes)}</p></details></section>`);
}
function v5VideoHtml(s){
  const run=v5CurrentRun(s.id),busy=directorBusy.has(P.id) || comparisonBatches.get(P.id)?.busy,prompt=P.director?.segments?.find(row=>row.id===s.id),versions=v5VideoVersions(s.id);
  if(!run)return '<p class="video-state hint">生成后，这里直接显示这一段的视频。</p>';
  return `<article class="director-card" data-director-generation="${esc(s.id)}"><section class="director-run" data-director-run="${esc(s.id)}:${esc(run.rev)}">${run.result?`<div class="review-player"><video controls playsinline preload="metadata" src="${fileUrl(run.result)}"></video>${reviewMarks(s.id,run)}</div>`:`<div class="video-state" role="status">${['running','submitting','query_pending'].includes(run.state)?'正在生成视频，约 2–4 分钟…':run.state==='failed'?errorHtml(run.error || '生成失败，请刷新或重试','视频生成'):'提交结果未知，请恢复任务'}</div>`}${queryRetryHtml(run)}${run.result && run.error?`<p class="action-error">${errorHtml(run.error,'视频生成')}</p>`:''}<nav class="director-version-bar" aria-label="视频版本">${versions.map((r,i)=>`<button class="small ghost ${run.rev===r.rev?'selected':''}" data-compare-version="${esc(r.rev)}" data-segment="${esc(s.id)}" aria-pressed="${run.rev===r.rev}">第 ${i+1} 版 · ${r.state==='completed'?reviewLabel(r.review,reviewState(r.review,directorReviewRunning(s.id,r),reviewError(s.id,r)),true):({running:'生成中',submitting:'提交中',query_pending:'等待查询',failed:'生成失败',unknown:'提交结果未知'}[r.state] || r.state)}${prompt?.selected_run===r.rev?' · 已选用':''}</button>`).join('')}</nav><div class="row wrap">${['running','submitting','failed','query_pending'].includes(run.state)?`<button class="small ghost" data-director-resume="${esc(s.id)}" data-rev="${esc(run.rev)}" ${busy?'disabled':''}>${run.state==='query_pending'?'再查一次':'刷新/恢复'}</button>`:''}${run.state==='unknown' || run.state==='failed'&&!run.remote_task_id?`<button class="small ghost" data-director-recover="${esc(s.id)}" data-rev="${esc(run.rev)}" ${busy?'disabled':''}>填写远端任务 ID 恢复</button>`:''}</div></section></article>`;
}
async function prepareSegmentAction(sid,kind,attempt=0,fixContext=null,channel=null){
  const pid=P.id,key=editSheetKey(sid),current=v5ActionState(sid);
  if(fixContext && !validFixVideo(sid,{fixContext}))return;
  if(['preparing','running'].includes(current?.status) || comparisonBatches.get(pid)?.busy || directorBusy.has(pid) || newStoryboardBusy.has(key))return;
  if(warnUnsavedCasting())return;
  const cells=kind==='cell'?comparisonSelectedCells().slice():[],cell=kind==='cell'?comparisonSelection().cell:null,request=kind==='cell'?(newStoryboardCellRequests.get(key) || ''):null;
  if(kind==='cell' && !cell)return;
  if(kind!=='video')v5CancelledPreviews.delete(v5PreviewScope(sid,cell));
  const state={kind,status:'preparing',cell,request,...(cells.length>1?{cells}:{}),...(fixContext?{fixPlan:true,fixContext}:{})};v5Actions.set(key,state);newStoryboardErrors.delete(key);directorErrors.delete(pid);renderSteps();
  const assertReady=()=>{if(P?.id!==pid)return false;if(newStoryboardErrors.has(key))throw Error(newStoryboardErrors.get(key));if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);return true;};
  try{
    if(!assertReady())return;
    const preparedInputs=v5PreparationInputs(sid);let preparedPrompt=null;
    if(kind!=='video')v5View(sid).view='board';
    if(kind==='video'){
      const s=P.segments_plan?.segments?.find(row=>row.id===sid);
      if(!comparisonBoardReady(sid))throw Error('请先生成这一段的新分镜');
      if(directorDraft().dirty){await directorAction('save');if(!assertReady())return;}
      if(comparisonNeedsPrompt(s))await directorAction('rewrite',{segment_id:sid});
      if(!assertReady())return;
      preparedPrompt=JSON.stringify(directorDraft().value?.segments?.find(s=>s.id===sid));
      await directorVideoAction('preview',sid,fixContext?{fix_plan:fixContext}:{});if(!assertReady())return;
      state.rec=P.runs?.[sid]?.findLast(r=>r.state==='pending_confirmation');
      if(!state.rec || !Number.isFinite(Number(state.rec.billing_seconds)))throw Error('没有得到有效计费预览');
    }else{
      const draft=editDraft(sid),saved=editSheetState(sid),status=P.segment_board_status?.[sid] || {};
      const changed=draft.request!==(saved.request || '') || Object.keys(draft.materials).some(k=>draft.materials[k]!==({...Object.fromEntries(editMaterials(sid).map(m=>[m.key,m.enabled])),...(P.segment_board_status?.[sid]?.active_materials || saved.materials)})[k]);
      if(!draft.plan || status.plan_stale || status.casting_stale || status.source_stale || status.warnings?.some(w=>w.startsWith('选角或商品')) || changed){await performStoryboardEdit(sid,'plan');if(!assertReady())return;}
      state.rec=kind==='cell' || channel?null:v5BoardPreview(sid,cell);
      if(!state.rec){await performStoryboardEdit(sid,kind==='cell'?'cell-preview':'preview',{}, {cellInput:cells.length>1?{cells,request}:{cell_id:cell,request},...(channel?{channel}:{})});if(!assertReady())return;state.rec=newStoryboardPreviews.get(key);}
      if(!state.rec)throw Error('没有得到预览');
    }
    if(cells.length>1 && (comparisonSelection().sid!==sid || JSON.stringify(comparisonSelectedCells())!==JSON.stringify(cells))){
      rememberCancelledSegmentPreview(sid,kind,state.rec,cell);state.status='failed';state.error='选中的镜头已变化，这次没有进入付费确认。请按当前选择重新点重画。';return;
    }
    if((fixContext || state.rec?.fix_plan_binding) && (v5Actions.get(key)!==state || !validFixVideo(sid,state))){if(v5Actions.get(key)===state)v5Actions.delete(key);return;}
    if(dirty.rep || v5PreparationInputs(sid)!==preparedInputs || kind==='cell' && (newStoryboardCellRequests.get(key) || '')!==request || kind==='video' && JSON.stringify(directorDraft().value?.segments?.find(s=>s.id===sid))!==preparedPrompt){
      newStoryboardPreviews.delete(key);newStoryboardInvalidPreviews.add(key);v5Actions.delete(key);
      if(attempt<2 && comparisonSelection().sid===sid && (kind!=='cell' || comparisonSelection().cell===cell))return await prepareSegmentAction(sid,kind,attempt+1,fixContext);
      v5Actions.set(key,state);throw Error('准备期间本段内容又有修改，请重试');
    }
    state.binding=v5Binding(kind,sid,cell,state.cells);state.status='confirming';
  }catch(error){state.status='failed';state.error=`准备失败：${publicText(error.message)}。点“${v5ActionLabel(kind,sid,cell)}”重试。`;}
  finally{if(P?.id===pid)renderSteps();else if(state.status==='preparing')v5Actions.delete(key);}
}
async function confirmSegmentAction(sid){
  const pid=P.id,key=editSheetKey(sid),state=v5ActionState(sid);if(state?.status!=='confirming')return;
  if(!validFixVideo(sid,state)){v5Actions.delete(key);renderSteps();return;}
  if(state.binding!==v5Binding(state.kind,sid,state.cell,state.cells) || state.kind!=='video' && newStoryboardInvalidPreviews.has(key)){v5Actions.delete(key);return prepareSegmentAction(sid,state.kind);}
  state.status='running';renderSteps();
  try{
    if(state.kind==='video'){
      const view=v5View(sid);view.view='video';view.rev=state.rec.rev;
      await directorVideoAction('confirm',sid,{rev:state.rec.rev,job_sha256:state.rec.job_sha256},{confirmed:true});
      if(P?.id!==pid)return;if(directorErrors.has(pid))throw Error(directorErrors.get(pid).message);
      if(state.fixContext && v5Actions.get(key)===state)v5Actions.delete(key);
    }else{
      await performStoryboardEdit(sid,'confirm',{image_id:state.rec.id},{confirmed:true});
      if(P?.id!==pid)return;if(newStoryboardErrors.has(key))throw Error(newStoryboardErrors.get(key));
    }
  }catch(error){state.status='failed';state.error=`生成失败：${publicText(error.message)}。请重试或刷新结果。`;}
  finally{if(P?.id===pid)renderSteps();}
}

// Trace the perimeter of the union of occupied grid squares. Shared edges are
// removed, leaving one closed L/staircase outline even when a segment wraps rows.
function comparisonOutline(indices){
  const occupied=[...new Set(indices)].sort((a,b)=>a-b),rects=occupied.map(index=>[index%3*100,Math.floor(index/3)*100,(index%3+1)*100,(Math.floor(index/3)+1)*100]);
  // A short segment can wrap with no vertical overlap (c03 → c04). Connect its
  // pieces through the gutter, never through another shot's image.
  const rows=[...new Set(occupied.map(index=>Math.floor(index/3)))];
  for(const row of rows){
    const above=rects.filter(r=>r[1]===row*100),below=rects.filter(r=>r[1]===(row+1)*100);
    if(!below.length || above.some(a=>below.some(b=>a[0]<b[2] && b[0]<a[2])))continue;
    const left=Math.max(...below.map(r=>r[2])),right=Math.min(...above.map(r=>r[0])),y=(row+1)*100;
    if(left<=right)rects.push([left-1,y-1,right+1,y+1]);
  }
  const xs=[...new Set(rects.flatMap(r=>[r[0],r[2]]))].sort((a,b)=>a-b),ys=[...new Set(rects.flatMap(r=>[r[1],r[3]]))].sort((a,b)=>a-b),edges=new Map();
  for(let xi=0;xi<xs.length-1;xi++)for(let yi=0;yi<ys.length-1;yi++){
    const x=(xs[xi]+xs[xi+1])/2,y=(ys[yi]+ys[yi+1])/2;
    if(!rects.some(r=>x>r[0] && x<r[2] && y>r[1] && y<r[3]))continue;
    const points=[[xs[xi],ys[yi]],[xs[xi+1],ys[yi]],[xs[xi+1],ys[yi+1]],[xs[xi],ys[yi+1]]];
    points.forEach((a,i)=>{const b=points[(i+1)%4],key=a+':'+b,reverse=b+':'+a;if(edges.has(reverse))edges.delete(reverse);else edges.set(key,[a,b]);});
  }
  let path='';
  while(edges.size){
    const [key,[start,next]]=edges.entries().next().value;edges.delete(key);path+=`M${start[0]} ${start[1]}L${next[0]} ${next[1]}`;
    let cursor=next;
    while(String(cursor)!==String(start)){
      const edge=[...edges.entries()].find(([,pair])=>String(pair[0])===String(cursor));if(!edge)break;
      edges.delete(edge[0]);cursor=edge[1][1];path+=`L${cursor[0]} ${cursor[1]}`;
    }
    path+='Z';
  }
  return path;
}
// A comparison playback session owns its listeners and pending play() calls.
// Nothing here changes project state or submits work to the server.
let comparisonPlayback=null;
const comparisonPlayOwners=new WeakMap();
function comparisonVideoSegment(sid,run){
  return run?.review_inputs?.segment || P.segments_plan?.segments?.find(s=>s.id===sid) || P.director?.segments?.find(s=>s.id===sid);
}
function comparisonVideoTiming(segment,video){
  const start=Number(segment?.source_start),end=Number(segment?.source_end);
  const actual=Number(video?.duration),planned=Number(segment?.duration);
  const duration=Number.isFinite(actual) && actual>0?actual:Number.isFinite(planned) && planned>0?planned:0;
  return {start,end,duration,ratio:duration>0 && Number.isFinite(start) && Number.isFinite(end) && end>start?(end-start)/duration:0};
}
function comparisonPlaybackRate(video,desired){
  const requested=Number.isFinite(desired) && desired>0?desired:1;
  try{video.playbackRate=requested;return video.playbackRate;}catch(error){
    // Engines support different ranges. Find the nearest supported boundary
    // between normal speed and the rejected rate instead of letting it throw.
    video.playbackRate=1;let accepted=1,rejected=requested;
    // Bracket the boundary near 1 first, even for a very large desired ratio.
    for(let candidate=requested>1?2:.5;requested>1?candidate<requested:candidate>requested;candidate=requested>1?candidate*2:candidate/2){
      try{video.playbackRate=candidate;accepted=candidate;}catch(error){rejected=candidate;break;}
    }
    for(let i=0;i<16;i++){
      const candidate=(accepted+rejected)/2;
      try{video.playbackRate=candidate;accepted=candidate;}catch(error){rejected=candidate;}
    }
    video.playbackRate=accepted;return accepted;
  }
}
function stopComparisonPlayback(message=''){
  const session=comparisonPlayback;if(!session)return;
  comparisonPlayback=null;clearTimeout(session.timer);
  for(const [video,event,listener] of session.listeners)video.removeEventListener(event,listener);
  for(const video of session.videos)video.pause?.();
  session.source.muted=session.muted;
  for(const [video,rate] of session.rates)try{video.playbackRate=rate;}catch(error){}
  session.button.textContent='同步播放';session.button.setAttribute('aria-pressed','false');
  if(session.status)session.status.textContent=message;
}
function toggleComparisonPlayback(){
  if(comparisonPlayback){stopComparisonPlayback();return;}
  const sid=comparisonSelection().sid,run=sid && v5CurrentRun(sid),segment=comparisonVideoSegment(sid,run);
  const button=$('[data-comparison-sync]'),source=$('[data-video-side="source"] video'),target=$('[data-video-side="target"] video');
  if(!button || button.disabled || v5View(sid).view!=='video' || run?.state!=='completed' || !run.result || !source || !target)return;
  const timing=comparisonVideoTiming(segment,target);if(!timing.ratio)return;
  const session={source,target,button,status:$('[data-comparison-sync-status]'),videos:[source,target],segment,listeners:[],seeks:new Map(),waitingMetadata:new Set([source,target].filter(video=>video.readyState===0)),ended:new Set(),muted:source.muted,rates:[[source,source.playbackRate],[target,target.playbackRate]]};
  comparisonPlayback=session;button.textContent='暂停';button.setAttribute('aria-pressed','true');
  if(session.status)session.status.textContent='';
  const active=()=>comparisonPlayback===session;
  const listen=(video,event,listener)=>{video.addEventListener(event,listener);session.listeners.push([video,event,listener]);};
  const fail=()=>{if(active())stopComparisonPlayback('播放没能开始，请再试一次。');};
  const finish=video=>{
    if(!active() || session.ended.has(video))return;
    session.ended.add(video);session.seeks.delete(video);video.pause();
    if(video===source && source.currentTime>timing.end){session.seeks.set(source,{time:timing.end,at:Date.now(),seen:false});source.currentTime=timing.end;}
    if(session.ended.size===2)stopComparisonPlayback();
  };
  const atEnd=video=>video===source?video.currentTime>=timing.end:video.currentTime>=comparisonVideoTiming(segment,target).duration;
  const check=()=>{
    if(!active())return;
    for(const video of session.videos)if(atEnd(video))finish(video);
  };
  for(const video of session.videos){
    video.pause();
    listen(video,'timeupdate',check);
    listen(video,'ended',()=>{if(video.ended || atEnd(video))finish(video);});
    listen(video,'pause',()=>{
      // pause events queued before a restart must not stop the new playback.
      if(!active() || !video.paused || session.ended.has(video))return;
      if(atEnd(video))finish(video);else stopComparisonPlayback();
    });
    listen(video,'seeking',()=>{
      if(!active())return;
      const expected=session.seeks.get(video);
      // Setting currentTime queues seeking asynchronously. Allow only the
      // expected initial position (plus time actually played), never a drag.
      const elapsed=expected?Math.max(0,Date.now()-expected.at)/1000:0;
      if(expected && (elapsed<1 || session.waitingMetadata.has(video)) && Math.abs(video.currentTime-expected.time)<=.05+Math.min(elapsed,.15)*(video.paused?0:video.playbackRate)){expected.seen=true;return;}
      stopComparisonPlayback();
    });
    listen(video,'seeked',()=>{const expected=session.seeks.get(video);if(expected?.seen)expected.finished=true;});
    listen(video,'loadedmetadata',()=>{
      if(!active() || !session.waitingMetadata.delete(video))return;
      // HAVE_NOTHING stores currentTime without emitting a seek. Reapply the
      // initial position after a slow metadata load and renew only that seek.
      const time=video===source?timing.start:0;
      try{session.seeks.set(video,{time,at:Date.now(),seen:false});video.currentTime=time;}catch(error){fail();}
    });
    listen(video,'error',fail);
  }
  const updateRate=()=>{if(active())try{comparisonPlaybackRate(source,comparisonVideoTiming(segment,target).ratio || timing.ratio);}catch(error){fail();}};
  listen(target,'loadedmetadata',updateRate);listen(target,'durationchange',updateRate);
  try{
    source.muted=true;target.playbackRate=1;updateRate();if(!active())return;
    for(const [video,time] of [[source,timing.start],[target,0]]){
      session.seeks.set(video,{time,at:Date.now(),seen:false});video.currentTime=time;
    }
    for(const video of session.videos){
      comparisonPlayOwners.set(video,session);
      Promise.resolve(video.play()).then(()=>{
        // A stopped play promise can settle after navigation. Pause that old
        // element, but never pause a newer session that reused the same node.
        if(!active() && comparisonPlayOwners.get(video)===session)video.pause();
      },fail);
    }
    const tick=()=>{
      // Drain queued seek events before retiring the initial seek allowance;
      // an older seeked event can arrive during an immediate stop/restart.
      for(const [video,expected] of session.seeks)if(!session.waitingMetadata.has(video) && !video.seeking && (expected.finished || Date.now()-expected.at>1000))session.seeks.delete(video);
      check();if(active())session.timer=setTimeout(tick,40);
    };
    session.timer=setTimeout(tick,40);
  }catch(error){fail();}
}
function renderComparison(){
  stopComparisonPlayback();
  for(const video of $$('#comparisonVideos video'))video.pause?.();
  const box=$('#comparisonView');if(!box)return;
  const segments=P.segments_plan?.segments || [],selection=comparisonSelection(),cells=[...(P.storyboard?.cells || [])].sort((a,b)=>a.index-b.index),skip=new Set(P.segments_plan?.skip_cells || []);
  const segment=segments.find(s=>s.id===selection.sid),view=segment?v5View(segment.id):{view:'board'},run=segment && v5CurrentRun(segment.id),videoSegment=segment && comparisonVideoSegment(segment.id,run);
  const landscape=(P.source?.probe?.width || 0)>(P.source?.probe?.height || 1),rows=Math.ceil(cells.length/3);
  const preparing=P.derived_from?.master_snapshot?.boards?.[selection.sid] && !v5HasBoard(selection.sid);
  // Both columns start at the same height: no per-column titles (left = original, right = new is obvious);
  // the selection status sits in one row above both columns.
  const status=`<div class="comparison-selection-status"><span data-compare-selection-count>${selection.cell?'已选 '+comparisonSelectedCells().length+' 镜':''}</span><span data-compare-selection-hint role="status">${esc(comparisonSelectionLocked()?'正在整理所选镜头的改法，完成后再选。':selection.hint || '')}</span>${preparing?'<span data-compare-preparing>右边先用母版的新分镜做底，正在准备这一段。</span>':''}${comparisonTouch() && selection.cell?`<button class="small ghost" data-compare-touch-add ${comparisonSelectionLocked()?'disabled title="正在整理改法，完成后再选"':''} aria-pressed="${!!selection.touchAdd}">再选一镜${selection.touchAdd?' · 已开启':''}</button>`:''}</div>`;
  const column=(side)=>`<section data-compare-side="${side}" aria-label="${side==='source'?'原片分镜':'新分镜'}"><div class="comparison-board ${landscape?'landscape':''}"><div class="comparison-grid">${cells.map(c=>{
    const owner=segments.find(s=>s.cells.includes(c.id)),index=segments.indexOf(owner),selected=owner?.id===selection.sid,skipped=skip.has(c.id),state=owner && comparisonSegmentState(owner),file=side==='source'?(c.file?storyboardFile(c.file):null):directorCellFile(c.id),generating=side==='target' && owner && v5CellGenerating(owner.id,c.id),oldCasting=side==='target' && file && P.segment_board_status?.[owner?.id]?.casting_stale;
    return `<div class="comparison-preview-cell" data-story-preview-holder><button type="button" class="compare-cell ${skipped?'is-skipped':selected?'is-selected':selection.sid?'is-muted':''} ${side==='target' && comparisonSelectedCells().includes(c.id)?'is-cell':''}" data-compare-key="${side}:${esc(c.id)}" data-compare-cell="${esc(c.id)}" data-compare-segment="${esc(owner?.id || '')}" data-compare-column="${side}" aria-pressed="${!!selected && !skipped}" aria-label="${owner?'第 '+(index+1)+' 段，':''}第 ${c.index} 镜，${side==='source'?'原片':'新画面'}${skipped?'，不复刻':''}" ${comparisonSelectionLocked() || skipped || !owner || side==='target' && (!file || generating)?'disabled':''} title="${comparisonSelectionLocked()?'正在整理所选镜头的改法，完成后再选':skipped?'这一镜不复刻':generating?'正在生成，完成后再选择':!file && side==='target'?'先生成这一段的新分镜':''}">
      <span class="comparison-thumb">${file?`<img draggable="false" src="${fileUrl(file)}" alt="${side==='source'?'原片':'新画面'}第 ${c.index} 镜" loading="lazy">`:'<span class="comparison-placeholder">待生成</span>'}${generating?'<span class="comparison-generating" role="status">生成中…</span>':''}
      ${owner?.cells[0]===c.id?`<span class="segment-badge ${oldCasting?'with-casting-badge':''}"><i class="segment-dot ${state.color}" title="${state.label}"></i>第 ${index+1} 段</span>`:''}${oldCasting?'<span class="casting-stale-badge" data-casting-stale>按旧选角生成</span>':''}${skipped?'<span class="skip-badge">不复刻</span>':''}</span>
      <span class="comparison-caption">第 ${c.index} 镜 <small>${fmt(c.start,1)}–${fmt(c.end,1)} 秒</small></span></button>${file?storyboardPreviewMagnifier(c.id,side):''}</div>`;
  }).join('')}</div><svg class="comparison-outline" viewBox="0 0 300 ${rows*100}" preserveAspectRatio="none" aria-hidden="true">${segments.map(s=>`<path data-segment-outline="${esc(s.id)}" class="${s.id===selection.sid?'is-selected':''}" d="${comparisonOutline(cells.flatMap((c,i)=>s.cells.includes(c.id) && !skip.has(c.id)?[i]:[]))}" vector-effect="non-scaling-stroke"></path>`).join('')}</svg></div></section>`;
  updateHtml(box,`<div id="comparisonBoards" ${view.view==='video'?'hidden':''}>${cells.length?`<div class="comparison-legend" aria-label="分段状态"><span><i class="segment-dot empty"></i>待生成新分镜</span><span><i class="segment-dot ready"></i>待生成视频</span><span><i class="segment-dot done"></i>已选定可用版本</span><span><i class="segment-dot warning"></i>有提示</span></div>${status}<div class="comparison-columns">${column('source')}${column('target')}</div>`:'<p class="hint">先在第 02 步生成原片分镜</p>'}</div><div class="row wrap" id="comparisonPlaybackControls" ${view.view==='video'?'':'hidden'}><button class="small" data-comparison-sync aria-pressed="false" ${run?.state==='completed' && run.result && P.source?.file?'':'disabled'}>同步播放</button><span class="hint" data-comparison-sync-status role="status"></span></div><div id="comparisonVideos" class="comparison-columns video-comparison" ${view.view==='video'?'':'hidden'}><section data-video-side="source" aria-label="原片">${segment && P.source?.file?`<video controls muted playsinline preload="metadata" src="${fileUrl(P.source.file)}#t=${Number(videoSegment.source_start)},${Number(videoSegment.source_end)}"></video>`:''}</section><section data-video-side="target" aria-label="生成的视频"><div id="directorGeneration">${segment?v5VideoHtml(segment):'<p class="hint">先完成上面的提示词</p>'}</div></section></div><div id="segmentReviewCard">${segment?directorReviewHtml(segment.id,v5CurrentRun(segment.id)):''}</div>`);
  if($('#selectedSegment'))$('#selectedSegment').hidden=!segment;
  renderSegmentBar();renderSegmentFoot();
  if(!segment){updateHtml($('#selectedSegmentHeader'),'');return;}
  const busy=segmentPlanBusy.has(P.id) || comparisonBatches.get(P.id)?.busy || v5PreparingOrConfirming(P.id);
  updateHtml($('#selectedSegmentHeader'),`<h3>第 ${segments.indexOf(segment)+1} 段 · ${esc(segment.title)}</h3><p class="hint">${esc(directorTimes(segment))} · ${esc(segment.mode_reason || '')}</p>${segmentSettingsHtml(segment)}<details data-detail-key="segment-summary"><summary>为什么是 ${segments.length} 段</summary><p>${esc(P.segments_plan.summary || '')}</p><p>${esc(P.segments_plan.why_segments || '')}</p>${directorNotices(P.segments_plan.questions)}<button class="small ghost" data-segment-generate ${busy?'disabled':''}>${segmentPlanBusy.get(P.id)==='generate'?'正在重新分段…':busy?'正在处理…':'重新生成分段方案'}</button></details>`);
}

function segmentSettingsHtml(s){
  const busy=!!segmentModeDisabledReason(s.id),photos=editMaterials(s.id);
  return `<details data-detail-key="segment-plan:${esc(s.id)}"><summary>本段生成方式和素材</summary><p>${displayTextHtml(s.what || '')}</p><label>生成方式 <select data-segment-mode="${esc(s.id)}" ${busy?'disabled':''}><option value="video" ${s.mode==='video'?'selected':''}>原片视频 + 目标画面和照片</option><option value="image" ${s.mode==='image'?'selected':''}>只用目标画面和照片</option></select></label><p class="hint">人物和商品照片按第 03 步最新保存的设定自动选取；可在下方调整新分镜的照片开关。视频提交的目标画面和照片合计最多 4 张。</p><p class="hint" data-segment-photos="${esc(s.id)}">本段照片：${photos.map(m=>esc(m.name || m.label)).join('、') || '无'}</p></details>`;
}

async function segmentPlanAction(action,sid,change={}){
  const pid=P.id,modeOnly=action==='save' && Object.keys(change).length===1 && 'mode' in change;
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  if(segmentPlanBusy.has(pid) || comparisonBatches.get(pid)?.busy || !modeOnly && v5PreparingOrConfirming(pid))return;
  if(action==='save' && 'mode' in change && segmentModeDisabledReason(sid))return;
  const previousMode=P.segments_plan?.segments?.find(s=>s.id===sid)?.mode;
  if(action==='generate' && warnUnsavedCasting())return;
  if(modeOnly){segmentModeErrors.delete(`${pid}:${sid}`);segmentModeFeedback.delete(`${pid}:${sid}`);}
  segmentPlanConfirmation=null;segmentPlanBusy.set(pid,action);segmentPlanErrors.delete(pid);renderSteps();
  try{
    const next=await api(action==='generate'?'POST':'PUT',`projects/${pid}/segment-plan${sid?'/'+sid:''}`,{action,...change});
    if(P?.id!==pid)return;if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;
    if(modeOnly){
      const segment=P.segments_plan?.segments?.find(s=>s.id===sid);
      if(segment && segment.mode!==previousMode){
        segmentModeFeedback.set(`${pid}:${sid}`,{mode:segment.mode,generation:P.segments_plan.generation_id || P.segments_plan.generated_at,message:`已改成${segment.mode==='video'?'带':'不带'}原片视频：已生成的新分镜照用；下次点“生成视频”时会先重写这一段的提示词（会调用推理）。已生成的视频保留。`});
      }
      return;
    }
    const affected=sid?[`${pid}:${sid}`]:[...newStoryboardDrafts.keys()].filter(key=>key.startsWith(pid+':'));
    for(const key of affected){newStoryboardDrafts.delete(key);newStoryboardPreviews.delete(key);newStoryboardInvalidPreviews.add(key);}
    for(const key of segmentModeFeedback.keys())if(key.startsWith(pid+':') && (!sid || key===`${pid}:${sid}`))segmentModeFeedback.delete(key);
  }catch(error){segmentPlanErrors.set(pid,publicText(error.message));if(modeOnly)segmentModeErrors.set(`${pid}:${sid}`,publicText(error.message));}
  finally{segmentPlanBusy.delete(pid);if(P?.id===pid)renderSteps();}
}

// ---------- selected segment results (step 08) ----------
const finalPreviewBusy=new Set(),finalPreviewErrors=new Map();
function finalPreviewStatus(){
  if(P.final_preview_status)return P.final_preview_status;
  const segments=P.segments_plan?.segments || [],warnings=[],reviewWarnings=[];
  for(const [i,s] of segments.entries()){
    const prompt=P.director?.segments?.find(r=>r.id===s.id);
    const bound=prompt && !directorLegacy(P.director) && ['cells','source_start','source_end'].every(k=>JSON.stringify(prompt[k])===JSON.stringify(s[k]));
    const chosen=bound?prompt.selected_run:null;
    const run=chosen && (P.runs?.[s.id] || []).find(r=>r.rev===chosen && r.state==='completed' && r.result);
    if(!run)warnings.push(`第 ${i+1} 段还没有选定的结果，在第 04 步选中这一段，在“视频”里生成或选定一版。`);
    const high=run?.review?.verdict==='fix'?issuesByTime(run.review.issues).map(row=>row.issue).filter(issue=>issue.level==='high'):[];
    if(high.length && !run?.review?.accepted)reviewWarnings.push(`第 ${i+1} 段选用的版本检查有问题：${joinDisplayText(high.map(issue=>issue.what))}，在第 04 步选中这一段，在“视频”里处理，或确认接受后再拼接。`);
  }
  if(!segments.length)warnings.push('先在第 04 步生成分段方案。');
  return {...P.final_preview,ready:segments.length>0 && !warnings.length,warnings,review_warnings:reviewWarnings,stale:!!P.final_preview && warnings.length>0};
}
function renderFinalPreview(){
  const box=$('#finalPreviewView');if(!box)return;const status=finalPreviewStatus(),busy=finalPreviewBusy.has(P.id);
  const segments=P.segments_plan?.segments || [],selected=segments.filter(s=>v5ChosenRun(s.id)).length;
  updateHtml(box,`<p class="final-selected-count">已选定 ${selected}/${segments.length} 段</p>${directorNotices(status.warnings)}${directorNotices(status.review_warnings)}<button data-final-preview ${busy || !status.ready || selected!==segments.length?'disabled':''}>${busy?'正在拼接…':'拼成整片预览'}</button>
    ${finalPreviewErrors.has(P.id)?directorNotices([finalPreviewErrors.get(P.id)]):''}
    ${status.file?`${status.stale?'<p class="caption-warning">选用版本改过，请重新拼接预览。</p>':''}<div class="final-check-layout"><div><video controls preload="metadata" src="${fileUrl(status.file)}"></video><p>时长 ${fmt(status.duration)} 秒</p></div>${finalCheckHtml()}</div>`:''}
    <div class="fission-entry"><button data-fission-open ${status.ready?'':'disabled'}>批量裂变</button><button class="ghost" data-batch-board="${esc(P.id)}">查看批量看板</button><span class="hint">${status.ready?'用这条片子当母版，换一组人物或商品，批量出新片。':esc((status.warnings || []).join('；'))}</span></div>`);
}
async function generateFinalPreview(){
  const pid=P.id;if(finalPreviewBusy.has(pid) || !finalPreviewStatus().ready || !(P.segments_plan?.segments || []).every(s=>v5ChosenRun(s.id)))return;
  finalPreviewBusy.add(pid);finalPreviewErrors.delete(pid);renderFinalPreview();
  try{const next=await api('POST',`projects/${pid}/final-preview`,{});if(P?.id===pid)P={...P,...next};}
  catch(error){finalPreviewErrors.set(pid,publicText(error.message));}
  finally{finalPreviewBusy.delete(pid);if(P?.id===pid)renderSteps();}
}

// ---------- director plan and its frozen video previews (steps 5–6) ----------
// ---------- manual video checks and review-bound correction plans ----------
const reviewDrafts=new Map(),fixPlanBusy=new Set(),fixPlanErrors=new Map(),fixFlows=new Map(),reviewInvalidations=new Map();
const finalCheckBusy=new Map(),finalCheckErrors=new Map();
let reviewRecorder=null;
function reviewDraft(sid,run){
  const key=directorReviewKey(sid,run.rev),signature=JSON.stringify([run.review?.request_id,run.review?.generated_at,run.review?.issues]);
  let draft=reviewDrafts.get(key);
  if(!draft || draft.signature!==signature){draft={signature,issues:run.fix_plan?.issues?.slice() || (run.review?.issues || []).flatMap((i,k)=>i.level==='high'?[k]:[]),note:draft?.note ?? run.fix_plan?.note ?? '',invalid:false};reviewDrafts.set(key,draft);}
  return draft;
}
function reviewState(review,running=false,error=null){
  if(running)return 'running';
  if(error || review?.status==='failed' || review?.status==='running')return 'failed';
  if(review?.accepted)return 'accepted';
  return review?.status==='completed'?(review.verdict==='pass'?'pass':'fix'):'unchecked';
}
function reviewLabel(review,state,version=false){
  return {unchecked:version?'未检查':'还没检查',running:'检查中',failed:'没检查完',pass:version?'检查通过':'通过',fix:`${version?'检查有':'发现'} ${(review?.issues || []).length} 个问题`,accepted:version?'已接受':'你已接受'}[state];
}
function reviewFailureReason(value){return publicText(value || '上次检查中断').replace(/(?:自动|重新)(?:验收|检查)/g,'检查视频').replace(/（免费）/g,'');}
function reviewTechnicalDetails(review,state,key){
  if(state!=='failed' || !review?.error_detail)return '';
  const detail=review.error_detail,content=typeof detail==='object'?JSON.stringify(Object.fromEntries(['http_status','error_type','message','image_count','request_bytes'].filter(k=>detail[k]!==undefined).map(k=>[k,detail[k]])),null,2):String(detail);
  return `<details class="review-technical" data-detail-key="review-technical:${esc(key)}"><summary>技术信息</summary><pre>${esc(content)}</pre></details>`;
}
function reviewError(sid,run){
  const key=directorReviewKey(sid,run.rev),error=directorReviewErrors.get(key);
  if(error && error.snapshot!==directorReviewSnapshot(run.review)){directorReviewErrors.delete(key);return null;}return error;
}
function reviewWarn(sid){const run=v5ChosenRun(sid) || v5CurrentRun(sid);if(!run)return false;return ['fix','failed'].includes(reviewState(run.review,directorReviewRunning(sid,run),reviewError(sid,run)));}
function fixPlanFor(sid,run){
  const plan=run?.fix_plan;if(!plan)return null;
  const started=plan.id && (P.runs?.[sid] || []).find(r=>r.fix_plan_binding?.id===plan.id && ['submitting','running','query_pending','completed','failed','unknown'].includes(r.state));
  if(started || plan.status==='video_started')return {...plan,status:'video_started',video_rev:started?.rev || plan.video_rev};
  if(['stale','cancelled'].includes(plan.status))return null;
  const draft=reviewDraft(sid,run),board=editSheetState(sid).current;
  if(draft.invalid || JSON.stringify(draft.issues)!==JSON.stringify(plan.issues) || draft.note.trim()!==(plan.note || '').trim())return null;
  if((plan.expected_board || plan.base_board)!==board)return null;
  return plan;
}
function fixCellNames(plan){return (plan.redraw || []).map(r=>P.storyboard?.cells?.find(c=>c.id===r.cell)?.index || r.cell).join('、');}
function reviewMarks(sid,run){
  const duration=Number(run.review?.duration || run.review_inputs?.segment?.duration || P.director?.segments?.find(s=>s.id===sid)?.duration);
  if(!duration || !run.review?.issues?.length)return '';
  return `<div class="review-marks" aria-label="橙色是有问题的时间段">${run.review.issues.map(i=>`<i data-review-mark style="left:${Math.max(0,Math.min(100,Number(i.start)/duration*100))}%;width:${Math.max(0,Math.min(duration,Number(i.end))-Math.max(0,Number(i.start)))/duration*100}%"></i>`).join('')}</div>`;
}
function fixPlanHtml(sid,run,plan){
  const key=directorReviewKey(sid,run.rev),locked=fixPlanBusy.has(key)||['running','preparing','confirming'].includes(fixFlows.get(key)?.status),disabled=locked?'disabled':'';
  if(plan.status==='video_started'){
    const number=v5VideoVersions(sid).findIndex(r=>r.rev===plan.video_rev)+1;
    return `<section class="fix-plan" data-fix-plan-view><p class="hint">${number?`已按方案生成第 ${number} 版`:'已按方案生成新版本'}${number?` <button class="small ghost" data-compare-version="${esc(plan.video_rev)}" data-segment="${esc(sid)}">去看第 ${number} 版 →</button>`:''}</p><button class="small ghost" data-fix-plan="${esc(sid)}" data-rev="${esc(run.rev)}" ${disabled}>重新出方案</button></section>`;
  }
  let n=0;const step=(title,body)=>`<div class="fix-step"><span class="fix-number">${++n}</span><div><b>${title}</b>${body}</div></div>`;
  const redraw=plan.redraw || [],why=redraw.map(r=>`<p class="hint">原因：${displayTextHtml(r.why)}</p><p class="hint">怎么改：${displayTextHtml(r.request)}</p>`).join('');
  return `<section class="fix-plan" data-fix-plan-view><h4>修改方案</h4><p class="hint">${displayTextHtml(plan.why)}</p>${redraw.length?step(`先重画第 ${esc(fixCellNames(plan))} 镜`,why+`<div class="fix-thumbs">${redraw.map(r=>directorCellFile(r.cell)?`<button class="story-preview-history" data-story-preview-cell="${esc(r.cell)}" data-story-preview-side="target" aria-label="看大图"><img src="${fileUrl(directorCellFile(r.cell))}" alt="第 ${esc(P.storyboard?.cells?.find(c=>c.id===r.cell)?.index || r.cell)} 镜（当前）"></button>`:'').join('')}</div>`):''}${step('改提示词',`<p class="hint">只改下面这几句，其他不动：</p>${(plan.prompt_changes || []).map(c=>`<div class="prompt-change" data-prompt-change>${c.before?`<del>${esc(c.before)}</del>`:''}${c.after?`<ins>${esc(c.after)}</ins>`:''}</div>`).join('') || '<p class="hint">提示词无需改动。</p>'}<p class="hint">完整提示词可以在“调整这一段”里看。</p>`)}${step('重新生成这一段视频','<p class="hint">生成完点“检查视频”，看问题是否修好。</p>')}<div class="fix-plan-foot"><span class="hint">共要花：重画 ${redraw.length} 张图 + 视频约 ${Number(plan.video_seconds)} 秒。${redraw.length?'重画完会先停下来让你看一眼新分镜，满意再生成视频。':'生成前会显示本次视频计费确认。'}</span><button class="small ghost" data-fix-plan="${esc(sid)}" data-rev="${esc(run.rev)}" ${disabled}>重新出方案</button><button data-fix-apply="${esc(sid)}" data-rev="${esc(run.rev)}" ${disabled}>按方案修改</button></div></section>`;
}
function directorReviewHtml(sid,run){
  if(run?.state!=='completed' || !run.result)return '';
  const key=directorReviewKey(sid,run.rev),review=run.review,error=reviewError(sid,run),running=directorReviewRunning(sid,run),state=reviewState(review,running,error),draft=reviewDraft(sid,run),plan=fixPlanFor(sid,run),flow=fixFlows.get(key),busy=fixPlanBusy.has(key) || ['running','preparing','confirming'].includes(flow?.status);
  const check=`<button class="small" data-director-review="${esc(sid)}" data-rev="${esc(run.rev)}" ${running || busy?'disabled':''}>检查视频</button>`;
  if(plan?.status==='ready_video' && plan.redraw.length)return `<section class="vcheck" data-review-card="${esc(key)}"><div class="check-head"><b>修改进度</b><span class="check-pill pass">第 ${esc(fixCellNames(plan))} 镜已重画</span></div><p class="check-copy">看一下右边新画的第 ${esc(fixCellNames(plan))} 镜。没问题就点上面的“按方案生成视频”，提示词已按方案改好。不满意可以点那一镜再单独重画。</p></section>`;
  const copy={unchecked:'看完视频觉得需要，就点“检查视频”。会逐秒对照原片和人物、商品照片，找出没换干净、剧情不通、动作不对的地方，大约 1 分钟。',running:'正在逐秒对照原片和人物、商品照片检查，大约 1 分钟…',failed:`这次检查没完成（原因：${reviewFailureReason(error?.message || review?.error_reason || review?.error)}），视频本身没受影响。点“检查视频”再查一次。`,pass:'人和商品都换干净了，剧情和镜头顺序对得上原片。可以点上面的“选用这一版”。',accepted:'检查查出的问题你选择不改，这一段之后不再提醒。'}[state];
  const editable=!running && (state==='fix' || state==='pass' || draft.note);
  return `<section class="vcheck" data-review-card="${esc(key)}"><div class="check-head"><b>视频检查</b><span class="check-pill ${state}">${reviewLabel(review,state)}</span>${state==='fix'?'<span class="hint">点时间可以跳到那一秒看</span>':''}<span class="spacer"></span>${review?.accepted?`<button class="small ghost" data-review-accept="${esc(sid)}" data-rev="${esc(run.rev)}" data-accepted="false">撤销</button>`:''}${check}</div>${copy?`<p class="check-copy" role="status">${displayTextHtml(copy)}</p>`:''}${reviewTechnicalDetails(review,state,key)}${state==='fix'?`<ul class="review-issues">${issuesByTime(review.issues).map(({issue:i,index:k})=>`<li class="review-issue ${draft.issues.includes(k)?'':'off'}"><input type="checkbox" data-review-issue="${k}" data-sid="${esc(sid)}" data-rev="${esc(run.rev)}" aria-label="处理问题 ${k+1}" ${draft.issues.includes(k)?'checked':''} ${busy?'disabled':''}><button class="time-chip" data-review-seek="${Number(i.start)}" data-sid="${esc(sid)}" data-rev="${esc(run.rev)}">${fmt(i.start,1)}–${fmt(i.end,1)} 秒</button><p><span class="issue-level ${i.level==='high'?'high':'low'}">${i.level==='high'?'要改':'轻微'}</span>${displayTextHtml(i.what)}</p></li>`).join('')}</ul>`:''}${editable?`<div class="review-ask"><textarea data-review-note="${esc(sid)}" data-rev="${esc(run.rev)}" rows="1" aria-label="补充意见" placeholder="还有检查没提到、你想改的地方，可以写在这里（可不填）" ${busy?'disabled':''}>${esc(draft.note)}</textarea><div class="review-ask-actions"><button class="small ghost" data-review-mic="${esc(sid)}" data-rev="${esc(run.rev)}" ${busy?'disabled':''}>${reviewRecorder?.key===key?'■ 结束录音':'语音输入'}</button>${!plan?`<button data-fix-plan="${esc(sid)}" data-rev="${esc(run.rev)}" ${busy || !draft.issues.length&&!draft.note.trim()?'disabled':''}>${fixPlanBusy.has(key)?'正在出方案…':`针对勾选的 ${draft.issues.length} 个问题出修改方案`}</button>`:''}</div></div>`:''}${fixPlanErrors.has(key)?`<p class="check-copy action-error" role="alert">${esc(fixPlanErrors.get(key))}</p>`:''}${plan && !running?fixPlanHtml(sid,run,plan):''}${state==='fix'?`<p class="check-copy review-accept">都不想改？<button class="small ghost" data-review-accept="${esc(sid)}" data-rev="${esc(run.rev)}" data-accepted="true" ${busy?'disabled':''}>不改，接受这一版</button></p>`:''}</section>`;
}
function seekReview(sid,rev,time){
  stopComparisonPlayback();
  if(v5View(sid).view!=='video'){v5View(sid).view='video';renderComparison();}
  const run=P.runs?.[sid]?.find(r=>r.rev===rev),segment=comparisonVideoSegment(sid,run);
  const gen=$('[data-video-side="target"] video'),src=$('[data-video-side="source"] video');
  if(gen){gen.currentTime=Number(time);gen.pause?.();}
  if(src && segment){const timing=comparisonVideoTiming(segment,gen);src.currentTime=Math.max(timing.start,Math.min(timing.end,timing.start+Number(time)*timing.ratio));src.pause?.();}
}
function changeReviewInput(sid,rev,change){
  const pid=P.id,run=P.runs?.[sid]?.find(r=>r.rev===rev);if(!run)return;
  const key=directorReviewKey(sid,rev),draft=reviewDraft(sid,run);Object.assign(draft,change);draft.invalid=true;fixFlows.delete(key);
  const action=v5Actions.get(editSheetKey(sid));if(action?.fixContext?.rev===rev)v5Actions.delete(editSheetKey(sid));
  if(run.fix_plan){
    const body={action:'invalidate',plan_id:run.fix_plan.id,issues:draft.issues.slice(),note:draft.note},previous=reviewInvalidations.get(key) || Promise.resolve();
    const pending=previous.catch(()=>{}).then(()=>api('PUT',`projects/${pid}/runs/${sid}/${rev}/fix-plan`,body)).catch(error=>{fixPlanErrors.set(key,publicText(error.message));}).finally(()=>{if(reviewInvalidations.get(key)===pending)reviewInvalidations.delete(key);});reviewInvalidations.set(key,pending);
  }
  renderComparison();
}
async function makeFixPlan(sid,rev){
  const pid=P.id,key=directorReviewKey(sid,rev),run=P.runs?.[sid]?.find(r=>r.rev===rev),draft=reviewDraft(sid,run);if(fixPlanBusy.has(key) || ['running','preparing','confirming'].includes(fixFlows.get(key)?.status) || !draft.issues.length&&!draft.note.trim())return;
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  const body={issues:draft.issues.slice(),note:draft.note},binding=JSON.stringify(body);fixPlanBusy.add(key);fixPlanErrors.delete(key);renderComparison();
  try{await reviewInvalidations.get(key);const next=await api('POST',`projects/${pid}/runs/${sid}/${rev}/fix-plan`,body);if(P?.id!==pid)return;
    if(JSON.stringify({issues:draft.issues,note:draft.note})!==binding){await api('PUT',`projects/${pid}/runs/${sid}/${rev}/fix-plan`,{action:'invalidate',issues:draft.issues,note:draft.note});return;}
    if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;draft.invalid=false;
  }catch(error){fixPlanErrors.set(key,publicText(error.message));}
  finally{fixPlanBusy.delete(key);if(P?.id===pid)renderSteps();}
}
async function acceptReview(sid,rev,accepted){
  const pid=P.id,key=directorReviewKey(sid,rev);if(fixPlanBusy.has(key))return;fixPlanBusy.add(key);
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  try{const next=await api('POST',`projects/${pid}/runs/${sid}/${rev}/review/accept`,{accepted});if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;}
  catch(error){fixPlanErrors.set(key,publicText(error.message));}
  finally{fixPlanBusy.delete(key);if(P?.id===pid)renderSteps();}
}
function fixInlineAction(sid){
  const run=v5CurrentRun(sid),plan=run && fixPlanFor(sid,run);if(!plan || plan.status==='video_started')return '';
  const key=directorReviewKey(sid,run.rev),flow=fixFlows.get(key),attrs=`data-rev="${esc(run.rev)}"`,names=fixCellNames(plan);
  if(flow?.status==='confirming'){const left=plan.redraw.length-(plan.completed_cells || []).length,rec=P.images?.[flow.imageId];return `<span class="inline-confirm" role="group" aria-label="付费确认"><span>按方案重画第 ${esc(names)} 镜：${left} 张图，${esc(left>1?imageBatchFee(Array(left).fill(rec)):imageFee(rec))}</span><button class="small pay" data-fix-confirm="${esc(sid)}" ${attrs}>确认</button><button class="small ghost" data-fix-cancel="${esc(sid)}" ${attrs}>取消</button></span>`;}
  if(['running','preparing'].includes(flow?.status))return `<span class="action-progress" role="status">正在重画第 ${esc(names)} 镜…</span>`;
  if(plan.status==='ready_video')return `<button data-fix-video="${esc(sid)}" ${attrs}>按方案生成视频</button>`;
  if(flow?.status==='failed' || ['redrawing','failed'].includes(plan.status))return `<span class="action-error">${esc(flow?.error || plan.error_reason || '重画尚未完成，已完成的镜头保留。')}</span><button class="small" data-fix-apply="${esc(sid)}" ${attrs}>继续按方案重画</button><button class="small ghost" data-fix-cancel="${esc(sid)}" ${attrs}>取消</button>`;
  return '';
}
async function applyFixPlan(sid,rev,confirmed=false){
  const pid=P.id,key=directorReviewKey(sid,rev),run=P.runs?.[sid]?.find(r=>r.rev===rev),plan=fixPlanFor(sid,run);if(!plan || plan.status==='video_started' || ['running','preparing'].includes(fixFlows.get(key)?.status) || warnUnsavedCasting())return;
  if(confirmed){
    const flow=fixFlows.get(key),rec=flow?.imageId && P.images?.[flow.imageId];
    if(flow?.status!=='confirming' || flow.id!==plan.id || rec?.state!=='pending_confirmation')return;
    // One confirmation covers the whole plan; later cells must keep the same frozen model and quality.
    flow.status='running';flow.approved=imageRecordInfo(rec);renderSteps();
    try{await api('POST',`projects/${pid}/images/${rec.id}/confirm`,{});if(P?.id!==pid || fixFlows.get(key)!==flow)return;rec.state='running';noteProjectResponse(pid);await refresh();}
    catch(error){flow.status='failed';flow.error=publicText(error.message);}
    finally{if(P?.id===pid){renderSteps();schedulePoll();}}
    return;
  }
  if(fixFlows.get(key)?.status==='confirming')return;
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  const flow={status:'preparing',rev,id:plan.id};fixFlows.set(key,flow);renderSteps();
  try{const next=await api('POST',`projects/${pid}/runs/${sid}/${rev}/fix-plan/apply`,{plan_id:plan.id});if(P?.id!==pid)return;if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;directorDrafts.delete(pid);newStoryboardDrafts.delete(editSheetKey(sid));
    if(!plan.redraw.length || plan.status==='ready_video'){fixFlows.delete(key);await prepareFixVideo(sid,rev);return;}
    flow.status='running';v5View(sid).view='board';comparisonSelection().cell=null;newStoryboardCells.delete(editSheetKey(sid));await advanceFix(sid,run.rev);
  }catch(error){flow.status='failed';flow.error=publicText(error.message);}
  finally{if(P?.id===pid)renderSteps();}
}
async function advanceFix(sid,rev){
  const pid=P.id,key=directorReviewKey(sid,rev),flow=fixFlows.get(key);if(!flow || flow.status!=='running' || flow.advancing)return;
  const run=P.runs?.[sid]?.find(r=>r.rev===rev),plan=fixPlanFor(sid,run);if(!plan || plan.status==='video_started'){fixFlows.delete(key);return;}
  if(plan.status==='ready_video'){fixFlows.delete(key);v5View(sid).view='board';renderSteps();return;}
  const image=flow.imageId && P.images?.[flow.imageId];
  if(image && !['completed','failed','cancelled','superseded'].includes(image.state))return;
  if(image && image.state!=='completed'){flow.status='failed';flow.error=publicText(plan.error_reason || image.error || '这一镜重画没完成，可重试或取消。');renderSteps();return;}
  const cell=plan.redraw.find(r=>!(plan.completed_cells || []).includes(r.cell));if(!cell)return;
  flow.advancing=true;flow.cell=cell.cell;
  try{
    const rec=await api('POST',`projects/${pid}/storyboard-edits/${sid}/preview`,{cell_id:cell.cell,request:cell.request,fix_plan:{rev,id:plan.id}});if(P?.id!==pid || fixFlows.get(key)!==flow)return;
    P.images ||= {};P.images[rec.id]=rec;noteProjectResponse(pid);flow.imageId=rec.id;
    if(!flow.approved){flow.status='confirming';renderSteps();$('#segmentBar')?.scrollIntoView({block:'start',behavior:'smooth'});return;}
    const now=imageRecordInfo(rec),before=flow.approved;
    if(now.channel!==before.channel || now.model!==before.model || now.quality!==before.quality){
      flow.approved=null;throw Error(`重画途中生图设置改了（确认时是 ${imageModelAnnotation({preview:before}) || before.channel}，现在是 ${imageModelAnnotation(rec) || now.channel}），已停下，已画好的镜头保留。点“继续按方案重画”，按现在的设置重新确认。`);
    }
    renderSteps();
    await api('POST',`projects/${pid}/images/${rec.id}/confirm`,{});if(P?.id!==pid)return;P.images[rec.id].state='running';noteProjectResponse(pid);await refresh();
  }catch(error){flow.status='failed';flow.error=publicText(error.message);}
  finally{flow.advancing=false;if(P?.id===pid){renderSteps();schedulePoll();}}
}
function resumeActiveFixes(){
  const pid=P.id;
  for(const [sid,runs] of Object.entries(P.runs || {}))for(const run of runs){
    const flow=fixFlows.get(directorReviewKey(sid,run.rev));
    if(flow?.status==='running'&&!flow.advancing){
      const image=flow.imageId && P.images?.[flow.imageId];
      if(!image || ['completed','failed','cancelled'].includes(image.state))Promise.resolve().then(()=>{if(P?.id===pid)return advanceFix(sid,run.rev);});
    }
  }
}
async function cancelFixPlan(sid,rev){
  const pid=P.id,key=directorReviewKey(sid,rev),flow=fixFlows.get(key),plan=P.runs?.[sid]?.find(r=>r.rev===rev)?.fix_plan;fixFlows.delete(key);
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  try{const next=await api('PUT',`projects/${pid}/runs/${sid}/${rev}/fix-plan`,{action:'cancel',plan_id:plan?.id});if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;}catch(error){fixPlanErrors.set(key,publicText(error.message));}finally{if(P?.id===pid)renderSteps();}
}
function validFixVideo(sid,state){
  const context=state.fixContext || state.rec?.fix_plan_binding;if(!context)return true;
  const run=P.runs?.[sid]?.find(r=>r.rev===context.rev),plan=run && fixPlanFor(sid,run);
  return !!plan && plan.id===context.id && plan.status==='ready_video';
}
async function prepareFixVideo(sid,rev){
  const pid=P.id,run=P.runs?.[sid]?.find(r=>r.rev===rev),plan=run && fixPlanFor(sid,run);
  if(!plan || plan.status!=='ready_video')return;
  fixFlows.delete(directorReviewKey(sid,rev));await prepareSegmentAction(sid,'video',0,{rev,id:plan.id});
  if(P?.id===pid)renderSteps();
}
async function toggleReviewMic(sid,rev){
  const pid=P.id,key=directorReviewKey(sid,rev);if(reviewRecorder){if(reviewRecorder.key===key)reviewRecorder.recorder.stop();return;}
  let stream;
  try{if(typeof MediaRecorder==='undefined'||!navigator.mediaDevices?.getUserMedia)throw Error('浏览器不支持录音');stream=await navigator.mediaDevices.getUserMedia({audio:true});if(P?.id!==pid){stream.getTracks().forEach(t=>t.stop());return;}
    const chunks=[],recorder=new MediaRecorder(stream);reviewRecorder={key,recorder};recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    recorder.onerror=()=>{stream.getTracks().forEach(t=>t.stop());reviewRecorder=null;fixPlanErrors.set(key,'录音没完成，请再试一次');if(P?.id===pid)renderComparison();};
    recorder.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());reviewRecorder=null;try{const blob=new Blob(chunks,{type:recorder.mimeType||'audio/webm'});if(!blob.size)throw Error('没有录到声音');const value=await api('POST',`projects/${pid}/voice-to-text`,blob);if(P?.id!==pid)return;const run=P.runs?.[sid]?.find(r=>r.rev===rev),draft=reviewDraft(sid,run);changeReviewInput(sid,rev,{note:draft.note+(draft.note?'\n':'')+value.text});}catch(error){fixPlanErrors.set(key,'录音没能转成文字：'+publicText(error.message));}finally{if(P?.id===pid)renderComparison();}};recorder.start();renderComparison();
  }catch(error){stream?.getTracks().forEach(t=>t.stop());fixPlanErrors.set(key,'无法使用麦克风，请允许录音权限，或直接打字：'+publicText(error.message));if(P?.id===pid)renderComparison();}
}
function finalCheckHtml(){
  const saved=P.final_preview,review=saved?.check;if(!saved?.file)return '';
  let error=finalCheckErrors.get(P.id);
  if(error && (error.file!==saved.file || error.snapshot!==directorReviewSnapshot(review))){finalCheckErrors.delete(P.id);error=null;}
  const running=finalCheckBusy.get(P.id)===saved.file || review?.status==='running' && P.jobs?.['final-check']?.state==='running',state=reviewState(review,running,error);
  const copy={unchecked:'每段自己的问题在第 04 步查。这里只看拼起来以后的事：段和段之间人和商品前后是不是同一个、接缝是不是突兀、整片剧情是否连得上。需要时点“检查视频”。',running:'正在看段与段的接缝…',pass:'段和段之间人和商品保持一致，接缝和剧情连得上。',failed:`这次检查没完成（原因：${publicText(error?.message || review?.error_reason || '上次检查中断')}），视频本身没受影响。点“检查视频”再查一次。`,accepted:'检查查出的问题你选择不改。'}[state];
  return `<section class="vcheck" data-final-check-card><div class="check-head"><b>整片检查</b><span class="check-pill ${state}">${reviewLabel(review,state)}</span><span class="spacer"></span>${review?.accepted?'<button class="small ghost" data-final-accept="false">撤销</button>':''}<button class="small" data-final-check ${running?'disabled':''}>检查视频</button></div>${copy?`<p class="check-copy" role="status">${displayTextHtml(copy)}</p>`:''}${reviewTechnicalDetails(review,state,'final:'+saved.file)}${state==='fix'?`<ul class="review-issues">${issuesByTime(review.issues).map(({issue:i,index:k})=>`<li class="review-issue final-issue"><button class="time-chip" data-final-seek="${Number(i.start)}">${fmt(i.start,1)}–${fmt(i.end,1)} 秒</button><p><span class="issue-level ${i.level==='high'?'high':'low'}">${i.level==='high'?'要改':'轻微'}</span>${displayTextHtml(i.what)}</p><button class="small ghost" data-final-issue-segment="${esc(i.segment)}" data-issue="${k}">去第 ${(P.segments_plan?.segments || []).findIndex(s=>s.id===i.segment)+1} 段处理 →</button></li>`).join('')}</ul><p class="check-copy">点“去第 N 段处理”，这个问题会带到那一段的补充意见里。<button class="small ghost" data-final-accept="true">不改，就用这一版</button></p>`:''}</section>`;
}
async function checkFinal(accepted){
  const pid=P.id,file=P.final_preview?.file,snapshot=directorReviewSnapshot(P.final_preview?.check);if(!file || finalCheckBusy.has(pid))return;finalCheckBusy.set(pid,file);finalCheckErrors.delete(pid);renderFinalPreview();
  try{const next=await api('POST',`projects/${pid}/final-preview/check${accepted===undefined?'':'/accept'}`,{file,...(accepted===undefined?{}:{accepted})});if(P?.id===pid && P.final_preview?.file===file)P={...P,...next};}
  catch(error){finalCheckErrors.set(pid,{message:publicText(error.message),file,snapshot});}
  finally{finalCheckBusy.delete(pid);if(P?.id===pid){renderFinalPreview();schedulePoll();}}
}
function handleFinalIssue(sid,index){
  const issue=P.final_preview?.check?.issues?.[index];if(!issue)return;
  const selection=comparisonSelection();selection.sid=sid;selection.cell=null;const view=v5View(sid),chosen=v5ChosenRun(sid);if(chosen)view.rev=chosen.rev;view.view='video';
  const run=v5CurrentRun(sid);if(run){const draft=reviewDraft(sid,run);changeReviewInput(sid,run.rev,{note:draft.note+(draft.note?'\n':'')+issue.what});}
  renderSteps();$('#step-4')?.scrollIntoView({behavior:'smooth',block:'start'});
}

const directorDrafts=new Map(),directorBusy=new Set(),directorOperations=new Map(),directorErrors=new Map(),directorSaveResults=new Map();
const directorReviewBusy=new Set(),directorReviewErrors=new Map(),directorRewriteResults=new Map();
function directorReviewKey(id,rev){return `${P.id}:${id}:${rev}`;}
function directorReviewRunning(id,run){
  return directorReviewBusy.has(directorReviewKey(id,run.rev)) || P.jobs?.[`review:${id}:${run.rev}`]?.state==='running';
}
function directorReviewSnapshot(review){
  return JSON.stringify(review ?? null,(_,value)=>value && typeof value==='object' && !Array.isArray(value)?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b))):value);
}
async function reviewDirectorRun(id,rev){
  const pid=P.id,key=directorReviewKey(id,rev),run=P.runs?.[id]?.find(r=>r.rev===rev);
  if(run?.state!=='completed' || directorReviewRunning(id,run))return;
  const snapshot=directorReviewSnapshot(run.review);
  directorReviewBusy.add(key);directorReviewErrors.delete(key);renderDirectorGeneration();
  try{const next=await api('POST',`projects/${pid}/runs/${id}/${rev}/review`,{});if(P?.id===pid)P={...P,...next};}
  catch(error){directorReviewErrors.set(key,{message:publicText(error.message),snapshot});}
  finally{directorReviewBusy.delete(key);if(P?.id===pid){renderSteps();schedulePoll();}}
}
function directorReviewRewriteWarning(review){
  const issues=(review?.issues || []).filter(i=>['image_mode','redraw_cell'].includes(i.fix));
  return issues.length?'有的问题要回第 04 步（对照和修改）处理，只改提示词可能修不好：'+joinDisplayText(issues.map(i=>i.what)):'';
}
async function rewriteDirectorRun(id,rev){
  const pid=P.id,key=directorReviewKey(id,rev),run=P.runs?.[id]?.find(r=>r.rev===rev);
  if(directorBusy.has(pid) || run?.review?.status!=='completed' || run.review.verdict!=='fix')return;
  directorRewriteResults.delete(key);
  await directorAction('rewrite',{segment_id:id,review_rev:rev});
  if(P?.id!==pid)return;
  if(!directorErrors.has(pid)){directorRewriteResults.set(key,'已按验收意见重写这一段，点“生成视频”继续。');v5Regenerate.add(`${pid}:${id}`);}
  renderDirectorGeneration();
}
function directorDraft(){
  let draft=directorDrafts.get(P.id);
  const saved=JSON.stringify(P.director || null);
  if(!draft || !draft.dirty && draft.base!==saved){draft={value:clone(P.director || null),base:saved,dirty:false};directorDrafts.set(P.id,draft);}
  return draft;
}
function directorReady(){
  if(P.director_status?.ready!==undefined)return P.director_status.ready;
  return !!P.segments_plan?.segments?.length && !P.segments_plan_status?.stale && P.segments_plan.segments.every(s=>{
    const state=P.segment_boards?.segments?.[s.id];return state?.versions?.some(v=>v.id===state.current) && !P.segment_board_status?.[s.id]?.source_stale;
  });
}
function directorCellFile(id){
  const segment=P.segments_plan?.segments?.find(s=>s.cells.includes(id)),state=P.segment_boards?.segments?.[segment?.id];
  const version=state?.versions?.find(v=>v.id===state.current),file=version?.cells?.[id];
  return file && !version?.master_snapshot?editVersionFile(version,file):null;
}
function directorMaterials(segment){
  const server=P.director_status?.segments?.find?.(s=>s.id===segment.id)?.generation_materials;
  if(server)return server;
  const reps=P.replacements || {},entries=(reps.characters || []).slice();
  if(reps.product?.asset_id)entries.push({...reps.product,source_id:'product'});
  const materials=(segment.gen_images || segment.images || []).map((key,i)=>{
    const cell=P.storyboard?.cells?.find(c=>c.id===key),person=entries.find(c=>c.source_id===key);
    const asset=P.assets?.find(a=>a.id===person?.asset_id);
    return {key,type:'image',label:`图${i+1}`,file:cell?directorCellFile(key):asset?.file,
      description:cell?`第 ${cell.index} 格的新画面（替换后的状态）`:key==='product'?'新商品照片':`PERSON ${key} 的人物照`};
  });
  if(segment.mode==='video')materials.push({key:'source_video',type:'video',label:'视频1',file:P.source?.file,
    source_start:segment.source_start,description:`原片 ${segment.source_start}–${segment.source_end} 秒，均匀拉伸到 ${segment.duration} 秒`});
  return materials;
}
function directorMaterialsHtml(segment){
  return `<div class="director-references" data-director-materials="${esc(segment.id)}">${directorMaterials(segment).map(m=>`<figure>
    ${m.file?(m.type==='video'?`<video src="${fileUrl(m.file)}#t=${Number(m.source_start || 0)}" controls muted preload="metadata" aria-label="${esc(m.label)}"></video>`:`<a class="director-image" href="${fileUrl(m.file)}" data-director-zoom="${esc(m.file)}" data-caption="${esc(m.label+' '+m.description)}"><img src="${fileUrl(m.file)}" alt="${esc(m.label+' '+m.description)}" loading="lazy"></a>`):'<span class="hint">素材找不到了，请在第 04 步检查这一段的新分镜</span>'}
    <figcaption>${esc(m.label)} · ${esc(m.description || m.duty || '')}</figcaption></figure>`).join('')}</div>`;
}
function directorLegacy(plan){return !!plan && plan.format_version!==3;}
function directorTimes(segment){
  return `原片 ${Number(segment.source_start).toFixed(1)}–${Number(segment.source_end).toFixed(1)} 秒 → 生成 ${segment.duration} 秒`;
}
function directorNotices(warnings){return (warnings || []).map(w=>`<p class="caption-warning">${displayTextHtml(w)}</p>`).join('');}
function directorPromptCorrection(segment){
  const reason=segment.rewrite_reason || '这一段的说明没写好';
  let help='请修改上面的提示词后点“保存”，或点“重新写这一段”。';
  if(reason.includes('内部格号'))help='请把格号改成对应的成片时间和画面描述，再点“保存”。';
  else if(reason.includes('素材编号'))help='请对照上面的素材清单，补齐缺少的编号、删除不存在的编号，再点“保存”。';
  else if(reason.includes('5000'))help='请删掉重复说明，将提示词缩短到 5000 字符以内，再点“保存”。';
  else if(reason.includes('检查项'))help='请点“重新写这一段”，补充至少 1 条非空检查项。';
  else if(segment.structure_needs_rewrite)help='请检查第 04 步（对照和修改）的本段设定，再点“重新写这一段”。';
  return `${reason}。${help}`;
}
function directorSavedFeedback(pid){
  const invalid=(P.director?.segments || []).flatMap((s,i)=>s.needs_rewrite?[`第 ${i+1} 段：${s.rewrite_reason || '提示词未通过校验'}`]:[]);
  directorSaveResults.set(pid,invalid.length?`已保存，但未通过校验（${joinDisplayText(invalid)}）。点“生成视频”时会自动重写。`:'已保存，提示词校验通过。点“生成视频”继续。');
}

function v5VideoPreviewDetails(sid){
  const state=v5Actions.get(editSheetKey(sid)),rec=state?.kind==='video'?state.rec:(P.runs?.[sid] || []).findLast(r=>r.state==='pending_confirmation');
  if(!rec)return '';
  return `<details data-detail-key="director-preview:${esc(sid)}:${esc(rec.rev)}"><summary>本次视频预览详情</summary><p class="hint">${esc(v5ConfirmationText(sid,{kind:'video',rec}))}</p><div class="director-references">${(rec.references || []).map(ref=>`<figure>${ref.file?`<button class="story-preview-history" data-story-preview-file="${esc(ref.file)}" data-story-preview-caption="${esc((ref.tag || ref.label || '参考图')+' · '+(ref.description || ref.duty || ref.label || '本次视频参考图'))}" aria-label="看大图"><img src="${fileUrl(ref.file)}" alt="${esc(ref.label || ref.tag)}"></button>`:''}<figcaption>${esc(ref.tag || ref.label)} ${esc(ref.description || ref.duty || ref.label)}</figcaption></figure>`).join('')}</div>${rec.clip?`<p class="hint">视频1 · 实际提交的原片片段</p><video controls muted preload="metadata" src="${fileUrl(rec.clip)}"></video>`:''}${rec.clip_processing?.blank_captions?captionResidueHtml(rec.caption_residue):''}${(rec.warnings || []).some(w=>!/(过期|重新生成预览|选角或商品改过)/.test(w))?`<p class="hint">${esc(joinDisplayText(rec.warnings.filter(w=>!/(过期|重新生成预览|选角或商品改过)/.test(w))))}</p>`:''}<pre>${esc(rec.preview?.tasks?.[0]?.prompt || '')}</pre></details>`;
}
function renderDirector(){
  const box=$('#directorView');if(!box)return;
  const plan=directorDraft().value,busy=directorBusy.has(P.id) || comparisonBatches.get(P.id)?.busy,ready=directorReady(),error=directorErrors.get(P.id),legacy=directorLegacy(plan);
  const staleWarning='上游内容改过，在第 04 步检查这一段的新分镜，再重新写这一段。';
  const warnings=[];

  if(!comparisonBoardReady(comparisonSelection().sid)){updateHtml(box,'<p class="hint">先完成上面的新分镜</p>');return;}
  const generate=!plan || legacy?'<p class="hint">点“生成视频”时会自动准备这一段的提示词。</p>':'';
  updateHtml(box,`${generate}${directorNotices(warnings)}${error?`<p class="caption-warning" role="alert">${esc(publicText(error.message))}${error.action==='save'?' 请按原因修改后再点“保存”；段落设定不一致时，先复制提示词，再刷新页面。':''} <button class="small ghost" data-director-retry>重试</button></p>`:''}${plan && !legacy?`
    <p class="director-summary">${esc(plan.summary)}</p>
    ${(plan.questions || []).map(q=>`<p class="caption-warning">导演有疑问（可以回第 03 步补充说明，再重新生成）：${esc(q)}</p>`).join('')}
    ${(plan.segments || []).filter(s=>s.id===comparisonSelection().sid).map((s)=>`<article class="director-card" data-director-card="${esc(s.id)}"><p class="hint">本段将提交的素材：</p>${directorMaterialsHtml(s)}

      <p>拍什么：${displayTextHtml(s.what || '')}</p><p>怎么拍：${(P.segments_plan?.segments?.find(row=>row.id===s.id)?.mode || s.mode)==='video'?'原片视频 + 目标画面和照片':'只用目标画面和照片'} —— ${esc(s.mode_reason || '')}</p>
      <div class="director-check"><p>生成后重点看：</p><ul>${(s.check || []).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></div>
      ${s.speech_warning?`<p class="caption-warning" data-speech-prompt-warning role="status">${esc(s.speech_warning)}</p>`:''}
      <details data-detail-key="director-prompt:${esc(s.id)}"><summary>完整提示词</summary>
        <label for="director-prompt-${esc(s.id)}">完整提示词（发给视频模型）</label><textarea id="director-prompt-${esc(s.id)}" data-director-prompt="${esc(s.id)}" rows="12" ${busy?'disabled':''}>${esc(s.prompt)}</textarea><div class="row wrap"><span class="hint${Array.from(s.prompt || '').length>5000?' director-count-over':''}" data-director-count="${esc(s.id)}">${Array.from(s.prompt || '').length} / 5000${s.edited?' · 已手动修改':''}</span><button class="small ghost" data-director-rewrite="${esc(s.id)}" ${busy?'disabled':''}>重新写这一段（一次推理）</button></div>
        <p class="hint" data-director-edited="${esc(s.id)}" ${s.edited?'':'hidden'}>这一段的提示词你改过；重新生成会覆盖你的修改。</p>
      </details>

    ${v5VideoPreviewDetails(s.id)}</article>`).join('')}<div class="row end">${directorSaveResults.has(P.id)?`<span id="directorSaveStatus" role="status">${esc(directorSaveResults.get(P.id))}</span>`:''}<span class="hint">${directorDraft().dirty?'有修改还没保存':''}</span><button data-director-save ${busy?'disabled':''}>保存</button></div>`:''}`);
}
function directorInput(el){
  if(!el.hasAttribute('data-director-prompt'))return false;
  const draft=directorDraft(),s=draft.value?.segments.find(s=>s.id===el.dataset.directorPrompt);if(!s)return true;
  s.prompt=el.value;s.edited=true;draft.dirty=true;
  directorSaveResults.delete(P.id);$('#directorSaveStatus')?.remove();
  const count=$(`[data-director-count="${s.id}"]`),length=Array.from(el.value).length;
  if(count){count.textContent=`${length} / 5000 · 已手动修改`;count.setAttribute('class',`hint${length>5000?' director-count-over':''}`);}
  const notice=$(`[data-director-edited="${s.id}"]`);if(notice)notice.hidden=false;
  return true;
}
async function directorAction(action,extra={},options={}){
  const pid=P.id;if(directorBusy.has(pid))return;
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  if(['generate','rewrite'].includes(action) && warnUnsavedCasting())return;
  if(action==='generate'){
    if(!directorReady())return;
    if(directorDraft().value && !options.automatic && !await pageConfirm('会覆盖现在的方案和你的修改'))return;
  }
  directorBusy.add(pid);directorOperations.set(pid,{kind:'prompt',sid:extra.segment_id});directorErrors.delete(pid);directorSaveResults.delete(pid);renderDirector();renderDirectorGeneration();
  try{
    const draft=directorDraft();
    if(action!=='generate' && draft.dirty){
      const sent=JSON.stringify(draft.value),next=await api('PUT',`projects/${pid}/director`,{action:'save',director:clone(draft.value)});
      if(P?.id!==pid)return;if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;
      if(JSON.stringify(draft.value)===sent)directorDrafts.delete(pid);
      if(action==='save'){directorSavedFeedback(pid);return;}
    }
    const next=await api(action==='generate'?'POST':'PUT',`projects/${pid}/director`,{action,...extra,...(action==='save'?{director:clone(directorDraft().value)}:{})});
    if(P?.id!==pid)return;if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;directorDrafts.delete(pid);
    if(['generate','rewrite'].includes(action))for(const key of segmentModeFeedback.keys())if(key.startsWith(pid+':') && (!extra.segment_id || key===`${pid}:${extra.segment_id}`))segmentModeFeedback.delete(key);
    if(action==='save')directorSavedFeedback(pid);
  }catch(error){directorErrors.set(pid,{message:error.message,action,extra});}
  finally{directorBusy.delete(pid);directorOperations.delete(pid);if(P?.id===pid)renderSteps();}
}
async function directorVideoAction(action,id,extra={},options={}){
  const pid=P.id;if(directorBusy.has(pid))return;
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  const validConfirmation=()=>action!=='confirm' || validFixVideo(id,{rec:P.runs?.[id]?.find(r=>r.rev===extra.rev)});
  if(!validConfirmation())return;
  if(['preview','confirm'].includes(action) && warnUnsavedCasting()){directorErrors.set(pid,{message:CASTING_UNSAVED_MESSAGE,videoAction:action,segment_id:id,extra});return;}
  if(action==='confirm' && !options.confirmed && !await pageConfirm('确认生成（付费）？这会提交 1 个三方渠道任务。'))return;
  if(!validConfirmation())return;
  // Save the visible prompt before packaging it. An error keeps the draft intact.
  if(action==='preview' && directorDraft().dirty){await directorAction('save');if(P?.id!==pid || directorErrors.has(pid))return;}
  if(['preview','confirm'].includes(action) && directorLegacy(P.director))return;
  if(action==='preview' && (P.director?.segments.find(s=>s.id===id)?.needs_rewrite || P.director_status?.segments?.find(s=>s.id===id)?.stale))return;
  directorBusy.add(pid);directorOperations.set(pid,{kind:'video',sid:id});directorErrors.delete(pid);renderDirector();renderDirectorGeneration();
  try{
    const result=await api('POST',`projects/${pid}/tasks/${id}/${action}`,extra);
    if(P?.id!==pid)return;
    if(action==='preview'){
      P.runs ||= {};const runs=P.runs[id] ||= [];for(const run of runs)if(run.state==='pending_confirmation')run.state='superseded';
      if(!runs.some(r=>r.rev===result.rev))runs.push(result);noteProjectResponse(pid,responseContext);
    }else {const next=await api('GET',`projects/${pid}`);if(!await mergeConcurrentProject(pid,next,responseContext) || P?.id!==pid)return;}
  }catch(error){directorErrors.set(pid,{message:error.message,videoAction:action,segment_id:id,extra});}
  finally{directorBusy.delete(pid);directorOperations.delete(pid);if(P?.id===pid){renderSteps();schedulePoll();}}
}
function renderDirectorGeneration(){ renderComparison(); }

// ---------- editable storyboards (step 4) ----------
const newStoryboardDrafts=new Map(),newStoryboardBusy=new Set(),newStoryboardErrors=new Map(),newStoryboardPreviews=new Map(),newStoryboardCells=new Map(),newStoryboardCellRequests=new Map(),newStoryboardInvalidPreviews=new Set();
let storyboardRecorder=null;
function editSheetKey(sid){return `${P.id}:${sid}`;}
function editSheetState(sid){return P.segment_boards?.segments?.[sid] || {};}
function editMaterials(sid){
  const provided=P.segment_board_status?.[sid]?.materials;if(provided)return provided;
  const sheet=segmentSheets().find(s=>s.id===sid),cells=(sheet?.cells || []).map(id=>P.storyboard?.cells?.find(c=>c.id===id)).filter(Boolean),rep=P.replacements || {};
  const order=[...new Set(cells.flatMap(c=>c.characters || []))],names=new Map((P.breakdown?.characters || []).map(c=>[c.id,c.name]));
  const entries=(rep.characters || []).filter(c=>['replace','add'].includes(c.action) && order.includes(c.source_id)).slice().sort((a,b)=>order.indexOf(a.source_id)-order.indexOf(b.source_id));
  if(rep.product?.action==='replace' && cells.some(c=>c.product || c.product_later))entries.push({...rep.product,source_id:'product'});
  return entries.map(c=>{const asset=P.assets?.find(a=>a.id===c.asset_id && a.type==='image'),key=c.source_id;return asset?{key,name:key==='product'?'商品':`${key} ${c.name || names.get(key) || ''}`.trim(),asset_id:asset.id,file:asset.file,description:c.description || '',added:c.action==='add',enabled:true}:null;}).filter(Boolean);
}
function editDraft(sid){
  const key=editSheetKey(sid),segment=P.segments_plan?.segments?.find(s=>s.id===sid),binding=JSON.stringify([segment?.cells,segment?.source_start,segment?.source_end,segment?.refs,editMaterials(sid),P.segment_board_status?.[sid]?.active_materials]);
  if(newStoryboardDrafts.get(key)?.binding!==binding){
    const state=editSheetState(sid);newStoryboardDrafts.set(key,{binding,request:state.request || '',materials:{...Object.fromEntries(editMaterials(sid).map(m=>[m.key,m.enabled])),...(P.segment_board_status?.[sid]?.active_materials || state.materials)},plan:state.plan?clone(state.plan):null});
  }
  const draft=newStoryboardDrafts.get(key);
  for(const m of editMaterials(sid))if(!(m.key in draft.materials))draft.materials[m.key]=m.enabled;
  return draft;
}
function newStoryboardInput(el){
  if(!el.closest('#newStoryboards, #segmentCellEditor'))return false;
  if(el.hasAttribute('data-edit-material'))return false;
  const sid=el.dataset.sheet || el.dataset.editRequest || el.dataset.editCellRequest,key=editSheetKey(sid),draft=editDraft(sid);
  if(el.hasAttribute('data-edit-cell-request') && (['preparing','confirming','running'].includes(v5ActionState(sid)?.status) || newStoryboardBusy.has(key))){el.value=newStoryboardCellRequests.get(key) || '';return true;}
  if(el.hasAttribute('data-edit-request'))draft.request=el.value;
  if(el.hasAttribute('data-edit-change')){const cell=draft.plan?.cells.find(c=>c.cell===el.dataset.editChange);if(cell){cell.changes=el.value;draft.plan.edited=true;}}
  if(el.hasAttribute('data-edit-remove-text') && draft.plan){draft.plan.remove_text=el.checked;draft.plan.edited=true;}
  if(el.hasAttribute('data-edit-cell-request'))newStoryboardCellRequests.set(key,el.value);
  newStoryboardPreviews.delete(key);newStoryboardInvalidPreviews.add(key);if(v5Actions.get(key)?.status==='confirming')v5Actions.delete(key);renderNewStoryboards();return true;
}
function editVersionFile(version,file=version.file){return version.resliced_at?file+'?v='+encodeURIComponent(version.resliced_at):file;}
function renderNewStoryboards(){
  const box=$('#newStoryboards');if(!box)return;
  if(!segmentSheets().length){
    for(const cache of [newStoryboardDrafts,newStoryboardErrors,newStoryboardPreviews,newStoryboardCells,newStoryboardCellRequests,newStoryboardInvalidPreviews])for(const key of cache.keys())if(key.startsWith(P.id+':'))cache.delete(key);
    updateHtml(box,'<p class="hint">先在第 04 步生成分段方案</p>');renderComparison();return;
  }
  updateHtml(box,segmentSheets().filter(sheet=>sheet.id===comparisonSelection().sid).map(sheet=>{
    const sid=sheet.id,key=editSheetKey(sid),state=editSheetState(sid),draft=editDraft(sid),plan=draft.plan,busy=segmentPlanBusy.has(P.id) || newStoryboardBusy.has(key) || comparisonBatches.get(P.id)?.busy || ['preparing','confirming','running'].includes(v5ActionState(sid)?.status),disabled=busy?'disabled':'',materials=editMaterials(sid),versions=state.versions || [],current=versions.find(v=>v.id===state.current),cells=sheet.cells.map(id=>P.storyboard?.cells?.find(c=>c.id===id)).filter(Boolean),preview=v5BoardPreview(sid,null);
    return `<section class="edit-sheet" data-edit-sheet="${esc(sid)}">${state.reused_after_replan?'<p class="hint">第 04 步重新生成后，这一段内容没变，沿用了已有的新分镜。</p>':''}<label>补充要求（可不填）<textarea rows="2" data-edit-request="${esc(sid)}" ${disabled} placeholder="在这里补充这一段的画面要求">${esc(draft.request)}</textarea></label><div class="row wrap">${v5CurrentRun(sid)?.state==='completed' && !comparisonNeedsPrompt({id:sid}) && !v5Regenerate.has(key)?`<button class="ghost small" data-edit-preview="${esc(sid)}" ${disabled}>整段重画</button>`:''}<button class="ghost small" data-edit-mic="${esc(sid)}" ${disabled} aria-label="语音输入">${storyboardRecorder?.key===key?'■ 结束录音':'语音输入'}</button><button class="ghost small" data-edit-plan="${esc(sid)}" ${disabled}>重新生成改图方案</button></div><div class="edit-materials">${materials.map(m=>`<label class="edit-material"><img src="${fileUrl(m.file)}" alt="${esc(m.name)}"><span>${esc(m.name)}</span><input type="checkbox" data-edit-material="${esc(m.key)}" data-sheet="${esc(sid)}" ${draft.materials[m.key]?'checked':''} ${disabled} aria-label="使用${esc(m.name)}"><span aria-hidden="true" class="material-eye ${draft.materials[m.key]?'':'off'}">👁</span></label>`).join('') || '<p class="hint">第 03 步选好的人物和商品照片会列在这里。</p>'}</div>
    ${plan?`<details class="edit-plan" data-detail-key="edit-plan:${esc(sid)}"><summary>改图方案</summary><p>${esc(plan.summary)}</p>${(plan.cells || []).map(row=>{const c=cells.find(c=>c.id===row.cell);return c?`<article class="edit-plan-row"><button class="story-preview-history" data-story-preview-cell="${esc(c.id)}" data-story-preview-side="source" aria-label="看大图"><img src="${fileUrl(storyboardFile(c.file))}" alt="原片第 ${c.index} 镜"></button><div><label>第 ${c.index} 镜 · ${fmt(c.start,1)}–${fmt(c.end,1)} 秒<textarea rows="2" data-edit-change="${esc(c.id)}" data-sheet="${esc(sid)}" ${disabled}>${esc(row.changes)}</textarea></label><p class="hint">${esc(row.keep)}</p></div></article>`:'';}).join('')}<label class="check"><input type="checkbox" data-edit-remove-text data-sheet="${esc(sid)}" ${plan.remove_text?'checked':''} ${disabled}>去掉原片画面上的字幕和表情</label>${plan.questions?.length?`<p class="hint">可以在对应格补充说明：${displayTextHtml(plan.questions.join('；'))}</p>`:''}<button class="ghost small" data-edit-save="${esc(sid)}" ${disabled}>保存方案</button></details>`:''}
    ${preview?`<details data-detail-key="edit-preview:${esc(sid)}"><summary>本次生图预览详情</summary><pre>${esc(preview.preview?.prompt || '')}</pre><div class="edit-preview-refs">${(preview.storyboard_context?.references || []).map((r,i)=>`<figure>${r.file?`<button class="story-preview-history" data-story-preview-file="${esc(r.file)}" data-story-preview-caption="图${i+1} · ${esc(r.name || '本次生图参考图')}" aria-label="看大图"><img src="${fileUrl(r.file)}" alt="图${i+1}：${esc(r.name)}"></button>`:''}<figcaption>图${i+1}：${esc(r.name)}</figcaption></figure>`).join('')}</div></details>`:''}
    ${current && current.slicer!==2?`<p class="hint">这一版切格有误，可按模型原图免费重新切。<button class="small ghost" data-edit-reslice="${esc(current.id)}" data-sheet="${esc(sid)}" ${disabled}>重新切格（免费）</button></p>`:''}
    <details data-detail-key="edit-history:${esc(sid)}"><summary>新分镜历史版本（${versions.length}）</summary><div class="edit-versions">${versions.map(v=>`<article><button class="story-preview-history" data-story-preview-version="${esc(v.id)}" data-sheet="${esc(sid)}" aria-label="看大图"><img src="${fileUrl(editVersionFile(v))}" alt="第 ${Number(v.id.slice(1))} 版"></button><span>第 ${Number(v.id.slice(1))} 版 · ${v.from==='full'?'整张':'重画第 '+Number(v.from.split(':c')[1])+' 镜'}</span>${imageModelAnnotationHtml(storyboardVersionRecord(v))}${state.current===v.id?'<b>在用</b>':`<button class="small ghost" data-edit-use="${esc(v.id)}" data-sheet="${esc(sid)}" ${disabled}>改用这一版</button>`}</article>`).join('')}</div></details></section>`;
  }).join(''));
  renderComparison();
}

async function performStoryboardEdit(sid,action,extra={},options={}){
  const pid=P.id,key=editSheetKey(sid);if(newStoryboardBusy.has(key))return;
  const responseContext={revision:projectResponseRevisions.get(pid) || 0};
  const draft=clone(editDraft(sid)),cellInput=options.cellInput || {cell_id:newStoryboardCells.get(key),request:newStoryboardCellRequests.get(key)};newStoryboardErrors.delete(key);
  newStoryboardBusy.add(key);renderNewStoryboards();
  try{
    if(['plan','preview','cell-preview','confirm'].includes(action) && dirty.rep)throw Error(CASTING_UNSAVED_MESSAGE);
    if(P?.id!==pid)return;
    if(action==='confirm'){
      if(newStoryboardInvalidPreviews.has(key))throw Error('方案或需求改过，请重新生成预览');
      const cached=newStoryboardPreviews.get(key),rec=extra.image_id?(P.images?.[extra.image_id] || (cached?.id===extra.image_id?cached:null)):cached;
      if(!rec || rec.storyboard_context?.sheet_id!==sid)throw Error('请先生成这一段的预览');
      if(!options.confirmed && !await pageConfirm(`确认付费生成新分镜（${imageFee(rec)}）？`))return;
      await api('POST',`projects/${pid}/images/${rec.id}/confirm`,{});newStoryboardPreviews.delete(key);
      if(P?.id===pid && P.images?.[rec.id]){P.images[rec.id].state='running';noteProjectResponse(pid,responseContext);}
      await refresh();return;
    }
    let result;
    if(action==='materials'){result=await api('PUT',`projects/${pid}/storyboard-edits/${sid}`,{materials:draft.materials});if(!await mergeConcurrentProject(pid,result,responseContext) || P?.id!==pid)return;return;}
    if(action==='reslice')result=await api('POST',`projects/${pid}/storyboard-edits`,{action:'reslice',sheet_id:sid,version_id:extra.version_id});
    else if(action==='use')result=await api('POST',`projects/${pid}/storyboard-edits/${sid}/use`,extra);
    else if(action==='plan')result=await api('POST',`projects/${pid}/storyboard-edits/${sid}/plan`,{request:draft.request,materials:draft.materials});
    else{
      const saved=await api('PUT',`projects/${pid}/storyboard-edits/${sid}`,draft);
      if(P?.id!==pid)return;if(!await mergeConcurrentProject(pid,saved,responseContext) || P?.id!==pid)return;
      if(action==='save')result=P;
      else{
        const rec=await api('POST',`projects/${pid}/storyboard-edits/${sid}/preview`,{...(action==='cell-preview'?cellInput:{}),...(options.channel?{channel:options.channel}:{})});
        if(P?.id!==pid)return;
        if(action==='cell-preview' && rec.storyboard_context?.cell_id===cellInput.cell_id && rec.storyboard_context.cell_revision){
          const board=editSheetState(sid);board.cell_revisions ||= {};board.cell_revisions[cellInput.cell_id]=clone(rec.storyboard_context.cell_revision);
        }
        newStoryboardPreviews.set(key,rec);newStoryboardInvalidPreviews.delete(key);P.images ||= {};P.images[rec.id]=rec;noteProjectResponse(pid,responseContext);newStoryboardDrafts.delete(key);return;
      }
    }
    if(P?.id!==pid)return;if((result!==P && !await mergeConcurrentProject(pid,result,responseContext)) || P?.id!==pid)return;if(!['use','reslice'].includes(action))newStoryboardDrafts.delete(key);newStoryboardPreviews.delete(key);newStoryboardInvalidPreviews.add(key);
  }catch(error){newStoryboardErrors.set(key,publicText(error.message));}
  finally{newStoryboardBusy.delete(key);if(P?.id===pid)renderSteps();}
}
async function toggleStoryboardMic(sid,cell=null){
  const pid=P.id,key=editSheetKey(sid);
  if(storyboardRecorder){if(storyboardRecorder.key===key)storyboardRecorder.recorder.stop();else{newStoryboardErrors.set(key,'先结束另一张分镜的录音');renderNewStoryboards();}return;}
  let stream;
  try{
    if(typeof MediaRecorder==='undefined' || !navigator.mediaDevices?.getUserMedia)throw Error('浏览器不支持录音');
    stream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(P?.id!==pid){stream.getTracks().forEach(t=>t.stop());return;}
    const chunks=[],recorder=new MediaRecorder(stream);storyboardRecorder={key,recorder};
    recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    recorder.onerror=()=>{newStoryboardErrors.set(key,'麦克风录音没完成，请再试一次');stream.getTracks().forEach(t=>t.stop());storyboardRecorder=null;if(P?.id===pid)renderNewStoryboards();};
    recorder.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop());storyboardRecorder=null;if(P?.id!==pid)return;
      newStoryboardBusy.add(key);renderNewStoryboards();
      try{
        const blob=new Blob(chunks,{type:recorder.mimeType || 'audio/webm'});if(!blob.size)throw Error('没有录到声音，请再试一次');
        const value=await api('POST',`projects/${pid}/voice-to-text`,blob);if(P?.id!==pid)return;
        if(cell){const before=newStoryboardCellRequests.get(key) || '';newStoryboardCellRequests.set(key,before+(before?'\n':'')+value.text);}else{const draft=editDraft(sid);draft.request+=(draft.request?'\n':'')+value.text;}newStoryboardErrors.delete(key);newStoryboardPreviews.delete(key);newStoryboardInvalidPreviews.add(key);
      }catch(error){newStoryboardErrors.set(key,'录音没能转成文字，请再试一次：'+publicText(error.message));}
      finally{newStoryboardBusy.delete(key);if(P?.id===pid)renderNewStoryboards();}
    };
    recorder.start();newStoryboardErrors.delete(key);renderNewStoryboards();
  }catch(error){stream?.getTracks().forEach(t=>t.stop());newStoryboardErrors.set(key,'无法使用麦克风，请允许录音权限，或直接打字：'+publicText(error.message));if(P?.id===pid)renderNewStoryboards();}
}

// ---------- shared library and master-based fission (V9a2) ----------
let fissionState=null,fissionOpening=0,fissionTimer=null,libraryState=null;
let fissionRequestSequence=0;
const librarySaved=new Map(),librarySaving=new Set(),librarySaveErrors=new Map(),fissionDraftStates=new Map();
// ---------- one live dashboard per master; GET never starts work ----------
function batchFileUrl(pid,file){return `/files/${encodeURIComponent(pid)}/${String(file).split('/').map(encodeURIComponent).join('/')}`;}
function batchBoardCurrent(state){return state===batchBoardState && state.generation===projectGeneration && !openingProjectId;}
function closeBatchBoard(){
  if(!batchBoardState)return;
  batchBoardState=null;clearTimeout(pollTimer);
  if(storyboardLightbox?.item.boardProject)closeStoryboardLightbox(false);
  const box=$('#batchBoard');if(box)box.hidden=true;
  const button=$('#deleteProjectBtn');if(button){button.disabled=false;button.title='删除当前项目及其所有视频和结果';}
}
async function openBatchBoard(masterId){
  if(!masterId)return;
  if(fissionState?.open && fissionState.busy && fissionState.busy!=='upload')return;
  if(fissionState){fissionState.open=false;fissionState.picker=null;clearTimeout(fissionTimer);renderFission();}
  if(storyboardLightbox)closeStoryboardLightbox(false);
  const generation=beginProjectView();
  const state={masterId,generation,data:null,error:'',hidden:{},busy:null,revision:0,read:null};
  batchBoardState=state;$('#workspace').hidden=true;$('#emptyState').hidden=true;
  $('#deleteProjectBtn').disabled=true;$('#deleteProjectBtn').title='看板不是项目；打开某条新片后可以删除那个项目';
  renderProjectOptions();renderBatchBoard();await refreshBatchBoard();
}
function batchApplyData(state,data){
  state.data=data;
  const rows=(data.batches || []).flatMap(b=>b.items || []),ids=new Set(rows.map(r=>r.project_id));
  projectList=projectList.filter(p=>p.derived_from?.project_id!==state.masterId || ids.has(p.id));
  if(data.master?.exists===false)projectList=projectList.filter(p=>p.id!==state.masterId);
  else if(data.master && !projectList.some(p=>p.id===state.masterId))projectList.push({id:state.masterId,name:data.master.name});
  for(const row of rows){
    const old=projectList.find(p=>p.id===row.project_id),entry={id:row.project_id,name:row.name,derived_from:{...(old?.derived_from || {}),project_id:state.masterId,fission_number:row.number}};
    if(old)Object.assign(old,entry);else projectList.push(entry);
  }
  renderProjectOptions();
}
function refreshBatchBoard(){
  const state=batchBoardState;if(!state)return Promise.resolve();
  if(state.read)return state.read;
  if(state.busy)return Promise.resolve();
  clearTimeout(pollTimer);const revision=state.revision;
  const reading=(async()=>{
    try{
      const data=await api('GET',`fission-boards/${encodeURIComponent(state.masterId)}`);
      if(!batchBoardCurrent(state) || revision!==state.revision)return;
      batchApplyData(state,data);state.pollError='';renderBatchBoard();
    }catch(error){if(batchBoardCurrent(state) && revision===state.revision){state.pollError='进度暂时没取到，已有任务不受影响。点“刷新看板”再查：'+publicText(error.message);renderBatchBoard();}}
    finally{if(state.read===reading)state.read=null;if(batchBoardCurrent(state))schedulePoll();}
  })();
  state.read=reading;return reading;
}
function batchPhotosHtml(pid,photos=[]){return `<div class="batch-photos">${photos.map(p=>`<span title="${esc(p.name)}">${p.file?`<img src="${esc(batchFileUrl(pid,p.file))}" alt="${esc(p.name)}" loading="lazy">`:'<span class="batch-photo-empty">'+esc(p.name)+'</span>'}</span>`).join('')}</div>`;}
function batchBoardsHtml(pid,boards=[]){return `<div class="batch-board-strip" aria-label="新分镜小图">${boards.filter(b=>b.file).map((b,i)=>`<span class="batch-board-thumb" data-batch-thumb="${i}" data-project="${esc(pid)}" title="${esc(b.index?'第 '+b.index+' 镜':'新分镜')}"><img src="${esc(batchFileUrl(pid,b.file))}" alt="${esc(b.index?'第 '+b.index+' 镜':'新分镜')}" draggable="false" loading="lazy"><button class="ghost small" data-batch-zoom="${i}" data-project="${esc(pid)}" aria-label="看大图" title="看大图">🔍</button></span>`).join('')}</div>`;}
function batchProgressHtml(progress={}){
  const states={completed:'完成',running:'进行中',pending_confirmation:'等确认',failed:'失败',idle:'待开始',queued:'排队中',interrupted:'已中断',query_pending:'等待查询'};
  return `<div class="batch-pipeline">${[['boards','新分镜'],['prompts','提示词'],['videos','视频'],['final','成片']].map(([key,name])=>{
    const step=progress[key] || {},status=step.state || 'idle',label=states[status] || '待开始';
    return `<span class="batch-stage ${esc(status)}" title="${esc(step.detail || label)}">${name}${key==='final'?(status==='completed'?' ✓':' · '+label):` ${step.done || 0}/${step.total || 0} · ${label}`}${step.detail?' · '+esc(publicText(step.detail)):''}</span>`;
  }).join('<span class="batch-arrow" aria-hidden="true">→</span>')}</div>`;
}
function batchRowHtml(row){
  const pid=row.project_id;
  return `<article class="batch-project-row" id="batch-row-${esc(pid)}" data-batch-project-row="${esc(pid)}">${batchPhotosHtml(pid,row.photos)}<div class="batch-row-name"><b>裂变 ${esc(row.number)}</b><span title="${esc(row.changes)}">${esc(row.changes)}</span></div><div class="batch-row-progress">${batchProgressHtml(row.progress)}${batchBoardsHtml(pid,row.boards)}</div><div class="batch-row-actions"><button class="small ghost" data-batch-open-project="${esc(pid)}">打开</button>${row.final?.file && row.progress?.final?.state==='completed'?`<a class="button small ghost" data-batch-download href="${esc(batchFileUrl(pid,row.final.file))}" download>下载</a>`:''}</div>${(row.errors || []).length?`<div class="batch-row-errors">${row.errors.map((error,index)=>`<div id="batch-error-${esc(pid)}-${index}"><span>${error.segment_index?'第 '+esc(error.segment_index)+' 段 · ':''}${{boards:'新分镜',prompts:'提示词',videos:'视频',final:'成片'}[error.stage] || ''}</span>${errorHtml(error.error || '处理没有完成，请打开项目查看。',error.stage==='boards'?'生图':error.stage==='videos'?'视频':'推理')}<button class="small ghost" data-batch-retry="${index}" data-project="${esc(pid)}" ${batchBoardState.busy?'disabled':''}>${['image-query','video-query'].includes(error.retry?.kind)?'再查一次':'打开这一段处理'}</button></div>`).join('')}</div>`:''}</article>`;
}
function batchConfirmationHtml(kind,pending,state){
  if(!pending?.items?.length || state.hidden[kind]===pending.token)return '';
  const images=kind==='images',groups=new Map();
  for(const item of pending.items){if(!groups.has(item.project_id))groups.set(item.project_id,{name:item.name,items:[]});groups.get(item.project_id).items.push(item);}
  const fee=items=>imageBatchFee(items.map(item=>({channel:item.channel,preview:{model:item.model,quality:item.quality}})));
  const text=images?`生成 ${pending.project_count} 条裂变的新分镜：${pending.image_count} 张图，${fee(pending.items)}`:`生成 ${pending.project_count} 条裂变的视频：计费约 ${pending.billing_seconds} 秒（输出 ${pending.output_seconds} 秒；带原片视频的段另算参考视频 ${pending.reference_seconds} 秒）`;
  return `<section class="batch-confirmation inline-confirm" data-batch-confirmation="${kind}" role="group" aria-label="整批付费确认"><span>${esc(text)}</span><details data-detail-key="batch-pay:${esc(state.masterId)}:${kind}"><summary>每条各多少</summary><ul>${[...groups].map(([pid,group])=>`<li><span title="${esc(group.name)}">${esc(group.name)}</span>：${images?`${group.items.length} 张图，${esc(fee(group.items))}`:`计费 ${Number(group.items.reduce((n,i)=>n+Number(i.billing_seconds),0).toFixed(4))} 秒（输出 ${Number(group.items.reduce((n,i)=>n+Number(i.output_seconds),0).toFixed(4))} 秒；参考视频 ${Number(group.items.reduce((n,i)=>n+Number(i.reference_seconds),0).toFixed(4))} 秒）`}</li>`).join('')}</ul></details><button class="small pay" data-batch-confirm="${kind}" data-token="${esc(pending.token)}" ${state.busy?'disabled':''}>确认</button><button class="small ghost" data-batch-cancel="${kind}" ${state.busy?'disabled':''}>取消</button></section>`;
}
function batchCreatedLabel(batch){
  const match=String(batch.created_at || '').match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return `${match?Number(match[1])+'/'+Number(match[2])+' '+match[3]+':'+match[4]:'创建时间未记录'} 创建 · ${(batch.items || []).length} 条`;
}
function renderBatchBoard(){
  const state=batchBoardState,box=$('#batchBoard');if(!box)return;box.hidden=!state;if(!state)return;
  const data=state.data,disabled=state.busy?'disabled':'',master=data?.master || {};
  preserveView(()=>updateHtml(box,`<header class="batch-board-header"><div><h1>批量看板</h1><p class="batch-master-name" title="${esc(master.name || '母版')}">${esc(master.name || '母版')}${master.exists===false?' · 母版已删除':''}</p><p>${data?`${data.total} 条新片 · ${data.completed} 条已出片`:'正在读取进度…'}</p></div><nav class="batch-board-actions"><button class="ghost" data-batch-refresh ${disabled}>刷新看板</button>${data?`<button class="ghost" data-batch-new ${disabled || (master.exists===false?'disabled title="母版已删除，无法再创建一批"':'')}>再出一批</button>`:''}${[['images','新分镜'],['videos','视频']].map(([kind,label])=>`${data?.['can_prepare_'+kind]?`<button data-batch-prepare="${kind}" title="先准备${label}预览，调用推理；生成前再确认付费" ${disabled}>全部生成${label}</button>`:''}${data?.pending?.[kind]?.items?.length && state.hidden[kind]===data.pending[kind].token?`<button class="ghost" data-batch-show="${kind}" ${disabled}>查看${label}付费清单</button>`:''}`).join('')}</nav>${data?.can_prepare_images || data?.can_prepare_videos?'<p class="hint batch-prepare-hint">“全部生成”会先准备好每一条的预览（要调用推理整理改法或写提示词），生图和生视频的钱要等你确认清单后才花。</p>':''}</header>${state.busy?`<p role="status">${esc(state.busy)}</p>`:''}${state.error?`<div role="status">${errorHtml(state.error)}</div>`:''}${state.pollError?`<p role="status" class="caption-warning">${esc(state.pollError)}</p>`:''}${data?`${['images','videos'].map(kind=>batchConfirmationHtml(kind,data.pending?.[kind],state)).join('')}<article class="batch-project-row batch-master-row" data-batch-master id="batch-master-${esc(state.masterId)}">${master.exists===false?'<div class="batch-missing-master">母版已删除</div>':`${batchPhotosHtml(state.masterId,master.photos)}<div class="batch-row-name"><b>母版</b><span title="${esc(master.name)}">${esc(master.name)}</span></div>${batchBoardsHtml(state.masterId,master.boards)}<div class="batch-row-actions"><button class="small ghost" data-batch-open-project="${esc(state.masterId)}">打开</button></div>`}</article>${(data.batches || []).map(batch=>`<section class="batch-group" id="batch-group-${esc(batch.id)}"><h2>${esc(batchCreatedLabel(batch))}</h2>${(batch.items || []).map(batchRowHtml).join('')}</section>`).join('')}${!data.total?'<p class="hint">还没有裂变项目。点“再出一批”选照片并创建新视频。</p>':''}`:''}`));
}
async function batchBoardAction(kind,action,token){
  const state=batchBoardState;if(!state || state.busy || !['images','videos'].includes(kind))return;
  clearTimeout(pollTimer);state.revision++;state.error='';
  state.busy=action==='confirm'?'正在提交已确认的清单…':`正在准备${kind==='images'?'新分镜':'视频'}预览（调用推理），准备好后再确认付费…`;renderBatchBoard();
  try{
    const data=await api('POST',`fission-boards/${encodeURIComponent(state.masterId)}/${kind}/${action}`,action==='confirm'?{token}:{});
    if(!batchBoardCurrent(state))return;
    delete state.hidden[kind];batchApplyData(state,data);
  }catch(error){if(batchBoardCurrent(state))state.error=publicText(error.message);}
  finally{
    state.busy=null;
    if(batchBoardCurrent(state)){renderBatchBoard();if(state.read)await state.read;if(batchBoardCurrent(state))await refreshBatchBoard();}
  }
}
async function batchOpenProject(pid,sid,stage){
  if(storyboardLightbox)closeStoryboardLightbox(false);
  try{
    const opening=openProject(pid),generation=projectGeneration;
    if(!await opening || generation!==projectGeneration || batchBoardState || P?.id!==pid)return;
    if(sid){setComparisonCells(sid,[]);v5View(sid).view=stage==='videos'?'video':'board';renderSteps();}
    $(stage==='final'?'#step-5':'#step-4')?.scrollIntoView?.({block:'start'});
  }
  catch(error){toast('项目暂时没有打开，请重试：'+publicText(error.message),true);}
}
async function batchRetry(pid,index){
  const state=batchBoardState,error=state?.data?.batches.flatMap(b=>b.items).find(r=>r.project_id===pid)?.errors?.[index];
  if(!error || state.busy)return;
  const retry=error.retry || {};
  if(!['image-query','video-query'].includes(retry.kind))return batchOpenProject(pid,error.segment_id,error.stage);
  state.busy='正在重新查询这一段的结果…';state.error='';state.revision++;clearTimeout(pollTimer);renderBatchBoard();
  try{await api('POST',retry.kind==='image-query'?`projects/${pid}/images/${retry.image_id}/resume`:`projects/${pid}/tasks/${error.segment_id}/resume`,retry.kind==='video-query'?{rev:retry.rev}:{});}
  catch(problem){state.error=publicText(problem.message);}
  finally{state.busy=null;if(batchBoardCurrent(state)){if(state.read)await state.read;renderBatchBoard();await refreshBatchBoard();}}
}
async function batchNewBatch(){
  const state=batchBoardState;if(!state || state.busy || state.data?.master?.exists===false)return;
  const id=state.masterId;
  try{
    let current=state;
    if(P?.id!==id){
      const navigation=openProject(id),generation=projectGeneration;
      if(!await navigation || generation!==projectGeneration || batchBoardState || P?.id!==id)return;
      const opening=openBatchBoard(id);current=batchBoardState;await opening;
      if(!batchBoardCurrent(current) || P?.id!==id)return;
    }
    clearTimeout(pollTimer);const opening=openFission({fromBoard:true}),panel=fissionState;await opening;
    if(batchBoardCurrent(current) && P?.id===id && fissionState===panel && fissionCurrent(panel) && panel.open && !panel.loading && !panel.error)await fissionNewBatch();
  }catch(error){toast('照片面板没有打开，请重试：'+publicText(error.message),true);}
}
function batchZoom(pid,index,origin){
  const data=batchBoardState?.data;if(!data)return;
  const row=pid===data.master?.id?data.master:data.batches.flatMap(b=>b.items).find(r=>r.project_id===pid),board=(row?.boards || []).filter(b=>b.file)[Number(index)];
  if(!board)return;
  const title=`${pid===data.master?.id?'母版':row.name} · ${board.segment_index?'第 '+board.segment_index+' 段 · ':''}${board.index?'第 '+board.index+' 镜 · ':''}新分镜${board.version?' 第 '+String(board.version).replace(/^v/,'')+' 版':''}`;
  openStoryboardLightbox({file:board.file,url:batchFileUrl(pid,board.file),side:'target',title,imageRecord:{channel:board.channel,preview:{model:board.model,quality:board.quality}},boardProject:pid,boardSegment:board.segment_id},origin);
}

const libraryReads=new Set();
async function readLibrary(apply){
  // A GET started before a background upload can return an older list. Merge
  // only uploads completed during this read, never the entire previous list.
  const uploaded=new Map();libraryReads.add(uploaded);
  try{
    const next=await libraryRequest('GET','library');
    for(const item of uploaded.values())rememberLibraryPhoto(next.items,item);
    if(apply)apply(next);
    return next;
  }finally{libraryReads.delete(uploaded);}
}
function fissionCurrent(state=fissionState){return !!state && state===fissionState && state.pid===P?.id;}
function librarySaveHtml(key,entry){
  if(!entry.asset_id)return '';
  const saved=(P.library_saved_assets || []).includes(entry.asset_id) || librarySaved.get(P.id)?.has(entry.asset_id),busy=librarySaving.has(`${P.id}:${key}`),error=librarySaveErrors.get(`${P.id}:${key}`);
  return `<button class="small ghost" data-library-save="${esc(key)}" ${saved || busy?'disabled':''}>${saved?'✓ 已在素材库':busy?'正在存入…':'存进素材库'}</button>${error?`<span class="caption-warning" role="status">${esc(error)}</span>`:''}`;
}
async function saveCastingToLibrary(key){
  const pid=P.id,entry=castEntry(castingDraft(),key),lock=`${pid}:${key}`;
  if(librarySaving.has(lock) || !entry.asset_id || librarySaved.get(pid)?.has(entry.asset_id) || (P.library_saved_assets || []).includes(entry.asset_id))return;
  if(dirty.rep){librarySaveErrors.set(lock,'选角改动尚未保存，素材库还不能读取这张照片。点第 03 步“保存”后再存入。');renderReplacements(true);return;}
  librarySaving.add(lock);librarySaveErrors.delete(lock);renderReplacements(true);
  try{await api('POST',`projects/${pid}/library`,{kind:key==='product'?'product':'person',source_id:key});
    if(!librarySaved.has(pid))librarySaved.set(pid,new Set());librarySaved.get(pid).add(entry.asset_id);
  }catch(error){librarySaveErrors.set(lock,'存入素材库失败，项目照片仍保留。点“存进素材库”重试：'+publicText(error.message));}
  finally{librarySaving.delete(lock);if(P?.id===pid)renderReplacements(true);}
}
function fissionApplyView(state,view){
  state.view=view;state.choices=clone(view.draft?.choices || {});state.removed=clone(view.draft?.removed || []);state.rows=clone(view.rows || []);
  if(view.batches)state.batches=view.batches;state.unsaved=false;
}
async function openFission(options={}){
  if(!P || !options.fromBoard && !finalPreviewStatus().ready)return;
  const pid=P.id,ticket=++fissionOpening,previous=fissionDraftStates.get(pid);
  if(batchBoardState)clearTimeout(pollTimer);
  clearTimeout(fissionTimer);
  if(previous?.busy==='upload'){
    fissionState=previous;previous.open=true;renderFission();$('#fissionPanel')?.focus();scheduleFissionPoll();return;
  }
  const state={pid,name:P.name,open:true,loading:true,choices:{},removed:[],rows:[],view:{columns:[],product_checks:{}},library:{items:[]},known:{},batches:[]};
  fissionState=state;fissionDraftStates.set(pid,state);renderFission();
  try{
    const [view]=await Promise.all([api('GET',`projects/${pid}/fission-draft`),readLibrary(library=>{
      if(!fissionCurrent(state) || ticket!==fissionOpening)return;
      state.library=library;state.known=Object.fromEntries(library.items.map(i=>[i.id,i]));
    })]);
    if(!fissionCurrent(state) || ticket!==fissionOpening)return;
    fissionApplyView(state,view);state.loading=false;state.showProgress=!!state.batches[0]?.items?.some(i=>['preparing','queued','running','pending','pending_confirmation','ready','awaiting_confirmation','interrupted'].includes(i.status));
    if(previous?.unsaved)Object.assign(state,{choices:clone(previous.choices),removed:clone(previous.removed),rows:clone(previous.rows),known:{...previous.known,...state.known},unsaved:true,error:previous.error,requestId:previous.requestId});
    else if(previous?.requestId)state.requestId=previous.requestId;
    renderFission();$('#fissionPanel')?.focus();scheduleFissionPoll();
  }catch(error){if(fissionCurrent(state)){state.loading=false;state.error='照片面板没有打开，母版未改变。关闭后再点“批量裂变”：'+publicText(error.message);renderFission();}}
}
function closeFission(){if(fissionState?.busy && fissionState.busy!=='upload')return;fissionOpening++;clearTimeout(fissionTimer);if(fissionState){fissionState.open=false;fissionState.picker=null;}renderFission();$('[data-fission-open]')?.focus();if(batchBoardState)refreshBatchBoard();}
function closeFissionPicker(){if(!fissionState || (fissionState.busy && fissionState.busy!=='upload'))return;fissionState.editing=null;fissionState.picker=null;renderFission();$('#fissionPanel')?.focus();}
function fissionItem(id,state=fissionState){return state.library.items.find(i=>i.id===id);}
function fissionName(id,state=fissionState){return fissionItem(id,state)?.name || state.known[id]?.name || '所选照片';}
function fissionCheck(id,state=fissionState){return state.view.product_checks?.[id];}
function fissionAnswered(id,state=fissionState){const q=fissionCheck(id,state);return q?.status==='ready' && (q.checks || []).every(c=>['yes','no','unsure'].includes(c.answer));}
function fissionBlockReason(state=fissionState){
  if(!state.view.ready && state.view.warnings?.length)return state.view.warnings[0];
  for(const c of state.view.columns)for(const id of state.choices[c.key] || []){
    if(!fissionItem(id,state))return `“${fissionName(id,state)}”这张照片已从素材库删除，点它右上角的 × 去掉，或重新选一张。`;
  }
  const errors=state.view.row_errors || [],messages=Array.isArray(errors)?errors.flatMap(e=>e.errors || (e.error?[e.error]:[])):Object.values(errors).flat();
  const materialError=messages.find(e=>!e.includes('用法问题还没回答'));if(materialError)return materialError;
  if(state.rows.length>20)return `一次最多 20 条，现在是 ${state.rows.length} 条。少选几张，或点 × 去掉一些。`;
  for(const c of state.view.columns.filter(c=>c.kind==='product'))for(const id of state.choices[c.key] || []){
    if(!fissionAnswered(id,state))return `“${fissionName(id,state)}”的用法问题还没回答，点上面的“回答问题”。`;
  }
  return messages[0] || '';
}
async function fissionSave(state=fissionState){
  const view=await api('PUT',`projects/${state.pid}/fission-draft`,{choices:state.choices,removed:state.removed,...(state.view.draft?.revision!==undefined?{revision:state.view.draft.revision}:{})});
  if(!fissionCurrent(state))return false;fissionApplyView(state,view);return true;
}
async function fissionChange(change){
  const state=fissionState;if(!fissionCurrent(state) || state.busy || state.loading)return;
  change(state);state.requestId=null;state.error='';state.conflict=false;state.busy='save';state.unsaved=true;renderFission();
  try{await fissionSave(state);}catch(error){state.conflict=/另一个窗口/.test(error.message);state.error=state.conflict?'另一个窗口已修改照片选择，这次修改没有保存。点“重新读取选择”查看最新选择后再选。':'草稿没有保存，这次修改还只在页面里。点“重试保存”后再创建：'+publicText(error.message);}
  finally{state.busy=null;if(fissionCurrent(state))renderFission();}
}
function fissionOpenPicker(key){
  const state=fissionState;if(!fissionCurrent(state) || state.busy)return;
  state.picker={key,selected:[...(state.choices[key] || [])]};renderFission();$('.fission-picker')?.focus();
}
function fissionToggleItem(id){
  const state=fissionState,p=state?.picker,c=state?.view.columns.find(c=>c.key===p?.key),item=fissionItem(id);
  if(!p || state.busy || !item || item.kind!==c.kind || (c.sha256 && c.sha256===item.sha256))return;
  p.selected=p.selected.includes(id)?p.selected.filter(i=>i!==id):[...p.selected,id];renderFission();
}
async function fissionPickerDone(){
  const p=fissionState?.picker;if(!p)return;
  return fissionChange(state=>{state.choices[p.key]=[...p.selected];state.picker=null;state.showProgress=false;});
}
async function fissionRemovePhoto(key,id){return fissionChange(state=>{state.choices[key]=(state.choices[key] || []).filter(i=>i!==id);if(state.question===id)state.question=null;});}
async function fissionDeleteRow(id){return fissionChange(state=>{if(!state.removed.includes(id))state.removed.push(id);});}
async function fissionQuestions(id){
  const state=fissionState;if(!fissionCurrent(state) || state.busy)return;
  state.question=state.question===id?null:id;if(!state.question || fissionCheck(id)?.status==='ready'){renderFission();return;}
  state.busy='questions';state.error='';renderFission();
  try{if(await fissionSave(state)){const view=await api('POST',`projects/${state.pid}/fission-draft/story-check`,{library_id:id});if(fissionCurrent(state))fissionApplyView(state,view);}}
  catch(error){state.error='商品问题没有整理完成，暂时不能创建。点“回答问题”重试：'+publicText(error.message);}
  finally{state.busy=null;if(fissionCurrent(state))renderFission();}
}
async function fissionAnswer(id,questionId,answer,note){
  const state=fissionState,check=fissionCheck(id);if(!fissionCurrent(state) || state.busy || !check)return;
  const answers=check.checks.map(q=>({id:q.id,answer:q.id===questionId && answer!==undefined?answer:q.answer || '',note:q.id===questionId && note!==undefined?note:q.note || ''}));
  state.busy='answers';state.error='';renderFission();
  try{const view=await api('PUT',`projects/${state.pid}/fission-draft/story-check`,{library_id:id,answers});if(fissionCurrent(state)){fissionApplyView(state,view);if(fissionAnswered(id,state))state.question=null;}}
  catch(error){state.error='回答没有保存，仍以之前的答案为准。请重新选择答案或填写备注：'+publicText(error.message);}
  finally{state.busy=null;if(fissionCurrent(state))renderFission();}
}
async function createFission(){
  const state=fissionState;if(!fissionCurrent(state) || state.busy || state.unsaved || !state.rows.length || fissionBlockReason(state) || !state.view.ready)return;
  state.requestId ||= 'fission-'+(globalThis.crypto?.randomUUID?.() || Date.now().toString(36)+'-'+(++fissionRequestSequence)+'-'+Math.random().toString(36).slice(2));state.busy='create';state.error='';renderFission();
  try{const result=await api('POST',`projects/${state.pid}/fission`,{choices:state.choices,removed:state.removed,request_id:state.requestId,...(state.view.draft?.revision!==undefined?{revision:state.view.draft.revision}:{})});
    if(!fissionCurrent(state))return;if(result.draft_revision!==undefined)state.view.draft.revision=result.draft_revision;state.batches=[result.batch,...state.batches.filter(b=>b.id!==result.batch.id)];
    if(result.batch.items.some(i=>i.project_id)){state.choices={};state.removed=[];state.rows=[];state.view.row_errors={};state.question=null;state.picker=null;}
    else state.error='这批新视频都没有创建成功，照片选择仍保留。查看下方失败原因后点创建重试。';
    state.requestId=null;state.showProgress=result.batch.items.some(i=>i.project_id);
    try{await refreshProjectList();}catch{state.pollError='新项目已创建；项目列表暂时没刷新，可以点下方“打开”进入项目。';}
  }catch(error){state.error='这次创建没有完成，已保存的草稿仍在。检查原因后点创建重试：'+publicText(error.message);}
  finally{state.busy=null;if(fissionCurrent(state)){renderFission();scheduleFissionPoll();}}
}
function scheduleFissionPoll(){
  clearTimeout(fissionTimer);fissionTimer=null;const state=fissionState,active=s=>['preparing','queued','running','pending','pending_confirmation','ready','awaiting_confirmation','generating'].includes(s);
  if(fissionCurrent(state) && state.open && state.batches.some(b=>b.items?.some(i=>active(i.status) || !i.status && i.segments?.some(s=>active(s.status)))))fissionTimer=setTimeout(()=>fissionPoll(),2000);
}
async function fissionPoll(){
  clearTimeout(fissionTimer);const state=fissionState;if(!fissionCurrent(state) || !state.open)return;
  const revision=state.pollRevision=(state.pollRevision || 0)+1;
  try{const batches=await Promise.all(state.batches.map(b=>api('GET',`batches/${b.id}`)));if(fissionCurrent(state) && state.pollRevision===revision){const updates=new Map(batches.map(b=>[b.id,b]));state.batches=state.batches.map(b=>updates.get(b.id) || b);state.pollError='';renderFission();}}
  catch(error){if(fissionCurrent(state) && state.pollRevision===revision){state.pollError='进度暂时没取到，后台准备不受影响。点“刷新进度”再查：'+publicText(error.message);renderFission();}}
  finally{if(fissionCurrent(state) && state.pollRevision===revision)scheduleFissionPoll();}
}
async function retryFission(projectId){
  const state=fissionState;if(!fissionCurrent(state) || state.busy)return;state.busy='retry';state.error='';renderFission();
  try{await api('POST',`projects/${projectId}/fission-retry`,{});if(fissionCurrent(state))await fissionPoll();}
  catch(error){state.error='重试没有启动，已有分镜预览保留。点“重试失败段”再试：'+publicText(error.message);}
  finally{state.busy=null;if(fissionCurrent(state))renderFission();}
}
function fissionPickerHtml(state,disabled){
  const p=state.picker;if(!p)return '';const c=state.view.columns.find(c=>c.key===p.key),label=c.kind==='person'?'人物':'商品';
  return `<section class="fission-picker" role="dialog" aria-modal="true" aria-label="选新${label}" tabindex="-1"><header class="fission-head"><h3 class="material-name-heading" title="选新${label}，换掉“${esc(c.master_name)}”">选新${label}，换掉“${esc(c.master_name)}”</h3><button class="small ghost" data-fission-picker-close ${state.busy==='upload'?'':disabled}>取消</button></header>${state.uploadProgress || state.uploadNotice?`<p role="status">${esc(state.uploadProgress || state.uploadNotice)}</p>`:''}${state.error?`<p class="caption-warning library-upload-errors" role="status">${esc(state.error)}</p>`:''}<div class="fission-pick-grid"><label class="fission-upload">${state.uploadProgress?esc(state.uploadProgress):'＋ 上传新照片'}<input type="file" accept="image/*" multiple data-fission-upload="${c.kind}" ${disabled}></label>${state.library.items.filter(i=>i.kind===c.kind).map(i=>{const master=!!c.sha256 && c.sha256===i.sha256,on=p.selected.includes(i.id);return `<article class="library-pick-card" data-library-photo="${esc(i.id)}"><button class="ghost fission-pick-item ${on?'selected':''}" data-fission-item="${esc(i.id)}" aria-label="选择${esc(i.name)}" aria-pressed="${on}" ${master?'disabled':disabled}><img src="${esc(i.url || '')}" alt="">${master?'<small>母版在用</small>':''}</button>${libraryNameHtml(i,state,disabled,'fission',on?'✓ ':'')}</article>`;}).join('')}</div><footer class="fission-foot"><span>已选 ${p.selected.length} 张</span><button data-fission-picker-done ${disabled}>完成</button></footer></section>`;
}
function fissionQuestionsHtml(id,state,disabled){
  const check=fissionCheck(id,state),done=fissionAnswered(id,state),open=state.question===id,name=materialNameHtml(fissionName(id,state));
  if(done && !open)return `<p class="fission-answered">${name} <button class="link-button" data-fission-check="${esc(id)}" ${disabled}>改答案</button></p>`;
  return `<section class="fission-check" data-fission-product="${esc(id)}"><p>“${name}”${done?'的用法问题':'要先回答几个用法问题，生成时才知道怎么拿、怎么用。'} <button class="link-button" data-fission-check="${esc(id)}" ${disabled}>${done?'收起':'回答问题'}</button></p>${check?.status!=='ready'?'<small>整理问题会调用一次推理</small>':''}${open?(state.busy==='questions'?'<p role="status">正在根据母版剧情整理问题…</p>':check?.status==='ready'?`${(check.checks || []).map(q=>`<div class="fission-question"><b>${esc(q.question)}</b><p class="hint">${esc(q.feature || '')} ${esc(q.where || '')}</p><div class="row wrap">${[['yes','能'],['no','不能'],['unsure','不确定']].map(([value,label])=>`<button class="small ${q.answer===value?'':'ghost'}" data-fission-answer="${value}" data-item="${esc(id)}" data-question="${esc(q.id)}" aria-pressed="${q.answer===value}" ${disabled}>${label}</button>`).join('')}</div><label>备注（可不填）<input data-fission-note="${esc(q.id)}" data-item="${esc(id)}" value="${esc(q.note || '')}" ${disabled}></label></div>`).join('') || '<p>没有需要确认的问题，可以创建。</p>'}<small>答“不能”的动作不会画进新视频；答“不确定”就保持母版的样子。</small>`:''):''}</section>`;
}
function fissionPhoto(url,name,extra='',same=false){return `<div class="fission-photo ${same?'same':''}" title="${esc(name)}">${url?`<img src="${esc(url)}" alt="${esc(name)}">`:`<span class="fission-photo-empty">${materialNameHtml(name)}</span>`}<span class="fission-photo-name" title="${esc(name)}">${esc(name)}</span>${extra}</div>`;}
function fissionFormula(state){
  const chosen=state.view.columns.filter(c=>state.choices[c.key]?.length);
  if(chosen.length===1){const c=chosen[0],n=state.choices[c.key].length;return `选了 ${n} 个新${c.kind==='person'?'人物，每人':'商品，每个商品'}一条；不想要的点 × 去掉`;}
  return chosen.map(c=>`${state.choices[c.key].length} 个新${c.kind==='person'?'人物':'商品'}`).join(' × ')+(chosen.length?'；不想要的点 × 去掉':'');
}
function fissionBatchSlots(item,state){
  if(item.slots?.length)return item.slots;
  return state.view.columns.map(c=>{const id=item.selections?.[c.key],photo=id&&(fissionItem(id,state) || state.known[id]);return {key:c.key,name:id?photo?.name || '所选照片':c.master_name,source_name:c.source_name || c.key,file:id?null:c.file,url:photo?.url,changed:!!id};});
}
function fissionProgressHtml(state,disabled,batches=state.batches){return batches.map(batch=>`<section class="fission-progress">${(batch.items || []).map(item=>{
  const failed=item.error || ['failed','error','interrupted','image_failed'].includes(item.status),label=item.status==='completed'?'已生成新分镜':item.status==='generating'?'新分镜生成中':item.status==='image_failed'?'生图失败，打开第 04 步重试':failed?'失败':['pending_confirmation','ready','awaiting_confirmation'].includes(item.status)?'等你确认付费':'整理改法中',slots=fissionBatchSlots(item,state);
  return `<div class="fission-progress-row" role="status"><div class="fission-thumbnails">${slots.map(slot=>fissionPhoto(slot.url || (slot.file?'/files/'+encodeURIComponent(item.project_id || state.pid)+'/'+slot.file.split('/').map(encodeURIComponent).join('/'):''),slot.name,'',!slot.changed)).join('')}</div><div class="fission-progress-copy"><b>裂变 ${item.fission_number || item.row}</b><span class="fission-changes" title="${esc(slots.filter(s=>s.changed).map(s=>(s.key==='product'?'商品':s.key)+' → '+s.name).join('，'))}">换了：${esc(slots.filter(s=>s.changed).map(s=>(s.key==='product'?'商品':s.key)+' → '+s.name).join('，') || '沿用母版设定')}</span><span>${label}</span>${failed?errorHtml(item.error || item.segments?.find(s=>s.error)?.error || '部分段没准备好，点重试继续。','推理'):''}</div>${item.project_id?`<button class="small ghost" data-fission-project="${esc(item.project_id)}" ${disabled}>打开</button>`:''}${(failed || item.can_retry) && item.project_id && item.status!=='image_failed' && !/项目已删除/.test(item.error || '')?`<button class="small ghost" data-fission-retry="${esc(item.project_id)}" title="${item.can_retry===false?'正在整理改法，完成后再重试':'重试没有准备好的段；会调用推理'}" ${disabled || (item.can_retry===false?'disabled':'')}>重试</button>`:''}</div>`;
}).join('')}</section>`).join('');}
async function fissionNewBatch(){
  if(!fissionCurrent() || fissionState.busy)return;
  await fissionChange(state=>{state.choices={};state.removed=[];state.rows=[];state.question=null;state.picker=null;state.showProgress=false;});
}
function fissionBusyHtml(state){const text={save:'正在保存照片选择…',answers:'正在保存回答…',retry:'正在启动重试，整理改法会调用推理…',create:'正在创建新项目…'}[state.busy];return text?`<p class="hint" role="status">${text}</p>`:'';}
function renderFission(){
  const box=$('#fissionPanel'),state=fissionState;if(!box)return;box.hidden=!fissionCurrent(state) || !state.open;if(box.hidden){clearTimeout(fissionTimer);return;}
  if(state.showProgress && state.batches.length){
    const batch=state.batches[0],disabled=state.busy?'disabled':'';
    updateHtml(box,`<header class="fission-head"><h2>已创建 ${(batch.items || []).filter(i=>i.project_id).length} 条新视频</h2></header><div class="fission-body">${fissionBusyHtml(state)}<p>每条新视频是一个单独的项目，在顶部项目列表里，挂在母版下面。系统正在为每条整理新分镜的改法（调用推理），整理好后进项目第 04 步确认付费，生成新分镜。可以先关掉这个窗口，准备会在后台继续。</p>${fissionProgressHtml(state,disabled,[batch])}${state.error?errorHtml(state.error):''}${state.pollError?errorHtml(state.pollError)+'<button class="small ghost" data-fission-refresh>刷新进度</button>':''}</div><footer class="fission-foot"><button class="small ghost" data-fission-close ${disabled}>关闭</button><button class="ghost" data-batch-board="${esc(state.pid)}" ${disabled}>查看批量看板</button><button data-fission-new-batch ${disabled}>再出一批</button></footer>`);return;
  }
  const disabled=state.busy?'disabled':'',columns=state.view.columns,reason=state.error || (state.unsaved?'草稿尚未保存，点“重试保存”后再创建。':fissionBlockReason(state));
  const cards=columns.map(c=>{const selected=state.choices[c.key] || [],name=c.master_name;return `<article class="fission-role" data-fission-slot="${esc(c.key)}"><h3 title="${esc(c.title || c.source_name || c.name)}">${esc(c.title || (c.kind==='person'?'人物 · '+(c.source_name || c.name || c.key):'商品 · '+(c.source_name || '原片商品')))}</h3>${c.source_description?`<p class="fission-role-description hint" title="${esc(c.source_description)}">${esc(c.source_description)}</p>`:''}<p class="fission-master-status" title="${esc(c.master_status || name)}">${esc(c.master_status || (c.action==='keep'?'母版没换，用的是原片里的'+(c.kind==='person'?'人':'商品'):'母版已换成：'+name))}</p><div class="fission-photos">${fissionPhoto(c.file?fileUrl(c.file):'',name,'<small class="photo-badge">母版</small>')}<span class="fission-arrow">→</span>${selected.map(id=>{const item=fissionItem(id,state);return fissionPhoto(item?.url,fissionName(id,state),`<button class="photo-remove" data-fission-remove="${esc(id)}" data-key="${esc(c.key)}" aria-label="移除${esc(fissionName(id,state))}" ${disabled}>×</button>${c.kind==='product'?`<small class="photo-badge ${fissionAnswered(id,state)?'answered':'todo'}">${fissionAnswered(id,state)?'✓ 已回答':'待回答'}</small>`:''}`);}).join('')}<button class="ghost fission-add-photo" data-fission-picker="${esc(c.key)}" ${disabled}>＋ 选照片</button></div>${!selected.length?`<p class="hint">没选，保持母版的${materialNameHtml(name)}</p>`:''}${c.kind==='product'?selected.map(id=>fissionQuestionsHtml(id,state,disabled)).join(''):''}</article>`;}).join('');
  const rows=state.rows.map((row,index)=>{const kept=columns.filter(c=>!row.selections[c.key]),changed=[...new Set(columns.filter(c=>row.selections[c.key]).map(c=>c.kind==='person'?'人物':'商品'))];return `<div class="fission-combination" data-fission-combination="${esc(row.id)}"><b>新视频 ${index+1}</b><div class="fission-thumbnails">${columns.map(c=>{const id=row.selections[c.key],item=fissionItem(id,state),name=id?fissionName(id,state):c.master_name;return fissionPhoto(id?item?.url:c.file?fileUrl(c.file):'',name,'',!id);}).join('')}</div><div class="fission-combination-names" title="${esc(columns.map(c=>row.selections[c.key]?fissionName(row.selections[c.key],state):c.master_name).join(' + '))}">${columns.map(c=>esc(row.selections[c.key]?fissionName(row.selections[c.key],state):c.master_name)).join(' + ')}<small>换了${changed.join('和')}${kept.length?'，'+kept.map(c=>esc(c.master_name)).join('、')+'不换':''}</small></div><button class="ghost small" data-fission-delete="${esc(row.id)}" aria-label="去掉新视频 ${index+1}" ${disabled}>×</button></div>`;}).join('');
  updateHtml(box,`<header class="fission-head"><h2>批量裂变</h2><span class="hint">母版：${esc(state.name)}</span><button class="small ghost" data-fission-close ${state.busy==='upload'?'':disabled}>关闭</button></header>${state.loading?'<div class="fission-body" role="status">正在读取照片和草稿…</div>':`<div class="fission-body">${fissionBusyHtml(state)}${!state.picker && (state.uploadProgress || state.uploadNotice)?`<p role="status">${esc(state.uploadProgress || state.uploadNotice)}</p>`:''}<p class="fission-lead">照这条视频再出几条新视频：分镜、镜头、节奏都照搬，只换你选的人或商品。</p><h3 class="fission-step">① 选新的人或商品 <small>每个位置可以选好几张；不选就保持母版的</small></h3><p class="hint">想换谁，就在谁的卡片里选照片；不换的不用动。</p><div class="fission-roles">${cards}</div><h3 class="fission-step">② 会出这些新视频（${state.rows.length} 条） <small>${esc(fissionFormula(state))}</small></h3><div class="fission-combinations">${rows || '<p class="fission-empty">还没选新照片。在上面点“＋ 选照片”，每选一张就多一条新视频。</p>'}</div>${state.rows.length && state.batches.length?`<details><summary>之前创建的进度</summary>${fissionProgressHtml(state,disabled)}</details>`:''}${state.pollError?`<p class="caption-warning">${esc(state.pollError)}</p><button class="small ghost" data-fission-refresh>刷新进度</button>`:''}</div>`}<footer class="fission-foot"><span class="hint">创建后，每条新视频先整理新分镜的改法（调用推理）；生成新分镜和视频前都会再请你确认付费。</span>${reason?`<span class="caption-warning fission-block-reason library-upload-errors" role="status">${esc(reason)}</span>`:''}${state.unsaved && !state.busy?(state.conflict?'<button class="small ghost" data-fission-reload>重新读取选择</button>':'<button class="small ghost" data-fission-save>重试保存</button>'):''}<button data-fission-create ${state.loading || state.busy || state.unsaved || !state.view.ready || !state.rows.length || fissionBlockReason(state)?'disabled':''}>${state.busy==='create'?'正在创建…':state.rows.length?`创建 ${state.rows.length} 条新视频`:'先选新照片'}</button></footer>${fissionPickerHtml(state,disabled)}`);
}
function rememberLibraryPhoto(items,item){
  const merged={...items.find(i=>i.id===item.id),...item};
  const index=items.findIndex(i=>i.id===item.id);if(index<0)items.push(merged);else items[index]=merged;
  return merged;
}
async function uploadPhotos(files,kind,progress,accept){
  const result={uploaded:0,duplicates:0,errors:[]};
  for(const [index,file] of files.entries()){
    progress(`正在上传 ${index+1}/${files.length}：${file.name}…`);
    try{
      const photo=await uploadPhoto(`library?kind=${kind}`,file);
      for(const pending of libraryReads)pending.set(photo.item.id,photo.item);
      accept(photo.item);result.uploaded++;if(photo.duplicate)result.duplicates++;
    }catch(error){
      const reason=publicText(error.message || String(error));
      result.errors.push(reason.startsWith(`“${file.name}”`)?reason:`“${file.name}”没有上传：${reason}`);
    }
  }
  result.notice=result.uploaded?`已上传 ${result.uploaded} 张`+(result.duplicates?`；其中 ${result.duplicates} 张已在素材库`:''):'';
  return result;
}
function photoUploadToast(notice,error){toast('素材库：'+[notice,error].filter(Boolean).join('\n'),!!error);}
async function fissionUpload(input){
  const state=fissionState,files=[...(input.files || [])],picker=state?.picker;if(!files.length || !picker || state.busy)return;
  const kind=input.dataset.fissionUpload;input.value='';
  state.busy='upload';state.error='';state.uploadNotice='';
  const visible=()=>fissionCurrent(state) && state.open && state.picker===picker;
  try{
    const result=await uploadPhotos(files,kind,text=>{state.uploadProgress=text;if(fissionCurrent(state))renderFission();},item=>{
      item=rememberLibraryPhoto(state.library.items,item);state.known[item.id]=item;
      if(libraryState){rememberLibraryPhoto(libraryState.items,item);renderLibrary();}
      const c=state.view.columns.find(c=>c.key===picker.key);
      if(visible() && c.sha256!==item.sha256 && !picker.selected.includes(item.id))picker.selected.push(item.id);
      if(fissionCurrent(state))renderFission();
    });
    state.uploadNotice=result.notice;state.error=result.errors.length?`${result.errors.length} 张没有上传：\n${result.errors.join('\n')}`:'';
  }catch(error){state.error='上传处理没有完成：'+publicText(error.message);}
  finally{
    state.busy=null;state.uploadProgress='';
    if(!visible())photoUploadToast(state.uploadNotice,state.error);
    if(fissionCurrent(state))renderFission();
  }
}
async function openLibrary(mode='manage'){
  if(libraryState?.busy){libraryState.open=true;renderLibrary();$('#libraryPanel')?.focus();return;}
  const state={items:[],open:true,busy:true,error:'',notice:'',tab:'person',mode,selected:[],pid:P?.id,editing:null};libraryState=state;
  try{renderLibrary();await readLibrary(next=>Object.assign(state,next));}
  catch(error){state.error='素材库暂时没有打开，已有照片不受影响。关闭后再打开：'+publicText(error.message);}
  finally{state.busy=false;if(libraryState===state){renderLibrary();if(state.open)$('#libraryPanel')?.focus();}}
}
function closeLibrary(){if(!libraryState)return;libraryState.open=false;renderLibrary();$('#fissionPanel:not([hidden])')?.focus();}
function materialNameHtml(name){return `<span class="material-name-inline" title="${esc(name)}">${esc(name)}</span>`;}
function libraryNameHtml(item,state,disabled='',scope='library',prefix=''){
  return `<div class="library-name">${state.editing===item.id?`<input data-library-name="${esc(item.id)}" data-library-scope="${scope}" aria-label="照片名字" value="${esc(state.editValue ?? item.name)}" ${disabled}>`:`<span title="${esc(item.name)}">${esc(prefix+item.name)}</span><button class="link-button" data-library-rename="${esc(item.id)}" data-library-scope="${scope}" aria-label="改名${esc(item.name)}" ${disabled}>✎</button>`}</div>`;
}
function renderLibrary(){
  const box=$('#libraryPanel'),state=libraryState;if(!box)return;box.hidden=!state?.open;if(box.hidden)return;
  const disabled=state.busy?'disabled':'',project=state.mode==='project',kind=state.tab,label=kind==='person'?'人物':'商品';
  const photos=state.items.filter(i=>i.kind===kind).map(item=>project?
    `<article class="library-pick-card" data-library-photo="${esc(item.id)}"><button class="ghost fission-pick-item ${state.selected.includes(item.id)?'selected':''}" data-library-select="${esc(item.id)}" aria-label="选择${esc(item.name)}" aria-pressed="${state.selected.includes(item.id)}" ${disabled}><img src="${esc(item.url || '')}" alt=""></button>${libraryNameHtml(item,state,disabled,'library',state.selected.includes(item.id)?'✓ ':'')}</article>`:
    `<article class="library-item" data-library-photo="${esc(item.id)}"><img src="${esc(item.url || '')}" alt="${esc(item.name)}">${libraryNameHtml(item,state,disabled)}${item.project_count>0?`<small class="hint">用在 ${item.project_count} 个项目</small>`:''}<button class="small ghost library-delete" data-library-delete="${esc(item.id)}" ${disabled}>删除</button></article>`).join('');
  updateHtml(box,`<header class="fission-head"><h2>${project?'从素材库添加':'素材库'}</h2><span class="hint">这里的照片所有项目都能用</span><button class="small ghost" data-library-close>${project?'取消':'关闭'}</button></header><div class="fission-body"><div class="row">${['person','product'].map(k=>`<button class="small ${kind===k?'':'ghost'}" data-library-tab="${k}" aria-pressed="${kind===k}" ${disabled}>${k==='person'?'人物':'商品'}（${state.items.filter(i=>i.kind===k).length}）</button>`).join('')}</div>${state.uploadProgress?`<p role="status">${esc(state.uploadProgress)}</p>`:state.busy?'<p class="hint" role="status">正在读取或更新素材库，请稍候…</p>':''}${state.error?`<p class="caption-warning library-upload-errors" role="status">${esc(state.error)}</p>`:''}${state.notice?`<p role="status">${esc(state.notice)}</p>`:''}<div class="library-items">${!project?`<label class="fission-upload">${state.uploadProgress?esc(state.uploadProgress):`＋ 上传${label}照片<small>可多选</small>`}<input type="file" accept="image/*" multiple data-library-upload="${kind}" ${disabled}></label>`:''}${photos}</div></div>${project?`<footer class="fission-foot"><span>已选 ${state.selected.length} 张</span><button data-library-import ${state.busy || !state.selected.length?'disabled':''}>添加到这个项目</button></footer>`:''}`);
}
function libraryRenameState(scope){return scope==='fission'?fissionState:libraryState;}
function libraryStartRename(id,scope='library'){
  const s=libraryRenameState(scope);if(!s || s.busy)return;
  const item=(scope==='fission'?s.library.items:s.items).find(i=>i.id===id);if(!item)return;
  s.editing=id;s.editValue=item.name;scope==='fission'?renderFission():renderLibrary();
  const input=$(`[data-library-name="${id}"][data-library-scope="${scope}"]`);input?.focus();input?.select?.();
}
function rememberLibraryRename(item){
  for(const pending of libraryReads)pending.set(item.id,item);
  if(libraryState)rememberLibraryPhoto(libraryState.items,item);
  if(fissionState?.library){rememberLibraryPhoto(fissionState.library.items,item);fissionState.known[item.id]=item;}
}
async function libraryRename(input,cancel=false){
  const scope=input?.dataset.libraryScope || 'library',s=libraryRenameState(scope),id=input?.dataset.libraryName;
  if(!id || !s || s.busy || s.editing!==id)return;
  const render=()=>{if(scope==='fission'){if(fissionState===s)renderFission();}else if(libraryState===s)renderLibrary();};
  const item=(scope==='fission'?s.library.items:s.items).find(i=>i.id===id),name=input.value.trim();
  // End editing before morphing the DOM, so blur cannot submit a second PATCH.
  s.editing=null;s.editValue=null;
  if(cancel || name===item?.name){render();return;}
  if(scope==='library'){
    s.editValue=name;
    const ok=await libraryMutation(async()=>{const result=await libraryRequest('PATCH',`library/${id}`,{name});rememberLibraryRename({...item,name,...result});});
    if(!ok){s.editing=id;render();}return;
  }
  s.busy='rename';s.error='';
  try{
    render();
    const result=await libraryRequest('PATCH',`library/${id}`,{name});
    rememberLibraryRename({...item,name,...result});
  }catch(error){s.error='照片名字没有保存，请重试：'+publicText(error.message);s.editing=id;s.editValue=name;}
  finally{s.busy=scope==='fission'?null:false;renderLibrary();renderFission();}
}
async function libraryMutation(action){
  const state=libraryState;if(!state || state.busy)return;state.busy=true;state.error='';
  try{renderLibrary();await action();const next=await readLibrary(next=>Object.assign(state,next));
    librarySaved.clear();if(P){
      // The mutation already succeeded. A stale project badge is not a failure.
      const pid=P.id;try{const fresh=await libraryRequest('GET',`projects/${pid}`);if(P?.id===pid)P.library_saved_assets=fresh.library_saved_assets || [];}catch{}
    }
    if(fissionCurrent()){fissionState.library=next;Object.assign(fissionState.known,Object.fromEntries(next.items.map(i=>[i.id,i])));renderFission();}return true;
  }catch(error){state.error='素材库操作没有完成，项目中的照片不受影响。请按原按钮重试：'+publicText(error.message);return false;}
  finally{state.busy=false;if(libraryState===state)renderLibrary();if(P)renderReplacements(true);}
}
async function libraryUpload(input){
  const state=libraryState,files=[...(input.files || [])];if(!files.length || !state || state.busy)return;
  const kind=input.dataset.libraryUpload;input.value='';state.busy=true;state.uploading=true;state.error='';state.notice='';state.editing=null;let lastUploaded=null;
  try{
    const result=await uploadPhotos(files,kind,text=>{state.uploadProgress=text;if(libraryState===state)renderLibrary();},item=>{
      item=rememberLibraryPhoto(state.items,item);lastUploaded=item.id;if(libraryState===state)renderLibrary();
      if(fissionCurrent()){rememberLibraryPhoto(fissionState.library.items,item);fissionState.known[item.id]=item;renderFission();}
    });
    state.notice=result.notice;state.error=result.errors.length?`${result.errors.length} 张没有上传：\n${result.errors.join('\n')}`:'';
  }catch(error){state.error='上传处理没有完成：'+publicText(error.message);}
  finally{
    state.busy=false;state.uploading=false;state.uploadProgress='';
    if(libraryState!==state || !state.open)photoUploadToast(state.notice,state.error);
    if(libraryState===state){renderLibrary();if(state.open && lastUploaded)libraryStartRename(lastUploaded);}
  }
}
async function libraryImport(){
  const state=libraryState;if(!state || state.busy || state.pid!==P?.id)return;state.busy=true;state.error='';state.notice='';
  try{renderLibrary();for(const id of [...state.selected]){const result=await libraryRequest('POST',`projects/${state.pid}/library-import`,{library_id:id});state.selected=state.selected.filter(i=>i!==id);state.notice=result.duplicate?'这张已在项目里':`已添加“${state.items.find(i=>i.id===id)?.name || '照片'}”`;if(P?.id===state.pid){P.assets ||= [];if(!P.assets.some(a=>a.id===result.asset.id))P.assets.push(result.asset);renderAssets();renderReplacements(true);}if(libraryState===state)renderLibrary();}
  }catch(error){state.error='有照片没有添加，已完成的照片保留在项目里。点“添加到这个项目”重试：'+publicText(error.message);}
  finally{state.busy=false;if(libraryState===state)renderLibrary();}
}
async function libraryDelete(id){
  const state=libraryState;if(!state || state.busy)return;
  if(!await pageConfirm('从素材库删除这张照片？已经用过它的项目不受影响。'))return;
  if(libraryState!==state || !state.open || state.busy)return;
  return libraryMutation(()=>libraryRequest('DELETE',`library/${id}`));
}

// ---------- casting settings (step 3) ----------
const castDrafts = new Map(), castResults = new Map(), castBusy = new Set(), castErrors = new Map();
const CASTING_UNSAVED_MESSAGE='选角或商品改了还没保存，点“保存”后，后面生成才会用新设定。';
function warnUnsavedCasting(){if(!dirty.rep)return false;toast(CASTING_UNSAVED_MESSAGE,true);return true;}
function castKey(key) {return `${P.id}:${key}`;}
function castEntry(rep,key) {return key==='product' ? rep.product || {} : (rep.characters || []).find(c=>c.source_id===key) || {};}
function castingDraft() {return castDrafts.get(P.id) || P.replacements || {};}
function visibleCastingCharacters(rep){const ids=new Set((P.breakdown?.characters || []).map(c=>c.id));return (rep?.characters || []).filter(c=>ids.has(c.source_id) || c.action==='add');}
function productBinding(entry) {return JSON.stringify([entry.asset_id || null,entry.description || '']);}
function castRecognitionHtml(key) {
  const id=castKey(key),busy=castBusy.has(id),result=castResults.get(id),error=castErrors.get(id);
  return `<button class="small ghost" data-cast-identify="${esc(key)}" ${busy?'disabled':''}>${busy?'正在识别…':error?'重试':'从照片识别外形'}</button>${error?`<p class="caption-warning" role="status">${esc(error)}</p>`:''}${result?`<div class="cast-suggestion"><p>识别结果：${esc(result)}</p><button class="small ghost" data-cast-apply="${esc(key)}" data-mode="replace">用这段替换</button> <button class="small ghost" data-cast-apply="${esc(key)}" data-mode="append">加到后面</button></div>`:''}`;
}
function storyChecksHtml(product) {
  if(product.action!=='replace')return '';
  const checks=product.story_checks || [],unanswered=checks.filter(c=>!c.answer).length;
  const changed=product.story_checks_sig && (P.story_checks_stale || productBinding(product)!==productBinding(P.replacements?.product || {}));
  const busy=castBusy.has(castKey('story')),error=castErrors.get(castKey('story'));
  return `<section class="story-checks"><h4>剧情检查</h4><p class="hint">原片的剧情可能靠旧商品的某个功能推动。点一下，让模型列出来，你确认新商品支不支持。</p><button class="small ghost" data-story-check ${busy?'disabled':''}>${busy?'正在检查…':error?'重试':'检查剧情是否还成立'}</button>
  ${error?`<p class="caption-warning" role="status">${esc(error)}</p>`:''}${changed?'<p class="caption-warning">商品改过，建议检查</p>':''}
  ${checks.map(c=>`<div class="story-check" data-story-question="${esc(c.id)}"><b>${esc(c.feature)}</b><p class="hint">${esc(c.where)}</p><p>${esc(c.question)}</p><div class="row wrap">${[['yes','支持'],['no','不支持'],['unsure','不确定']].map(([value,label])=>`<label class="check"><input type="radio" name="story-${esc(c.id)}" data-story-answer="${esc(c.id)}" value="${value}" ${c.answer===value?'checked':''}> ${label}</label>`).join('')}</div>${c.answer==='no'?`<label>那这里改成什么 <input type="text" data-story-note="${esc(c.id)}" value="${esc(c.note || '')}"></label>`:''}</div>`).join('')}
  ${unanswered?`<p class="caption-warning" data-story-unanswered>还有 ${unanswered} 条没确认，在第 04 步写提示词时会按“不确定”处理</p>`:''}${product.story_checks_sig && !checks.length?'<p class="hint">没有发现需要确认的剧情功能。</p>':''}</section>`;
}
function sourceProductNames(){
  if(P.source_product)return P.source_product;
  const groups=new Map(),variants=[];
  for(const row of P.breakdown?.productMoments || []){const raw=String(row.product || ''),key=raw.replace(/\s+/g,'').toUpperCase();if(!key)continue;if(!variants.includes(raw))variants.push(raw);if(!groups.has(key))groups.set(key,{name:raw,count:0});groups.get(key).count++;}
  return {name:[...groups.values()].sort((a,b)=>b.count-a.count)[0]?.name || '',variants};
}
function renderReplacements(force) {
  const rep=castingDraft(),chars=P.breakdown?.characters || [];
  const key=JSON.stringify([P.id,rep,P.breakdown,P.assets,P.story_checks_stale,[...castResults],[...castBusy],[...castErrors]]);
  if(!force && seen.rep===key)return;seen.rep=key;
  const photo=(key,r)=>{const file=P.assets?.find(a=>a.id===r.asset_id)?.file;return `${file?`<button class="cast-photo ghost" data-camera-zoom="${esc(file)}" data-camera-caption="${esc(key==='product'?'新商品照片':'新人物照片')}"><img src="${fileUrl(file)}" alt="${key==='product'?'新商品照片':'新人物照片'}"></button>${librarySaveHtml(key,r)}`:''}<label>照片 <select data-cast-asset="${esc(key)}">${scenePhotoOptions(key==='product'?'product':'character',r.asset_id)}</select></label>`;};
  const card=(c,r,added=false)=>{
    const key=c.id,on=added || r.action==='replace';
    return `<article class="cast-card" data-cast-card="${esc(key)}"><h3>${esc(added?'新人物 '+key:key+' '+(c.name || ''))}</h3>${added?`<label>名字 <input data-cast-name="${esc(key)}" type="text" value="${esc(r.name || '')}"></label>`:`<p class="hint">原片里的样子：${esc(joinDisplayText([c.appearance,c.outfit]) || '拆解里没有说明')}</p><label>处理 <select data-cast-action="${esc(key)}"><option value="keep">不换</option><option value="replace" ${on?'selected':''}>换</option></select></label>`}
    ${on?`<div class="cast-fields">${photo(key,r)}<label>描述 <textarea rows="3" data-cast-description="${esc(key)}" placeholder="外形、发型、服装、年龄等，可以手写">${esc(r.description || '')}</textarea></label>${castRecognitionHtml(key)}</div>`:''}${added?`<button class="small ghost" data-cast-remove="${esc(key)}">移除这个新人物</button>`:''}</article>`;
  };
  const prod=rep.product || {},products=sourceProductNames();
  updateHtml($('#replacementForm'),`${!P.breakdown?'<p class="hint">完成拆解后，这里会列出原片里的人物。</p>':''}<div class="cast-cards">${chars.map(c=>card(c,castEntry(rep,c.id))).join('')}${(rep.characters || []).filter(c=>c.action==='add' && !chars.some(role=>role.id===c.source_id)).map(r=>card({id:r.source_id},r,true)).join('')}</div>
    <button class="ghost" data-cast-add>加一个原片里没有的人</button>
    <article class="cast-card" data-cast-card="product"><h3>商品</h3><p class="hint">原片里的商品：${esc(products.name || '没有商品说明')}</p>${products.variants.length?`<details class="hint" data-product-variants><summary>（拆解里共 ${products.variants.length} 种写法，点开看）</summary><ul>${products.variants.map(v=>`<li>${esc(v)}</li>`).join('')}</ul></details>`:''}<label>处理 <select data-cast-action="product"><option value="keep">不换</option><option value="replace" ${prod.action==='replace'?'selected':''}>换</option></select></label>
    ${prod.action==='replace'?`<div class="cast-fields">${photo('product',prod)}<label>商品说明 <textarea id="rep-product-note" rows="3" data-cast-description="product" placeholder="写清楚是什么商品、外形和用法">${esc(prod.description || '')}</textarea></label>${castRecognitionHtml('product')}</div>`:''}${storyChecksHtml(prod)}</article>`);
  $('#repHint').textContent=dirty.rep?CASTING_UNSAVED_MESSAGE:'';
  renderCompletedSteps();renderSegmentBar();
}
function readReplacements() {
  const rep=clone(castingDraft());rep.characters ||= [];
  for(const card of $$('[data-cast-card]',$('#replacementForm'))) {
    const key=card.dataset.castCard,entry={...castEntry(rep,key)};
    const action=$('[data-cast-action]',card),asset=$('[data-cast-asset]',card),description=$('[data-cast-description]',card),name=$('[data-cast-name]',card);
    if(action)entry.action=action.value;else if(key!=='product')entry.action='add';
    if(asset)entry.asset_id=asset.value || null;
    if(description)entry.description=description.value;
    if(name)entry.name=name.value;
    if(key==='product') {
      entry.story_checks=(entry.story_checks || []).map(check=>{
        const answer=$$('[data-story-answer]',card).find(el=>el.dataset.storyAnswer===check.id && el.checked);
        const note=$$('[data-story-note]',card).find(el=>el.dataset.storyNote===check.id);
        return {...check,answer:answer?.value || check.answer || '',note:note?.value ?? check.note ?? ''};
      });
      rep.product=entry;
    } else {
      entry.source_id=key;const i=rep.characters.findIndex(c=>c.source_id===key);
      if(i<0)rep.characters.push(entry);else rep.characters[i]=entry;
    }
  }
  return rep;
}
function captureCasting() {const rep=readReplacements();castDrafts.set(P.id,rep);dirty.rep=true;return rep;}
function changeCasting(el) {
  if(!el.closest('#replacementForm') || !['data-cast-action','data-cast-asset','data-cast-description','data-cast-name','data-story-answer','data-story-note'].some(a=>el.hasAttribute(a)))return false;
  // Keep radio state deterministic in the small offline DOM as well as browsers.
  if(el.dataset.storyAnswer)$$('[data-story-answer]').filter(n=>n.dataset.storyAnswer===el.dataset.storyAnswer).forEach(n=>n.checked=n===el);
  const key=el.dataset.castAsset,previous=key?castEntry(castingDraft(),key).asset_id:null;
  const rep=captureCasting();
  if(key && castEntry(rep,key).asset_id!==previous){delete castEntry(rep,key).look;castResults.delete(castKey(key));castErrors.delete(castKey(key));}
  renderReplacements(true);return true;
}
function addCastPerson() {
  const rep=captureCasting(),ids=new Set([...(P.breakdown?.characters || []).map(c=>c.id),...rep.characters.map(c=>c.source_id)]);
  let n=1;while(ids.has('N'+n))n++;
  rep.characters.push({source_id:'N'+n,action:'add',name:'',asset_id:null,description:''});renderReplacements(true);
}
function applyCastSuggestion(key,mode) {
  const rep=captureCasting(),entry=castEntry(rep,key),result=castResults.get(castKey(key));if(!result)return;
  entry.description=mode==='append' && entry.description ? entry.description+'\n'+result : result;
  castResults.delete(castKey(key));renderReplacements(true);
}
async function requestCasting(key,story=false) {
  const pid=P.id,id=castKey(story?'story':key);if(castBusy.has(id))return;
  const draft=captureCasting(),entry=clone(castEntry(draft,key)),binding=JSON.stringify(entry);
  castBusy.add(id);castErrors.delete(id);renderReplacements(true);
  try {
    const result=await api('POST',`projects/${pid}/cast/${story?'story-check':'identify'}`,story?{entry}:{kind:key==='product'?'product':'character',source_id:key==='product'?undefined:key,entry});
    if(P?.id!==pid)return;
    const latestDraft=castingDraft(),current=castEntry(latestDraft,key);
    P={...P,...result.project};
    if(JSON.stringify(current)!==binding) {
      castErrors.set(id,'这张卡片刚刚改过，请重试');return;
    }
    if(story) {latestDraft.product={...latestDraft.product,...result.project.replacements.product};P.story_checks_stale=result.project.story_checks_stale ?? false;}
    else {current.look=result.look;if(entry.description?.trim())castResults.set(castKey(key),result.description);else current.description=result.description;}
    castDrafts.set(pid,latestDraft);
  } catch(error) {castErrors.set(id,publicText(error.message));}
  finally {castBusy.delete(id);if(P?.id===pid)renderReplacements(true);}
}
async function saveReplacements() {
  const pid=P.id,body=readReplacements(),bad=visibleCastingCharacters(body).find(c=>['replace','add'].includes(c.action) && !c.asset_id);
  if(bad)return toast('换人或加人需要选一张照片',true);
  const next=await guard(()=>api('PUT',`projects/${pid}/replacements`,body),'设定已保存');
  if(P?.id!==pid)return;
  const latest=readReplacements(),changedDuringSave=JSON.stringify(latest)!==JSON.stringify(body);
  const productChanged=productBinding(body.product || {})!==productBinding(P.replacements?.product || {});
  P={...P,...next};if(productChanged && body.product?.story_checks_sig && next.story_checks_stale===undefined)P.story_checks_stale=true;
  dirty.rep=changedDuringSave;if(changedDuringSave)castDrafts.set(pid,latest);else castDrafts.delete(pid);
  for(const segment of P.segments_plan?.segments || []){
    const key=editSheetKey(segment.id),state=v5Actions.get(key);
    if(state?.status==='confirming' && state.binding!==v5Binding(state.kind,segment.id,state.cell)){
      v5Actions.delete(key);newStoryboardPreviews.delete(key);newStoryboardInvalidPreviews.add(key);
    }
  }
  seen.rep='';renderReplacements(true);renderSteps();scheduleReplacementCheck(true);
}

function imageRecordCard(r) {
  const asset=P.assets?.find(a=>a.id===r.asset_id),imageFile=r.result || asset?.file;
  const removed = r.state === 'completed' && !asset;
  return `<div class="run" data-image-key="${esc(r.id)}"><b>${esc(r.for_task?.startsWith("camera:") ? "主镜头图 " + r.for_task.slice(7) : "补图")} ${esc(r.id.slice(-4))}</b> · ${esc(publicText(r.preview?.model))} · <span class="state ${r.state === "pending_confirmation" ? "" : r.state}">${removed ? "已从素材库移除" : { pending_confirmation: "待确认", running: "生成中", query_pending: "等待查询", completed: "已加入素材库", failed: "失败" }[r.state] || r.state}</span>${queryRetryHtml(r,true)}
      ${imageFile ? `<img src="${fileUrl(imageFile)}" style="width:100%;border-radius:6px;margin-top:4px">` : ""}
      ${r.state === "pending_confirmation" ? `<details data-detail-key="image-prompt"><summary>提示词</summary><pre style="white-space:pre-wrap;font-size:11px">${esc(r.preview?.prompt)}</pre></details><button class="small pay" data-imgconfirm="${r.id}">确认生图（${esc(imageFee(r))}）</button>` : ""}
      ${removed ? `<button class="small ghost" data-imgresume="${r.id}">恢复</button>${r.restore_error ? `<p class="hint">恢复未完成：${esc(publicText(r.restore_error))}</p>` : ''}` : ""}
      ${r.state === "running" ? `<span data-image-wait="${esc(r.id)}"></span>` : ""}
      ${r.state === "completed" && !removed ? `<p class="hint">已作为参考图加入素材库；主镜头图完成后会自动在用，其他版本可点“改用这张”。</p>` : ""}
      ${r.state === "failed" ? `<div class="hint">${esc(publicText(r.error || ""))}</div>${imageRecovery(r)}` : ""}${imageUsage(r)}</div>`;
}
function renderCameraImages() {
  const eligible=new Set(Object.values(P.scene_cast || {}).flatMap(row=>row.camera_candidates || []));
  const recs = Object.values(P.images || {}).filter((r) => !r.for_task || sceneEditorEnabled() && /^(?:camera:|scene-camera:)/.test(r.for_task || '') && !eligible.has(r.asset_id) && (r.for_task.startsWith('camera:') || r.state==='completed'));
  const box = $("#cameraImages"); if(!box)return; const sig = JSON.stringify([P.id, recs, P.assets]);
  if (box.dataset.sig === sig) return; box.dataset.sig = sig;
  updateHtml(box, (sceneEditorEnabled() && recs.length ? '<h4>其他图片</h4>' : '') + recs.map(imageRecordCard).join(""));
}

// ---------- script (step 4) ----------
function renderScript(force) {
  const sel = $("#scriptProvider");
  renderProviderSelect(sel,'script');
  const running = P.jobs?.script?.state === "running";
  $("#scriptBtn").disabled = !P.breakdown || running;
  $("#scriptBtn").textContent = running ? "生成中…" : P.script ? "重新生成新脚本" : "生成新脚本";
  const scriptError = operationErrors.get(operationKey('script')) || (['error','failed'].includes(P.jobs?.script?.state) ? P.jobs.script.error : '');
  const errorBox = $('#scriptFailure');
  if (errorBox) updateHtml(errorBox, scriptError ? `<p class="hint">上次失败：${errorHtml(scriptError,'脚本推理')}</p>${thirdRetry('script')}` : '');
  updateDialogueModelLabels();
  updateDialogueRequestUi();
  const key = JSON.stringify(P.script || null);
  if (!force && (key === seen.script || dirty.script)) return;
  seen.script = key;
  S = P.script ? clone(P.script) : null;
  const box = $("#scriptView");
  if (!S) { updateHtml(box, ""); renderTasks(true); return; }
  const total = S.tasks.reduce((a, t) => a + Number(t.duration || 0), 0);
  updateHtml(box, `<div class="card wide" style="margin:12px 0"><h4>改编思路 <span class="hint">${esc(publicText(S.model))} · ${esc(S.at)} · 共 ${S.tasks.length} 段 / ${fmt(total)} 秒 · 台词${S.dialogue_mode === "keep" ? "保留原片" : "按要求改写"}</span></h4>
      <p>${esc(S.summary)}</p>
      ${S.treatment_md ? `<details data-detail-key="script-treatment"><summary>创作方案</summary><pre class="md">${esc(S.treatment_md)}</pre></details>` : ""}</div>
    ${S.tasks.map((t, i) => segEditor(t, i)).join("")}
    <div class="row end"><button class="ghost" data-addseg>＋ 加一段</button><button id="saveScript">保存脚本修改</button></div>`);
  renderTasks(true);
}
function segEditor(t, i) {
  const kind = t.kind || "broll";
  const axes = RECIPE_AXES[kind === "speaker" ? "speaker" : kind === "broll" ? "broll" : null] || {};
  const est = estimate(t);
  const hasLines = dialogueLines(t).length > 0;
  const textField = kind === "speaker"
    ? `${hasLines ? '' : `<label>台词（逐字交给模型）<textarea data-k="dialogue" rows="3">${esc(t.dialogue)}</textarea></label>`}
       <label>表演方向<textarea data-k="action" rows="3">${esc(t.action)}</textarea></label>`
    : kind === "broll" ? `<label>${t.mode === "video" ? "表演方向" : "微故事（按参考图顺序写每张图的作用和动作）"}<textarea data-k="story" rows="4">${esc(t.story || t.prompt)}</textarea></label>`
    : `<label>补充方向<textarea data-k="direction" rows="3">${esc(t.direction)}</textarea></label>`;
  return `<div class="seg" data-seg="${i}" data-seg-key="${esc(t.id)}">
    <div class="head"><span class="mono">${esc(t.id)}</span><span class="tag">${esc(sceneLabel(t))}</span><input class="title" data-k="title" value="${esc(t.title)}">
      <label>类型 <select data-k="kind">${Object.entries(KIND_LABELS).map(([k, l]) => `<option value="${k}" ${k === kind ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <button class="small ghost" data-mv="-1">↑</button><button class="small ghost" data-mv="1">↓</button><button class="small ghost" data-delseg>删除</button>
    </div>
    <div class="row wrap">
      <label>原片 <input class="num" type="number" step="0.01" data-k="source_start" value="${fmt(t.source_start)}">–<input class="num" type="number" step="0.01" data-k="source_end" value="${fmt(t.source_end)}">秒</label>
      <label>请求时长 <select data-k="duration">${Array.from({ length: 12 }, (_, k) => k + 4).map((d) => `<option value="${d}" ${d === Number(t.duration) ? "selected" : ""}>${fmt(d)}</option>`).join("")}</select>秒</label>
      ${kind === "speaker" ? `<label>语速 <select data-k="pace">${["normal", "fast", "slow"].map((v) => `<option value="${v}" ${v === (t.pace || "normal") ? "selected" : ""}>${PACE_LABELS[v]}</option>`).join("")}</select></label>
      <label>留白 <input class="num" type="number" step="0.01" data-k="padding" value="${fmt(t.padding || 0)}">秒</label>
      <span class="hint" data-est>台词估算 ${est ? fmt(est) : "—"} 秒${est && Math.ceil(est) > 15 ? " · 超过 15 秒，建议拆段" : ""}</span>` : ""}
      <label>比例 <select data-k="ratio">${["9:16", "16:9", "1:1"].map((r) => `<option ${r === t.ratio ? "selected" : ""}>${r}</option>`).join("")}</select></label>
    </div>
    <div class="grid">${textField}</div>
    ${speakerEditorHtml(t)}
    <div data-dialogue-section="${esc(t.id)}">${dialogueProposalHtml(t,P.replacement_checks?.[t.id])}</div>
    ${Object.keys(axes).length ? `<div class="row wrap recipe">${Object.entries(axes).map(([ax, vals]) =>
      `<label>${RECIPE_AXIS_LABELS[ax]} <select data-recipe="${ax}"${t.mode === "video" ? " disabled" : ""}>${vals.map((v) => `<option value="${v}" ${(t.recipe || {})[ax] === v ? "selected" : ""}>${RECIPE_VALUE_LABELS[v]}</option>`).join("")}</select></label>`).join("")}</div>` : ""}
    ${t.mode === "video" ? '<p class="hint">这一段的动作和镜头都跟着原片走，这里只需要写语气、表情和重音</p>' : ""}
    ${(t.overlays || []).length ? `<p class="hint">要后期叠加的字幕/花字：${esc(joinDisplayText(t.overlays.map(o=>o.textZh && o.textZh!==o.text?`${o.textZh}（原文：${o.text}）`:o.text)))}</p>` : ""}
    ${t.footage_reason || t.split_reason ? `<details data-detail-key="segment-info"><summary>详细信息</summary>${t.footage_reason ? `<p class="hint">原片片段用途：${esc(t.footage_reason)}</p>` : ''}${t.split_reason ? `<p class="hint">分段依据：${esc(t.split_reason)}</p>` : ''}</details>` : ''}
  </div>`;
}
function readSegEditors() {
  $$("#scriptView .seg").forEach((el) => {
    const t = S.tasks[Number(el.dataset.seg)];
    $$("[data-k]", el).forEach((f) => {
      const k = f.dataset.k;
      if (["source_start", "source_end", "padding"].includes(k)) {
        // Formatting alone must not move an existing precise cut boundary.
        if (Number(f.value) !== Number(fmt(t[k] ?? 0))) t[k] = Number(Number(f.value).toFixed(2));
      } else t[k] = k === 'duration' ? Number(f.value) : f.value;
    });
    const lines = dialogueLines(t);
    $$("[data-speaker]", el).forEach((f) => {
      const line = lines[Number(f.dataset.speaker)], character = (P.breakdown?.characters || []).find(c => c.id === f.value);
      if (line.speaker !== f.value) line.onScreen = character ? !offscreenCharacter(character) : null;
      line.speaker = f.value;
    });
    let textChanged = false;
    $$("[data-dialogue-text]",el).forEach(f => {
      const line = lines[Number(f.dataset.dialogueText)];
      if (line.text !== f.value) { line.text = f.value; textChanged = true; }
    });
    if (lines.length) t.dialogue_lines = lines;
    if (textChanged) t.dialogue = lines.map(l => l.text).join(' ');
    t.recipe = t.recipe || {};
    $$("[data-recipe]", el).forEach((f) => { t.recipe[f.dataset.recipe] = f.value; });
    if (["motion", "camera"].includes(t.kind)) t.mode = "video";
  });
}
async function saveScript(quiet, referenceOrders, owner, trigger = '#saveScript') {
  if (!S) return;
  const save = async state => {
    if ($("#scriptView .seg")) readSegEditors();
    const submitted=clone(S),pid=state.pid;
    const expected=clone(submitted);
    for(const task of expected.tasks) {
      const previous=P.script?.tasks?.find(t=>t.id===task.id),old=referenceOrders?.[task.id] || previous?.image_ids || [],ids=task.image_ids || [];
      for(const field of ['action','story','direction']) {
        if(!Object.hasOwn(task,field) || !referenceOrders?.[task.id] && task[field]!==previous?.[field])continue;
        task[field]=String(task[field] || '').replace(/@image(\d+)\b/g,(token,n)=>{
          const index=ids.indexOf(old[Number(n)-1]);return index<0 ? token : '@image'+(index+1);
        });
      }
    }
    const actionMatches=state.matches;
    state.matches=latest=>(!actionMatches || actionMatches(latest)) && scriptEditsMatch(latest.script,expected,state.ignored);
    const np=await api("PUT",`projects/${pid}/script`,{script:submitted,...(referenceOrders ? {reference_orders:referenceOrders} : {})});
    if(P?.id!==pid)return;
    P={...P,script:np.script};dirty.script=false;seen.script='';renderScript();renderSteps();
    scheduleReplacementCheck(true);
    if(!quiet)toast('脚本已保存');
    return true;
  };
  if(owner && dialogueRequests.get(`${owner.pid}:script-save:`)===owner)return save(owner);
  return withScriptSave(trigger,save);
}

// Share 24.6's project-keyed request states and ticking clock with script saves.
function scriptSaveState() { return P && dialogueRequests.get(operationKey('script-save')); }
function scriptEditsMatch(latest,submitted,ignored = {}) {
  const stable=value=>JSON.stringify(value,(_,v)=>v && typeof v==='object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))) : v);
  const fields=['id','title','kind','mode','source_start','source_end','duration','ratio','pace','padding','dialogue','dialogue_lines','action','story','direction','prompt','recipe','image_ids','mute_clip','blank_captions','audio_id','voice_approved'];
  return !!latest && latest.tasks?.length===submitted.tasks.length
    && ['summary','treatment_md','dialogue_mode'].every(k=>ignored.script?.includes(k) || !Object.hasOwn(submitted,k) || stable(latest[k])===stable(submitted[k]))
    && submitted.tasks.every((task,i)=>fields.every(k=>{
      const current=latest.tasks[i];
      if(ignored[task.id]?.includes(k) || !Object.hasOwn(task,k) || stable(current[k])===stable(task[k]))return true;
      // Adoption/keep also rewrites quoted dialogue in performance directions.
      // Accept only the exact server-recorded transformation, including its inverse.
      const mapping=current.dialogue_anchor_mapping;
      const record=ignored[task.id]?.includes('dialogue') && ['action','story','direction'].includes(k) && mapping?.fields?.[k];
      return !!record && task[k]===record[mapping.active ? 'before' : 'after'] && current[k]===record[mapping.active ? 'after' : 'before'];
    }));
}
function updateScriptSaveUi() {
  if(!P)return;
  const state=scriptSaveState();
  for(const button of $$(SCRIPT_WRITE_BUTTONS)) {
    // Rendering can replace a node or change its ordinary disabled condition.
    if(button.hasAttribute('data-script-save-label')) {
      if(button.textContent.startsWith('正在保存并做安全校对'))button.textContent=button.dataset.scriptSaveLabel;
      if(button.dataset.scriptSaveProject===P.id && button.getAttribute('aria-busy')==='true') {
        button.dataset.scriptSaveDisabled==='1' ? button.setAttribute('disabled','') : button.removeAttribute('disabled');
      }
      for(const key of ['data-script-save-label','data-script-save-disabled','data-script-save-project','aria-busy'])button.removeAttribute(key);
    }
    const tid=button.dataset.dialogueAdopt || button.dataset.dialogueKeep || button.dataset.dialoguePropose
      || (button.dataset.thirdRetry==='dialogue' ? button.dataset.taskId : '');
    if(tid) {
      const task=S?.tasks?.find(t=>t.id===tid);
      const invalid=button.hasAttribute('data-dialogue-adopt') && task?.dialogue_proposal
        && (dialogueAdoptionProblem(dialogueProposalLines(task)) || dialogueProposalAdopted(task));
      dialogueRequests.has(operationKey('dialogue',tid)) || invalid ? button.setAttribute('disabled','') : button.removeAttribute('disabled');
      if(button.hasAttribute('data-dialogue-adopt'))button.textContent=dialogueProposalAdopted(task) ? '已采用' : '采用这版';
    }
    if(button.hasAttribute('data-preview')) {
      P.replacement_checks?.[button.dataset.preview]?.errors?.length ? button.setAttribute('disabled','') : button.removeAttribute('disabled');
    }
    if(button.id==='scriptBtn') {
      !P.breakdown || P.jobs?.script?.state==='running' ? button.setAttribute('disabled','') : button.removeAttribute('disabled');
    }
    if(button.id==='workflowNext') {
      workflowState().some(row=>row.state==='当前') ? button.removeAttribute('disabled') : button.setAttribute('disabled','');
    }
    if(!state)continue;
    button.setAttribute('data-script-save-label',button.textContent);
    button.setAttribute('data-script-save-disabled',button.hasAttribute('disabled') || button.disabled ? '1' : '0');
    button.setAttribute('data-script-save-project',P.id);
    button.setAttribute('aria-busy','true');button.setAttribute('disabled','');
    if(button.matches(state.trigger))button.textContent=dialogueProgressText(state);
  }
}
async function withScriptSave(trigger,action,matches,ignored = {}) {
  const pid=P.id,key=operationKey('script-save');
  if(dialogueRequests.has(key))return;
  const state={pid,trigger,matches,ignored,kind:'save',startedAt:Date.now()};
  dialogueRequests.set(key,state);updateDialogueRequestUi();scheduleDialogueWait(key,state);
  try {return await action(state);}
  catch(error) {
    if(!/校对期间脚本或替换设定已变化|刚才有另一次保存先完成了/.test(error.message))throw error;
    const latest=await api('GET',`projects/${pid}`);
    if(P?.id!==pid)return;
    const saved=!!state.matches?.(latest);
    P=latest;S=clone(P.script || {tasks:[]});dirty.script=false;dirty.rep=false;castDrafts.delete(pid);seen.script='';seen.rep='';
    operationErrors.delete(operationKey('script'));
    for(const task of S.tasks) {
      const dialogueKey=operationKey('dialogue',task.id);
      dialogueProposalEdits.delete(dialogueKey);operationErrors.delete(dialogueKey);
      dialogueFailureKinds.delete(dialogueKey);dialogueNotices.delete(dialogueKey);
    }
    // A conflict explicitly replaces stale drafts; ordinary polling preserves them.
    for(const selector of ['#scriptView','#taskCards','#replacementForm']) {
      const box=$(selector);if(box){renderedHtml.delete(box);box.innerHTML='';}
    }
    renderAll();scheduleReplacementCheck(true);
    toast(saved ? '已保存' : '刚才有另一次保存先完成了，页面已更新到最新内容，请检查后再操作');
  } finally {
    clearTimeout(state.timer);dialogueRequests.delete(key);
    if(P?.id===pid)updateDialogueRequestUi();
  }
}

// ---------- tasks (step 5) ----------
let replacementCheckTimer, replacementCheckSequence = 0;
let replacementCheckPending = "", replacementCheckCache = null;
const displayedReplacementChecks = new Map();
function replacementCheckInput() {
  if ($("#scriptView .seg")) readSegEditors();
  const body = { tasks: clone(S.tasks) };
  if (dirty.rep) body.replacements = readReplacements();
  // Checks also depend on source analysis, saved script provenance and completed
  // camera bindings. Run progress/log timestamps deliberately do not invalidate it.
  const cameras = Object.values(P.images || {}).filter(r => r.for_task?.startsWith("camera:") && r.state === "completed")
    .map(r => [r.for_task, r.asset_id]);
  const key = JSON.stringify([P.id, body.tasks, body.replacements || P.replacements, P.assets,
    P.analysis, P.breakdown, P.source, P.script?.replacements_fingerprint, cameras,
    P.camera_current, P.camera_stale_assets, P.camera_stale_images, P.missing_asset_files, P.action_comparisons, P.motion_warning_dismissals, P.motion_warning_epoch,
    P.jobs?.analyze?.state, P.jobs?.script?.state]);
  return { key, body };
}
function applyReplacementChecks(checks, announce = false) {
  const previous = displayedReplacementChecks.get(P.id), completed = [];
  if (announce && previous) for (const task of S.tasks) {
    const before = fixGroups(previous[task.id],task), after = new Set(fixGroups(checks[task.id],task).map(t=>t.key));
    if (checks[task.id]) completed.push(...before.filter(t=>!after.has(t.key)).map(t=>t.action));
  }
  displayedReplacementChecks.set(P.id,clone(checks));
  preserveView(() => {
    P.replacement_checks = checks;
    renderReplacements();
    renderSteps();
    S.tasks.forEach((t) => {
      const check = checks[t.id];
      const dialogue = $(`[data-dialogue-section="${t.id}"]`);
      if (dialogue) updateHtml(dialogue, dialogueProposalHtml(t,check));
      const box = $(`[data-replacement-check="${t.id}"]`);
      if (!box || !check) return;
      $('[data-check-progress]',box)?.remove(); $('[data-check-error]',box)?.remove();
      updateHtml(box, replacementCheckHtml(check, t));
      box.setAttribute("aria-busy","false"); box.setAttribute("class", "");
      const card = box.closest("[data-task]");
      const mute = $('[data-m="mute_clip"]', card);
      if (mute) { mute.disabled = !!check.force_mute; if (check.force_mute) { mute.checked = true; t.mute_clip = true; } }
      const reason = $('[data-mute-reason]', card);
      if (reason) reason.textContent = check.mute_reason || "";
      const timeline = $('[data-motion-timeline]', card);
      if (timeline) updateHtml(timeline, motionTimelineHtml(check.source_motion_timeline));
      const caption = $('[data-caption-warning]', card);
      if (caption) updateHtml(caption, t.blank_captions === true ? captionWarningsHtml(check.masks) : '');
      const button = $('[data-preview]', card);
      if (button) button.disabled = check.errors.length > 0;
      const previewReason = $('[data-preview-reason]', card);
      if (previewReason) updateHtml(previewReason, previewReasonHtml(check, t.id));
    });
  });
  if (completed.length) toast(`已完成：${[...new Set(completed)].join('；')}`);
  updateScriptSaveUi();
}
function markReplacementChecking() {
  for (const box of $$('[data-replacement-check]')) {
    box.setAttribute('aria-busy','true'); box.setAttribute('class','check-rechecking');
    if (!$('[data-check-progress]',box)) {
      const progress = document.createElement('p'); progress.setAttribute('data-check-progress','');
      progress.setAttribute('class','hint'); progress.textContent = '正在检查…'; box.appendChild(progress);
    }
  }
}
function scheduleReplacementCheck(force = false) {
  if (!P || openingProjectId || projectUnavailable(P.id) || !S?.tasks?.length) {
    clearTimeout(replacementCheckTimer); replacementCheckPending = ""; replacementCheckCache = null; ++replacementCheckSequence;
    return;
  }
  const { key, body } = replacementCheckInput();
  if (!force && key === replacementCheckPending) { markReplacementChecking(); return; }
  clearTimeout(replacementCheckTimer);
  const sequence = ++replacementCheckSequence, pid = P.id, generation = projectGeneration;
  replacementCheckPending = "";
  if (!force && replacementCheckCache?.key === key) { applyReplacementChecks(replacementCheckCache.checks); return; }
  if (force) replacementCheckCache = null;
  replacementCheckPending = key;
  markReplacementChecking();
  replacementCheckTimer = setTimeout(async () => {
    if (sequence !== replacementCheckSequence || !projectViewCurrent(pid, generation)) return;
    try {
      const checks = await api("POST", `projects/${pid}/replacement-check`, body);
      if (sequence !== replacementCheckSequence || !projectViewCurrent(pid, generation) || !S?.tasks?.length) return;
      // Never label an old response with newly edited inputs, even if an input
      // event has not yet reached the debounce handler.
      if (key !== replacementCheckInput().key) { scheduleReplacementCheck(); return; }
      applyReplacementChecks(checks,true);
      replacementCheckCache = { pid, key: replacementCheckInput().key, checks };
    } catch (error) {
      if (sequence !== replacementCheckSequence || !projectViewCurrent(pid, generation)) return;
      S.tasks.forEach((t) => {
        const box = $(`[data-replacement-check="${t.id}"]`);
        if (!box) return;
        box.setAttribute('aria-busy','false'); box.setAttribute('class','');
        $('[data-check-progress]',box)?.remove();
        const message = document.createElement('p'); message.setAttribute('class','hint'); message.setAttribute('data-check-error','');
        $('[data-check-error]',box)?.remove();
        message.textContent = `检查暂时不可用：${publicText(error.message)}；生成预览时仍会校验。`;
        box.appendChild(message);
      });
    } finally {
      if (sequence === replacementCheckSequence) replacementCheckPending = "";
    }
  }, force ? 0 : 250);
}
function renderTasks(force) {
  renderLegacyRoutes();
  if (!P.replacement_checks && replacementCheckCache?.pid === P.id) P.replacement_checks = replacementCheckCache.checks;
  const box = $("#taskCards");
  if (!S || !S.tasks?.length) { updateHtml(box, `<p class="hint">生成新脚本后，每一段会出现在这里。</p>`); scheduleReplacementCheck(); return; }
  if (force || !box.querySelector(".seg")) {
    updateHtml(box, S.tasks.map((t, i) => taskCard(t, i)).join(""), true);
  }
  S.tasks.forEach((t) => renderRuns(t));
  updateDialogueRequestUi();
  scheduleReplacementCheck();
}
function taskCard(t, i) {
  const order = t.image_ids || [];
  const check = P.replacement_checks?.[t.id];
  const {useful, other} = taskReferenceImages(t);
  const chip = (a) => {
    const n = order.indexOf(a.id), cameraScene = n === 0 ? assetCameraScene(a) : "";
    return `<span class="chip ${n >= 0 ? "on" : ""}" data-chip="${esc(a.id)}"><img src="${fileUrl(a.file)}">${n >= 0 ? `<span class="n">图${n + 1}</span>` : ""}${materialNameHtml(a.label)}${cameraScene ? `<span class="camera-scene-badge">场景 ${esc(cameraScene)}</span>` : ""}</span>`;
  };
  const chips = useful.map(chip).join("") || `<span class="hint">${(P.assets || []).some(a=>a.type === 'image') ? '暂无本段可用的参考图' : '素材库里还没有图片'}</span>`;
  return `<div class="seg" data-task="${t.id}" data-i="${i}">
    <div class="head"><b>${esc(t.id)} · ${esc(t.title)}</b><span class="tag">${esc(sceneLabel(t))}</span><span class="hint">${esc(KIND_LABELS[t.kind] || "")} · 原片 ${fmt(t.source_start)}–${fmt(t.source_end)} 秒 · 生成 ${fmt(t.duration)} 秒${targetSeconds(t)<Number(t.duration)?` · 成片取 ${fmt(targetSeconds(t))} 秒`:''} · ${t.ratio}</span></div>
    <div class="row wrap">
      <label>参考方式 <select data-m="mode"><option value="video" ${t.mode === "video" ? "selected" : ""}>原片片段 + 图（Mini-video）</option><option value="image" ${t.mode === "image" ? "selected" : ""}>只用图（Mini）</option></select></label>
      ${t.mode === "video" ? `<label class="check"><input type="checkbox" data-m="mute_clip" ${check?.force_mute || t.mute_clip !== false ? "checked" : ""} ${check?.force_mute ? "disabled" : ""}> 参考片段去掉原声</label>
      <span class="hint" data-mute-reason>${esc(check?.mute_reason || "")}</span>
      <label class="check"><input type="checkbox" data-m="blank_captions" ${t.blank_captions === true ? "checked" : ""}> 去掉参考片段里的原片字幕</label>
      <span class="hint">提示词已要求新视频不出现字幕。只有生成的视频里出现了原片字幕时，再勾选这里重新生成</span>
      <div data-caption-warning>${t.blank_captions === true ? captionWarningsHtml(check?.masks) : ''}</div>` : ""}
      <label>声线 <select data-m="audio_id">${assetOptions("audio", t.audio_id)}</select></label>
      <label class="check"><input type="checkbox" data-m="voice_approved" ${t.voice_approved ? "checked" : ""}> 我已试听并认可这个声线</label>
    </div>
    <div class="hint">参考图（按点击顺序编号为图1、图2…，最多 4 张；${t.kind === "speaker" || ["motion", "camera"].includes(t.kind) ? "图1 必须是出镜人物 / 主镜头图" : "按微故事里说明的顺序选"}）。只列出本段可能用到的图；内部截帧不会出现在这里</div>
    <div class="chips">${chips}</div>
    ${other.length ? `<details data-detail-key="other-camera-images"><summary>显示其他版本的主镜头图（${other.length}）</summary><div class="chips">${other.map(chip).join('')}</div></details>` : ''}
    <div class="reference-actions">${order.filter(id=>!(P.assets || []).some(a=>a.id === id)).map(id=>`<button class="small ghost" data-chip="${esc(id)}">去掉失效参考图“${esc(id)}”</button>`).join('')}
    ${check?.issues?.some(i=>i.code === 'image1_not_person') && firstPersonIndex(t) > 0 ? `<button class="small ghost" data-fix-image1="${esc(t.id)}">把人物/主镜头图放到图1</button>` : ''}</div>
    ${cameraFollowHtml(t)}
    <p class="hint image-order-warning" data-image-order-warning>${order.length && !isPersonImage(order[0]) ? "图1 应该是出镜人物或主镜头图" : ""}</p>
    ${t.mode === "video" ? `<div data-motion-timeline>${motionTimelineHtml(check?.source_motion_timeline || t.source_motion_timeline)}</div>` : ""}
    ${t.need_image ? `<p class="hint">脚本建议补图：${esc(t.need_image)}</p>` : ""}
    ${(t.replacement_safety || []).length ? `<details data-detail-key="replacement-safety"><summary>映射安全改写：${t.replacement_safety.length} 处</summary>${t.replacement_safety.map((change) => `<p><del>${esc(change.before)}</del> → <ins>${esc(change.after)}</ins></p>`).join("")}</details>` : ""}
    ${(t.performance_cleanup || []).length ? `<details data-detail-key="performance-cleanup"><summary>表演描述清理：${t.performance_cleanup.length} 处</summary>${t.performance_cleanup.map((change) => `<p><b>${esc({action: "表演", story: "故事", direction: "方向"}[change.field] || change.field)}</b>：<del>${esc(change.before)}</del> → <ins>${esc(change.after || "（已删除）")}</ins></p>`).join("")}</details>` : ""}
    <div data-replacement-check="${t.id}" role="status">${replacementCheckHtml(check, t)}</div>
    <div class="row end">
      <button class="ghost small" data-imgform="${t.id}">另外生成一张参考图</button><span class="hint">需要额外的人物 / 商品状态图时用，通常不需要</span>
      <span class="hint" data-preview-reason="${t.id}">${previewReasonHtml(check, t.id)}</span>
      <button data-preview="${t.id}" ${check?.errors?.length ? "disabled" : ""}>生成预览${t.mode === "video" ? "（含模型动作检查）" : "（免费）"}</button>
    </div>
    <div class="imgform" data-imgbox="${t.id}" hidden></div>
    <div data-live="${t.id}"></div>
  </div>`;
}
function taskReferenceImages(task) {
  const assets = P.assets || [], order = task.image_ids || [];
  const candidates = assets.filter(a=>a.type === 'image' && !internalAsset(a) && !a.derived_from);
  const available = new Map(candidates.map(a=>[a.id,a]));
  const cameras = new Set(candidates.filter(a=>a.camera_character || assetCameraScene(a)).map(a=>a.id));
  // Selected references stay visible even when they are internal or from another scene.
  const useful = new Map(order.map(id=>assets.find(a=>a.id === id)).filter(Boolean).map(a=>[a.id,a]));
  const characters = P.replacements?.characters || [], sid = taskScene(task);
  const preferred = sceneEditorEnabled() ? [sceneCurrentCamera(sid),...(sceneEditorRows().find(r=>r.scene_id===sid)?.subjects || []).filter(s=>s.on).map(s=>s.asset_id)] : [...characters.filter(c=>c.action === 'replace').map(c=>cameraImageFor(c.source_id,sid)),
    ...characters.map(c=>c.asset_id), P.replacements?.product?.asset_id];
  preferred.forEach(id=>{ if (available.has(id)) useful.set(id,available.get(id)); });
  candidates.filter(a=>!cameras.has(a.id)).forEach(a=>useful.set(a.id,a));
  return {useful:[...useful.values()], other:candidates.filter(a=>cameras.has(a.id) && !useful.has(a.id))};
}
function cameraScenes() {
  const includeDropped=!!arguments[0];
  const canonical=includeDropped ? P.scene_editor_scenes : P.effective_scenes;
  if(Array.isArray(canonical))return canonical;
  const scenes = (P.breakdown?.scene || []).filter(s => s.id);
  return scenes.some(s => s.timeRanges?.length) ? scenes : [{id:'S1',name:scenes[0]?.name || '原片场景',timeRanges:scenes[0]?.timeRanges || []}];
}
function sceneForRange(start, end) {
  const scenes = cameraScenes();
  const overlap = s => (s.timeRanges || []).reduce((sum,[a,b]) => sum + Math.max(0,Math.min(Number(end),b)-Math.max(Number(start),a)),0);
  return [...scenes].sort((a,b) => overlap(b)-overlap(a))[0].id;
}
function taskScene(task) { return sceneForRange(task.source_start || 0, task.source_end ?? Infinity); }
function firstCharacterScene(cid) {
  const shot = [...(P.breakdown?.shots || [])].filter(s => s.characters?.includes(cid)).sort((a,b)=>a.start-b.start)[0];
  return shot ? sceneForRange(shot.start,Math.min(Number(shot.end),Number(shot.start)+0.001)) : cameraScenes()[0].id;
}
function validCameraAsset(aid) {
  return !!aid && P.assets?.some(a => a.id === aid) && !P.camera_stale_assets?.includes(aid) && !P.missing_asset_files?.includes(aid);
}
function cameraImageFor(cid, sid) {
  if(sceneEditorEnabled()){const current=sceneCurrentCamera(sid);return validCameraAsset(current) && (cid==='scene' || sceneEditorRows().find(r=>r.scene_id===sid)?.subjects.some(s=>s.key===cid && s.on))?current:null;}
  if (P.camera_current?.[cid] && Object.hasOwn(P.camera_current[cid],sid)) return validCameraAsset(P.camera_current[cid][sid]) ? P.camera_current[cid][sid] : null;
  const c = (P.replacements?.characters || []).find(c => c.source_id === cid) || {};
  const recs = Object.values(P.images || {}).filter(r => r.state === 'completed').reverse();
  const candidates = [c.camera_assets?.[sid], ...recs.filter(r => r.for_task === `camera:${cid}:${sid}`).map(r => r.asset_id)];
  if (sid === firstCharacterScene(cid)) candidates.push(c.camera_asset_id,...recs.filter(r => r.for_task === `camera:${cid}`).map(r => r.asset_id));
  return candidates.find(validCameraAsset) || null;
}
function taskCameraReference(task) {
  if(sceneEditorEnabled()){const aid=task.image_ids?.[0],sid=taskScene(task),asset=P.assets?.find(a=>a.id===aid),record=Object.values(P.images || {}).find(r=>r.asset_id===aid && r.for_task?.startsWith('scene-camera:'));return aid && (asset?.camera_scene===sid || record?.for_task===`scene-camera:${sid}` || sceneCurrentCamera(sid)===aid)?{cid:'scene',sid,aid}:null;}
  const aid = task.image_ids?.[0], sid = taskScene(task);
  if (!aid) return null;
  const asset = P.assets?.find(a => a.id === aid);
  const record = Object.values(P.images || {}).find(r => (r.asset_id === aid || r.id === aid) && r.for_task?.startsWith('camera:'));
  const cid = asset?.camera_character || record?.for_task.split(':')[1];
  const cameraSid = asset?.camera_scene || (record && (record.for_task.split(':')[2] || firstCharacterScene(cid)));
  return cid && cameraSid === sid ? {cid,sid,aid} : null;
}
function cameraFollowHtml(task) {
  const issue = P.replacement_checks?.[task.id]?.issues?.find(i=>i.expected_asset_id && (['invalid_camera_image','camera_image_stale','missing_camera_image'].includes(i.code) && (!i.reference_index || i.reference_index === 1) || i.code === 'camera_scene_mismatch' && !i.reference_index));
  const reference = taskCameraReference(task) || (issue && {cid:issue.character_id,sid:issue.scene_id,aid:task.image_ids?.[0]});
  if (!reference) return '';
  const {cid,sid,aid} = reference;
  if (cameraImageFor(cid,sid) === aid) return `<p class="hint" data-camera-follow>图1：${esc(sid)} 在用的主镜头图</p>`;
  return `<p class="caption-warning" data-camera-follow-warning>图1 不是 ${esc(sid)} 在用的主镜头图${cameraImageFor(cid,sid) ? ` <button class="small ghost" data-fix-camera="${esc(task.id)}" data-character="${esc(cid)}" data-scene="${esc(sid)}">换成 ${esc(sid)} 当前的主镜头图</button>` : ''}</p>`;
}
function sceneLabel(task) {
  const sid = taskScene(task), scene = cameraScenes().find(s=>s.id === sid);
  return `场景 ${sid} · ${scene?.name || '原片场景'}`;
}
function assetCameraScene(asset) {
  const sceneRecord=Object.values(P.images || {}).find(r=>r.asset_id===asset.id && r.for_task?.startsWith('scene-camera:'));if(sceneRecord)return sceneRecord.for_task.slice('scene-camera:'.length);
  if (asset.camera_scene) return asset.camera_scene;
  const record = Object.values(P.images || {}).find(r=>r.asset_id === asset.id && r.for_task?.startsWith('camera:'));
  if (record) { const [,cid,sid] = record.for_task.split(':'); return sid || firstCharacterScene(cid); }
  const c = (P.replacements?.characters || []).find(c=>c.camera_asset_id === asset.id || Object.values(c.camera_assets || {}).includes(asset.id));
  return c ? Object.keys(c.camera_assets || {}).find(s=>c.camera_assets[s] === asset.id) || firstCharacterScene(c.source_id) : '';
}

function dialogueLines(task) {
  if (Array.isArray(task.dialogue_lines)) return clone(task.dialogue_lines);
  return (P.breakdown?.dialogue || []).map(line => {
    const range = Array.isArray(line.time) ? line.time : String(line.time || '').match(/\d+(?:\.\d+)?/g) || [];
    const start = Number(line.start ?? range[0]), end = Number(line.end ?? range[1] ?? range[0]);
    return {...line, start, end, speaker: line.speaker || 'unknown', onScreen: line.onScreen ?? null, evidence: line.evidence || ''};
  }).filter(line => Number.isFinite(line.start) && Number.isFinite(line.end) && (line.end > Number(task.source_start || 0) || line.start === line.end && line.start >= Number(task.source_start || 0)) && line.start < Number(task.source_end ?? Infinity));
}
function speakerLabel(id) {
  const c = (P.breakdown?.characters || []).find(c => c.id === id);
  return c ? `${c.name || c.id}${offscreenCharacter(c) ? '（画外角色）' : ''}` : id && id !== 'unknown' ? id : '未确定';
}
function speakerEditorHtml(task) {
  const lines = dialogueLines(task);
  if (!lines.length) return '';
  const unknown = lines.filter(l => !l.speaker || l.speaker === 'unknown').length;
  const choices = [...(P.breakdown?.characters || []).map(c => c.id), 'unknown'];
  return `<div class="speaker-editor"><h4>逐句说话人</h4>${unknown ? `<p class="caption-warning" data-speaker-warning role="status">有 ${unknown} 句台词还没确定是谁说的，请在第 04 步确认</p>` : ''}<div class="speaker-lines">${lines.map((line,i) => `<div class="speaker-line" data-speaker-line="${i}"><span class="mono">${fmt(line.start,2)}–${fmt(line.end,2)} 秒</span><label>台词<textarea data-dialogue-text="${i}" rows="2">${esc(line.text)}</textarea></label><label>说话人 <select data-speaker="${i}">${[...new Set([...choices,line.speaker || 'unknown'])].map(id=>`<option value="${esc(id)}" ${id === (line.speaker || 'unknown') ? 'selected' : ''}>${esc(speakerLabel(id))}</option>`).join('')}</select></label><small class="hint">${esc(typeof line.evidence === 'string' ? line.evidence : JSON.stringify(line.evidence || ''))}</small></div>`).join('')}</div></div>`;
}
function dialogueLineMetrics(task, line) {
  return {changed:line.proposed!==line.original,
    length_warning:speechUnits(line.proposed)>speechUnits(line.original)*1.3};
}
function dialogueProposalLines(task) {
  const key=operationKey('dialogue',task.id),signature=JSON.stringify(task.dialogue_proposal);
  const sourceSignature=JSON.stringify((task.dialogue_proposal?.lines || []).map(l=>[l.start,l.end,l.original]));
  let draft=dialogueProposalEdits.get(key);
  if(draft?.signature!==signature){
    // Adoption replaces proposal metadata while the user may still be typing.
    // Keep those edits when source text/timing match, even if speakers are updated.
    const values=task.dialogue_choice==='adopt' && draft?.sourceSignature===sourceSignature ? draft.values : {};
    draft={signature,sourceSignature,values};dialogueProposalEdits.set(key,draft);
  }
  return (task.dialogue_proposal?.lines || []).map((line,i)=>{
    const row={...line,proposed:draft.values[i] ?? line.proposed};
    return {...row,...dialogueLineMetrics(task,row)};
  });
}
const DIALOGUE_LENGTH_WARNING='这句比原句长不少，生成后注意听是否说得太快';
function dialogueReason(line) {
  if(!line.changed)return '不涉及新人物或新商品，保留原句';
  // Old model replies sometimes include implementation notes alongside the explanation.
  const reason=String(line.reason || ''),internal=/(?:mustKeep|maxUnits|\bmax\s*\d+|≤)/i;
  if(!internal.test(reason))return reason;
  return reason
    .replace(/[（(][^（）()]*?(?:mustKeep|maxUnits|\bmax\s*\d+|≤)[^（）()]*[）)]/gi,'')
    .split(/[，,；;。\r\n]/).map(part=>part.trim())
    .filter(part=>part && !internal.test(part)).join('，');
}
function dialogueAdoptionProblem(lines) {
  return lines.map((line,i)=>!line.proposed.trim() ? `第 ${i+1} 句，原句「${line.original}」：改写台词不能为空` : '').filter(Boolean).join('；');
}
function dialogueProposalAdopted(task,lines=task?.dialogue_proposal ? dialogueProposalLines(task) : []) {
  return task?.dialogue_choice==='adopt' && lines.length>0 && Array.isArray(task.dialogue_lines)
    && lines.length===task.dialogue_lines.length && lines.every((line,i)=>line.proposed===task.dialogue_lines[i].text);
}
function dialogueChannelFailure(task) {
  const key=operationKey('dialogue',task.id);
  return operationErrors.has(key) && ['auth','quota','transient'].includes(dialogueFailureKinds.get(key));
}
function dialogueModelName() {
  const selection=requestRoute('dialogue_proposal',$('#scriptProvider'));
  const resolved=selection.route || STATUS?.dialogue_provider_routes?.[selection.provider];
  const channel=resolved?.channel || selection.provider;
  const model=resolved?.model || STATUS?.providers?.[channel]?.model || routeFor('dialogue_proposal').model;
  return routeName({channel,model});
}
function dialogueModelLabel() {
  const temporary=$('#scriptProvider')?.dataset.userSelected==='1' && !!$('#scriptProvider').value;
  return `改写模型：${dialogueModelName()}（${temporary ? '本次临时选择' : legacyRouteNote('dialogue_proposal') || '跟随设置里的推理模型'}）`;
}
function dialogueCallHint() {
  return `会调用一次 ${dialogueModelName()}，通常需要 20–60 秒`;
}
function updateDialogueModelLabels() {
  renderLegacyRoutes();
  const labels=$$('[data-dialogue-model]');
  if(!labels.length)return;
  const text=dialogueModelLabel();
  labels.forEach(el=>{if(el.textContent!==text)el.textContent=text;});
  $$('[data-dialogue-call-hint]').forEach(el=>{el.textContent=dialogueCallHint();});
}
function dialogueProgressText(state) {
  const seconds=Math.max(0,Math.floor((Date.now()-state.startedAt)/1000));
  return `${state.kind==='save' ? '正在保存并做安全校对' : '正在改写'}…（已等待 ${seconds} 秒）`;
}
function updateDialogueRequestUi() {
  if(!P)return;
  for(const box of $$('[data-dialogue-box]')) {
    const tid=box.dataset.dialogueBox,key=operationKey('dialogue',tid),state=dialogueRequests.get(key);
    const task=S?.tasks.find(t=>t.id===tid);
    const problem=task?.dialogue_proposal && dialogueAdoptionProblem(dialogueProposalLines(task));
    const adopted=dialogueProposalAdopted(task);
    const status=$('[data-dialogue-adopted]',box);if(status)status.hidden=!adopted;
    const unsaved=$('[data-dialogue-unsaved]',box);if(unsaved)unsaved.hidden=task?.dialogue_choice!=='adopt' || adopted;
    box.setAttribute('aria-busy',String(!!state || !!scriptSaveState()));
    for(const button of $$('[data-dialogue-propose], [data-dialogue-keep], [data-dialogue-adopt], [data-third-retry="dialogue"]',box)) {
      const disabled=!!state || button.hasAttribute('data-dialogue-adopt') && (!!problem || adopted);
      disabled ? button.setAttribute('disabled','') : button.removeAttribute('disabled');
      if(button.hasAttribute('data-dialogue-propose'))button.textContent=state ? dialogueProgressText(state) : task?.dialogue_proposal ? '按我的要求重新改写' : '改写冲突台词';
    }
  }
  updateScriptSaveUi();
}
function scheduleDialogueWait(key,state) {
  state.timer=setTimeout(()=>{
    if(dialogueRequests.get(key)!==state)return;
    updateDialogueRequestUi();
    scheduleDialogueWait(key,state);
  },1000);
}
function updateDialogueProposalInput(el) {
  const box=el.closest('[data-dialogue-box]');
  if(!box)return false;
  const tid=box.dataset.dialogueBox,key=operationKey('dialogue',tid);
  if(el.hasAttribute('data-dialogue-instruction')){dialogueInstructions.set(key,el.value);return true;}
  if(!el.hasAttribute('data-dialogue-line'))return false;
  const task=S?.tasks.find(t=>t.id===tid);
  if(!task?.dialogue_proposal)return true;
  dialogueProposalLines(task);
  dialogueProposalEdits.get(key).values[Number(el.dataset.dialogueLine)]=el.value;
  const lines=dialogueProposalLines(task),problem=dialogueAdoptionProblem(lines);
  const line=lines[Number(el.dataset.dialogueLine)],row=el.closest('tr');
  if(row){
    row.setAttribute('class',line.changed?'dialogue-changed':'');
    el.setAttribute('aria-invalid',String(!line.proposed.trim()));
    let hint=$('[data-dialogue-length-warning]',row);
    if(line.length_warning && !hint){
      hint=document.createElement('small');hint.setAttribute('class','hint');hint.setAttribute('data-dialogue-length-warning','');
      hint.textContent=DIALOGUE_LENGTH_WARNING;el.parentElement.appendChild(hint);
    } else if(!line.length_warning)hint?.remove();
    const reason=$('[data-dialogue-reason]',row);if(reason)reason.textContent=dialogueReason(line);
  }
  const adopt=$('[data-dialogue-adopt]',box);
  if(adopt){problem || dialogueRequests.has(key) || scriptSaveState() ? adopt.setAttribute('disabled','') : adopt.removeAttribute('disabled');}
  const reason=$('[data-dialogue-adopt-reason]',box);if(reason){reason.textContent=problem ? '暂不能采用：'+problem : '';reason.hidden=!problem;}
  updateDialogueRequestUi();
  return true;
}
function dialogueProposalHtml(task, check) {
  if (!task?.id) return '';
  const key=operationKey('dialogue',task.id),state=dialogueRequests.get(key),error=operationErrors.get(key),notice=dialogueNotices.get(key);
  if (!state && (task.dialogue_keep_original || task.dialogue_choice==='keep')) return '';
  const proposal=task.dialogue_proposal;
  const wanted=proposal || state || error || notice || task.dialogue_proposal_requested || check?.dialogue_conflicts?.length || (check?.warnings || []).some(w=>/台词提到原片外观|台词里有[\s\S]*和新商品可能不符/.test(w));
  if (!wanted) return '';
  const lines=dialogueProposalLines(task),problem=dialogueAdoptionProblem(lines),channelFailure=dialogueChannelFailure(task),adopted=dialogueProposalAdopted(task,lines);
  return `<div class="dialogue-proposal" data-dialogue-box="${esc(task.id)}" aria-busy="${!!state}"><h4>台词改写方案</h4>
    ${proposal ? `<p class="hint" role="status" data-dialogue-adopted ${adopted?'':'hidden'}>✓ 已采用改写后的台词</p>` : ''}
    <p class="hint" data-dialogue-model>${esc(dialogueModelLabel())}</p>
    ${error ? `<div class="dialogue-error" data-dialogue-error role="alert"><p>改写失败：${esc(error)}</p><p>可以再点一次改写，或点保持原台词。</p></div>${channelFailure ? thirdRetry("dialogue",`data-task-id="${esc(task.id)}"`) : ''}` : ''}
    ${notice ? `<p class="hint" role="status" data-dialogue-notice>${esc(notice.message)}</p>` : ''}
    ${proposal ? `<table><thead><tr><th>时间</th><th>说话人</th><th>原句</th><th>改写后（可编辑）</th><th>原因</th></tr></thead><tbody>${lines.map((l,i)=>`<tr class="${l.changed?'dialogue-changed':''}"><td>${fmt(l.start,2)}–${fmt(l.end,2)} 秒</td><td>${esc(speakerLabel(l.speaker))}</td><td>${esc(l.original)}</td><td><textarea data-dialogue-line="${i}" aria-invalid="${!l.proposed.trim()}" rows="2">${esc(l.proposed)}</textarea>${l.length_warning ? `<small class="hint" data-dialogue-length-warning>${DIALOGUE_LENGTH_WARNING}</small>` : ''}</td><td data-dialogue-reason>${esc(dialogueReason(l))}</td></tr>`).join('')}</tbody></table><button data-dialogue-adopt="${esc(task.id)}" ${problem || state || adopted?'disabled':''}>${adopted?'已采用':'采用这版'}</button><span class="hint" role="status" data-dialogue-unsaved ${task.dialogue_choice==='adopt' && !adopted?'':'hidden'}>改动还没保存，点“采用这版”生效</span><p class="hint" role="status" data-dialogue-adopt-reason ${problem?'':'hidden'}>${problem?'暂不能采用：'+esc(problem):''}</p>` : ''}
    <label>改写要求（可不填）<input data-dialogue-instruction value="${esc(dialogueInstructions.get(operationKey('dialogue',task.id)) || '')}" placeholder="例如：尽量沿用原句语气"></label>
    <button class="ghost" data-dialogue-propose="${esc(task.id)}" ${state?'disabled':''}>${state ? dialogueProgressText(state) : proposal?'按我的要求重新改写':'改写冲突台词'}</button>
    <button class="ghost" data-dialogue-keep="${esc(task.id)}" ${state?'disabled':''}>保持原台词</button>
    <p class="hint" data-dialogue-call-hint>${esc(dialogueCallHint())}</p></div>`;
}

function dialogueStatusHtml(task, check) {
  if (!task?.id) return '';
  const kept = task.dialogue_keep_original || task.dialogue_choice === 'keep';
  // This line describes the saved choice; local proposal edits remain in step 04.
  const adopted = task.dialogue_choice === 'adopt';
  const conflicts = check?.dialogue_conflicts || [];
  const count = conflicts.reduce((n,c)=>n + (c.terms?.length || Number(c.title?.match(/有\s*(\d+)\s*处/)?.[1]) || 1),0);
  const resolved = kept || adopted || !count;
  const text = kept ? '保持原台词' : adopted ? '已采用改写版' : count ? `有 ${count} 处提到旧外观，还没处理` : '保持原台词';
  return `<p class="hint" data-dialogue-status="${esc(task.id)}">台词：${text} <button class="small ghost" data-dialogue-jump="${esc(task.id)}">去第 04 步${resolved ? '查看' : '处理'}</button></p>`;
}

function motionTimelineHtml(rows) {
  return `<details data-detail-key="motion-timeline"><summary>原片动作时间轴（只读）</summary><pre>${(rows || []).map((r) => `[${fmt(r.start)}–${fmt(r.end)} s] ${esc(r.action)}`).join('\n') || '暂无可用条目，请补全原片拆解。'}</pre></details>`;
}
function motionCheckHtml(check, tid, rev) {
  if (!check) return '';
  return `<button class="small ghost" data-motion-check="${esc(tid)}" data-rev="${esc(rev)}">${check.status === 'running' ? '自动检查中…' : '查看自动检查（仅供参考）'}</button>`;
}
function speechDelayHtml(run) {
  const timing=run.speech_timing;
  if (!timing || !(timing.lag_seconds > .5) || !(P.scene_plan_enabled ?? sceneEditorEnabled())) return '';
  return `<p class="caption-warning" data-speech-delay role="status">台词比原片晚 ${Number(timing.lag_seconds).toFixed(1)} 秒开口，成片会多保留 ${Number(timing.extra_seconds || 0).toFixed(1)} 秒以说完这句</p>`;
}
function motionCheckDetails(check, task, record) {
  const report = clone(check || {}), threshold = report.thresholds?.max_lag_seconds ?? .5;
  const boundary = Number.isFinite(report.motion_lag_seconds) && Math.abs(report.motion_lag_seconds) >= (report.motion_search_seconds ?? 2) - 1 / (report.sample_fps || 10);
  let incomplete = !Number.isFinite(report.motion_lag_seconds) || boundary || report.motion_status === 'inconclusive';
  let deviation = !incomplete && (Math.abs(report.motion_lag_seconds) > threshold || report.motion_correlation < (report.thresholds?.min_correlation ?? .45));
  const warnings = [], lines = record?.motion_source?.dialogue_lines || task?.dialogue_lines || [];
  const oldLines = record?.motion_source?.transcript?.segments || report.speech_lags || [];
  const currentLine = row => lines.find(l => Math.abs(Number(l.start) - Number(row.source_start ?? row.start)) < .02);
  const replacedLine = text => {
    const old = oldLines.find(l=>l.text === text);
    const current = old && currentLine(old);
    return current && current.text !== text ? current : null;
  };
  const rows = [], invalidatedTexts = new Set();
  for (const row of report.speech_lags || []) {
    const current = !report.speech_reference && currentLine(row);
    if (current && current.text !== row.text) {
      incomplete = true;
      invalidatedTexts.add(row.text);
      warnings.push(`旧检查使用原片台词“${row.text}”；本段实际台词为“${current.text}”；这一句无法判断`);
    } else {
      rows.push(row);
      if (Number.isFinite(row.lag_seconds) && Math.abs(row.lag_seconds) > threshold) deviation = true;
    }
  }
  for (const warning of report.warnings || []) {
    if (boundary && /成片运动节奏比原片|成片整体比原片|整体运动节奏|动作节奏相关系数偏低|画面变化曲线在 ±2 秒/.test(warning)) continue;
    const obsoleteTiming = warning.match(/^“([\s\S]+)” (?:提前|延后) [\d.]+ 秒$/);
    if (obsoleteTiming && invalidatedTexts.has(obsoleteTiming[1])) continue;
    const missing = warning.match(/^成片未可靠匹配台词：(.*)$/s);
    const changed = !report.speech_reference && missing && replacedLine(missing[1]);
    if (changed) {
      incomplete = true;
      warnings.push(`旧检查使用原片台词“${missing[1]}”；本段实际台词为“${changed.text}”；这一句无法判断`);
    } else {
      warnings.push(warning);
      if (/未完成|未判定|无法|未可靠匹配|缺少/.test(warning)) incomplete = true;
    }
  }
  if (boundary) warnings.unshift('画面变化曲线在 ±2 秒内没有找到可靠的对应位置（原片和成片的字幕、小窗等画面元素不同时常见），这一项不作判断');
  // Keep explicit failures for reports whose missing detail cannot be reinterpreted.
  if (!boundary && !lines.length && report.status === 'misaligned') deviation = true;
  const conclusion = deviation ? '有偏差' : incomplete || report.status === 'inconclusive' ? '无法判断' : '对齐';
  const lag = (value) => `${value < 0 ? '提前' : '延后'} ${Math.abs(value).toFixed(2)} 秒`;
  return `<p><b>结论：${report.status === 'running' ? '自动检查中…' : conclusion}</b></p>
    ${!boundary && Number.isFinite(report.motion_lag_seconds) ? `<p>整体运动节奏${lag(report.motion_lag_seconds)}；相关系数 ${esc(report.motion_correlation)}</p>` : ''}
    ${rows.map(r=>`<p>“${esc(r.text)}” ${Number.isFinite(r.lag_seconds) ? lag(r.lag_seconds) : '无法判断'}</p>`).join('')}
    ${[...new Set(warnings)].map(w=>`<p>${esc(w)}</p>`).join('')}`;
}
function openMotionCheck(tid, rev, button) {
  const record = P.runs?.[tid]?.find(r=>r.rev === rev);
  if (!record) return;
  const task = (S?.tasks || P.script?.tasks || []).find(t=>t.id === tid);
  const modal = $('#motionCheckModal');
  if (!modal) return;
  motionCheckReturnFocus = button;
  modal.innerHTML = `<div class="motion-check-content" role="document"><h3 id="motionCheckTitle">自动检查（仅供参考）</h3>
    <p>这是程序按画面变化和语音时间自动算的参考，不代表视频好不好。请以你自己看视频的感受为准。</p>
    ${motionCheckDetails(record.motion_check, task, record)}
    <h4>判断标准</h4>
    <p>整体节奏：把两段视频每秒的画面变化量排成曲线，在 ±2 秒内找最吻合的位置；相关系数 ≥ 0.45 且偏移 ≤ 0.5 秒算对齐。偏移落在搜索边界时，这一项不作判断。</p>
    <p>台词：用本地语音识别找出每句在成片里开始的时间，和原片比，差 ≤ 0.5 秒算对齐。优先匹配本段实际要说的台词；没有逐句台词时才用原片转录。</p>
    <div class="row end"><button type="button" data-motion-close>关闭</button></div></div>`;
  modal.hidden = false;
  $('[data-motion-close]', modal)?.focus();
}
function closeMotionCheck() {
  const modal = $('#motionCheckModal');
  if (modal) modal.hidden = true;
  if (motionCheckReturnFocus?.isConnected) motionCheckReturnFocus.focus();
  motionCheckReturnFocus = null;
}
function isPersonImage(id) {
  const asset = (P.assets || []).find((a) => a.id === id);
  const roles = asset?.roles || [];
  return asset?.type === "image" && (roles.includes("person_identity")
    || (roles.includes("target_state") && id !== P.replacements?.product?.asset_id && !roles.includes("product_truth")));
}
function firstPersonIndex(task) {
  const ids = task.image_ids || [];
  if(sceneEditorEnabled()){const row=sceneEditorRows().find(r=>r.scene_id===taskScene(task)),current=sceneCurrentCamera(taskScene(task));const index=ids.indexOf(current);if(index>=0)return index;const portraits=new Set((row?.subjects || []).filter(s=>s.on && s.kind==='character').map(s=>s.asset_id));return ids.findIndex(id=>portraits.has(id) && isPersonImage(id));}
  if (task.mode === "video") {
    const shots = (P.breakdown?.shots || []).filter((s) => Number(s.end) > Number(task.source_start || 0) && Number(s.start) < Number(task.source_end || Infinity));
    const visible = new Set(shots.flatMap((s) => s.characters || []));
    const people = (P.replacements?.characters || []).filter((c) => c.action === "replace" && (!visible.size || visible.has(c.source_id)));
    const bound = new Set(people.map(c => cameraImageFor(c.source_id, taskScene(task))).filter(Boolean));
    const index = ids.findIndex((id) => bound.has(id) && isPersonImage(id));
    if (index >= 0) return index;
    const portrait = ids.findIndex(id => people.some(c=>c.asset_id === id) && isPersonImage(id));
    if (portrait >= 0) return portrait;
  }
  return ids.findIndex(isPersonImage);
}
// UI-only guidance. Server errors remain the sole preview gate.
const ISSUE_TODOS = {
  analysis_no_caption_bbox: {step:'02',action:'重新拆解原片，补全字幕位置',reason:'上次拆解没有记录字幕在哪，无法处理字幕区域',target:'analysis',label:'去重新拆解'},
  person_keep_with_image: {step:'03',action:'把第 03 步的人物处理改成“替换”并保存',reason:'这一段选了新人物图，第 03 步人物仍是“保持”',target:'person',label:'去改人物处理'},
  person_image_mismatch: {step:'03',action:'在第 03 步选用本段对应的人物图并保存',reason:'本段人物图与第 03 步选的人物不一致',target:'person-asset',label:'去选人物图'},
  product_mismatch: {step:'03',action:'把第 03 步的商品处理改成“替换”并保存',reason:'这一段用了新商品图，但第 03 步商品还是“保持”或选了另一张图',target:'product',label:'去改商品处理'},
  camera_image_stale: {step:'03',action:'重新生成 {scene} 的主镜头图',reason:'人物、商品或场景设定改过，现在的主镜头图还是按旧设定生成的',target:'camera',label:'去生成主镜头图'},
  missing_camera_image: {step:'03',action:'生成 {scene} 的主镜头图',reason:'这一段要换人，这个场景还没有主镜头图',target:'camera',label:'去生成主镜头图'},
  invalid_camera_image: {step:'03',action:'重新生成 {scene} 的主镜头图',reason:'之前的主镜头图是按旧的人物或商品设定生成的，现在已失效',target:'camera',label:'去生成主镜头图'},
  script_stale: {step:'04',action:'重新生成新脚本',reason:'替换设定在生成脚本之后改过',target:'script',label:'去生成脚本'},
  performance_has_motion: {step:'04',action:'重新生成新脚本',reason:'脚本重复写了原片的身体动作，需要按当前规则重新生成',target:'script',label:'去生成脚本'},
  anchor_order: {step:'04',action:'重新生成新脚本',reason:'脚本中的台词顺序与原片动作对不上',target:'script',label:'去生成脚本'},
  appearance_conflict: {step:'04',action:'重新生成新脚本',reason:'脚本还在描述原片人物或商品的外观',target:'script',label:'去生成脚本'},
  reference_mapping: {step:'04',action:'重新生成新脚本',reason:'脚本里的参考图编号与本段图片顺序对不上',target:'script',label:'去生成脚本'},
  missing_dialogue: {step:'04',action:'补全本段台词并保存脚本',reason:'这是口播段落，脚本里还没有台词',target:'dialogue',label:'去补台词'},
  no_images: {step:'05',action:'在本段选上人物或主镜头参考图',reason:'这一段还没有选任何参考图',target:'references',label:'去选参考图'},
  invalid_image: {step:'05',action:'在本段去掉失效参考图并重新选图',reason:'选中的素材已删除或不是图片',target:'references',label:'去选参考图'},
  product_missing_image: {step:'05',action:'在本段参考图里选上商品图{product}',reason:'这一段要替换商品，还没选上第 03 步的商品图或包含它的主镜头图',target:'references',label:'去选参考图'},
  person_replace_missing_image: {step:'05',action:'在本段选上{person}的人物图或主镜头图',reason:'第 03 步设置了换人，本段却没有对应人物的参考图',target:'references',label:'去选参考图'},
  image1_not_person: {step:'05',action:'把人物或主镜头图放到本段图1',reason:'口播段落需要用出镜人物作为第一张参考图',target:'references',label:'去调整图1'},
  video_reference_required: {step:'05',action:'把本段参考方式改成“原片片段 + 图”',reason:'复刻动作或运镜需要用到原片片段',target:'mode',label:'去改参考方式'},
  camera_scene_mismatch: {step:'05',action:'换成 {scene} 当前的主镜头图',reason:'图1的场景与本段原片场景不同',target:'references',label:'去更换图1'},
  dialogue_conflict: {step:'04',action:'核对并改写本段提到旧外观的台词',reason:'台词提到的外观可能与新人物或新商品不符，可改写或明确保持原台词',target:'dialogue-proposal',label:'去核对台词'},
};
function checkIssues(check) {
  return check?.issues?.length ? check.issues : (check?.errors || []).map(title=>({code:'legacy',step:'05',title,details:[title]}));
}
function fixGroups(check, task = {}) {
  const issues = checkIssues(check).filter(i=>i.code !== 'motion_additions' && i.severity !== 'warning').flatMap(i=>i.affected?.length ? i.affected.map(context=>({...i,...context})) : [i]), todos = new Map();
  const sid = task.id && taskScene(task) || 'S1';
  const visibleIds = new Set((P.breakdown?.shots || []).filter(s=>Number(s.end) > Number(task.source_start || 0) && Number(s.start) < Number(task.source_end ?? Infinity)).flatMap(s=>s.characters || []));
  const visible = (P.breakdown?.characters || []).filter(c=>!offscreenCharacter(c) && (!visibleIds.size || visibleIds.has(c.id)));
  const character = issue => issue.character_id || P.assets?.find(a=>a.id === issue.asset_id)?.camera_character
    || (P.replacements?.characters || []).find(c=>c.asset_id === issue.asset_id)?.source_id
    || visible.find(c=>P.replacements?.characters?.some(r=>r.source_id === c.id && r.action === 'replace'))?.id || visible[0]?.id || '';
  const product = P.assets?.find(a=>a.id === P.replacements?.product?.asset_id);
  const cameraCodes = ['camera_image_stale','missing_camera_image','invalid_camera_image'];
  const roots = issues.filter(i=>cameraCodes.includes(i.code) && !i.expected_asset_id);
  const early = issues.some(i=>i.step < '04' && !cameraCodes.includes(i.code)) || roots.length;
  const scriptReasons = [];
  const scriptChanged = roots.length && issues.some(i=>i.code === 'script_stale' || i.details?.some(d=>/生成脚本之后改过/.test(d)));
  const put = todo => {
    if (todo.target === 'script' && todo.reason) scriptReasons.push(todo.reason);
    const previous = todos.get(todo.key);
    if (previous) { previous.issues.push(...todo.issues); if (todo.code === 'camera_image_stale') previous.reason = todo.reason; }
    else todos.set(todo.key,todo);
  };
  for (const issue of issues) {
    const info = ISSUE_TODOS[issue.code] || {step:issue.step || '05',action:issue.title || '核对本段参考图和脚本',reason:issue.fix || '检查发现本段还需要修正，请核对技术明细',target:'references',label:'去核对本段'};
    const scene = issue.scene_id || sid, cid = character(issue);
    const todo = {...info,code:issue.code,cid,sid:scene,tid:task.id || '',issues:[issue]};
    todo.action = todo.action.replace('{scene}',scene).replace('{product}', product ? `“${product.label || product.id}”` : '（第 03 步所选商品）').replace('{person}',cid || '替换人物');
    if (issue.code === 'product_mismatch') {
      const chosen = P.assets?.find(a=>task.image_ids?.includes(a.id) && a.roles?.includes('product_truth'));
      if (chosen) todo.action += `，选用商品图“${chosen.label || chosen.id}”`;
      todo.alternative = {target:'references',label:'去本段去掉这张商品图',note:'要换商品：改成替换并保存；保留原片商品：从本段去掉这张商品图。'};
    }
    if (issue.code === 'person_keep_with_image') todo.action = `把第 03 步 ${cid || '出镜人物'} 的人物处理改成“替换”并保存`;
    if (issue.code === 'person_image_mismatch') todo.action = `在第 03 步 ${cid || '出镜人物'} 的人物图里选用本段人物图并保存`;
    if (issue.code === 'person_keep_with_image') todo.alternative = {target:'references',label:'去本段去掉新人物图',note:'要换人：改成替换并保存；保留原片人物：从本段去掉新人物图。'};
    if (cameraCodes.includes(issue.code)) {
      if (issue.expected_asset_id && !roots.some(i=>(i.scene_id || sid) === scene && character(i) === cid)) {
        Object.assign(todo,{step:'05',action:`换成 ${scene} 当前的主镜头图`,reason:'本段图1尚未跟随当前在用的主镜头图，需要核对并保存',target:'references',label:'去更换图1'});
      } else {
        Object.assign(todo,{step:'03',action:`重新生成 ${scene} 的主镜头图`,target:'camera',label:'去生成主镜头图'});
        if (issue.code !== 'camera_image_stale') {
          const completed = Object.values(P.images || {}).filter(r=>r.state === 'completed' && (r.for_task === `camera:${cid}:${scene}` || sceneEditorEnabled() && r.for_task === `scene-camera:${scene}`));
          const outdated = completed.some(r=>P.camera_stale_assets?.includes(r.asset_id) || P.camera_stale_images?.includes(r.id))
            || roots.some(i=>character(i) === cid && (i.scene_id || sid) === scene && (i.code === 'camera_image_stale' || i.details?.some(d=>/替换依据.*变化|已过期/.test(d))));
          const deleted = completed.length && !outdated && completed.every(r=>!P.assets?.some(a=>a.id === r.asset_id));
          todo.action = `${completed.length ? '重新生成' : '生成'} ${scene} 的主镜头图`;
          todo.reason = !completed.length ? ISSUE_TODOS.missing_camera_image.reason : deleted ? '之前的主镜头图已从素材库删除' : ISSUE_TODOS.invalid_camera_image.reason;
        }
      }
    }
    if (todo.target === 'camera' && typeof cameraRecords === 'function') {
      const rec = cameraRecords(cid,scene).at(-1);
      if (rec?.state === 'pending_confirmation' && !cameraConfirmReason(cid,scene,rec)) {
        Object.assign(todo,{action:`确认生成 ${scene} 的主镜头图`,reason:'生成预览已完成，还需要确认生图（付费）才会出图',target:'camera-confirm',label:'去确认生图（付费）'});
      } else if (rec?.state === 'running' && !cameraRecordStale(rec)) {
        Object.assign(todo,{action:`等待 ${scene} 的主镜头图生成完成`,reason:'生图任务已提交，完成后会自动在用并更新本段图1',target:'camera-progress',label:'查看生成进度'});
      }
    }
    if (issue.code === 'camera_scene_mismatch' && issue.reference_index) Object.assign(todo,ISSUE_TODOS.script_stale,{reason:'本段横跨多个场景，脚本还没带上所需场景的主镜头图'});
    // Regenerating after step 03 rebuilds the reference list. Do not ask users to
    // repair a list that will be replaced, or to fix image1 which follows cameras.
    if (early && (['no_images','invalid_image','product_missing_image','person_replace_missing_image','image1_not_person','camera_scene_mismatch'].includes(issue.code) || todo.step === '04' || cameraCodes.includes(issue.code) && todo.step === '05')) {
      Object.assign(todo,ISSUE_TODOS.script_stale,{reason:info.step === '04' ? info.reason : ''});
    }
    todo.key = todo.target.startsWith('camera') && todo.target !== 'references' ? `camera:${sceneEditorEnabled()?'scene':cid}:${scene}` : todo.target === 'script' ? 'script'
      : cameraCodes.includes(issue.code) || issue.code === 'camera_scene_mismatch' && todo.target === 'references' ? `camera-reference:${cid}:${scene}` : `${issue.code}:${cid}:${scene}`;
    put(todo);
  }
  // A stale camera embeds the script-stale error in its details. It still needs
  // one explicit later action even when the backend has merged away that code.
  if (roots.length && issues.some(i=>i.code === 'camera_image_stale' || i.details?.some(d=>/重新生成新脚本|生成脚本之后改过/.test(d)))) {
    put({...ISSUE_TODOS.script_stale,key:'script',code:'script_stale',tid:task.id || '',issues:[],reason:''});
  }
  const scriptTodo = todos.get('script');
  if (scriptTodo) {
    const reasons = [...new Set([...(scriptChanged ? [ISSUE_TODOS.script_stale.reason] : []),...scriptReasons])];
    scriptTodo.reason = reasons.length ? joinDisplayText(reasons.slice(0,2)) + (reasons.length > 2 ? '等' : '') : '主镜头图更新后，重新生成脚本会自动带上新的参考图';
  }
  const items = [...todos.values()];
  const cameraReferences = items.filter(t=>t.key.startsWith('camera-reference:'));
  return items.filter(todo=>{
    if (todo.code === 'no_images' && items.some(t=>t !== todo && t.target === 'references' && t.code !== 'image1_not_person')) return false;
    if (todo.code === 'person_replace_missing_image' && cameraReferences.some(t=>t.cid === todo.cid && t.sid === todo.sid)) return false;
    if (todo.code === 'image1_not_person' && cameraReferences.length) return false;
    if (todo.code === 'product_missing_image' && cameraReferences.some(t=>P.assets?.some(a=>a.id === t.issues[0]?.expected_asset_id && a.camera_product_id && a.camera_product_id === P.replacements?.product?.asset_id))) return false;
    return true;
  }).sort((a,b)=>a.step.localeCompare(b.step));
}
function previewReasonHtml(check, tid) {
  const task = typeof S !== 'undefined' && S?.tasks?.find(t=>t.id === tid) || {id:tid};
  const todos = fixGroups(check,task);
  return todos.length ? `<button class="ghost small fix-reason" data-fix-guide="${esc(tid)}">还要做 ${todos.length} 件事：先到第 ${todos[0].step} 步</button>` : '';
}
function todoButton(todo, target = todo.target, label = todo.label) {
  return `<button class="small ghost" data-todo-target="${esc(target)}" data-fix-step="${esc(target === 'references' ? '05' : todo.step)}" data-fix-task="${esc(todo.tid)}" data-fix-character="${esc(todo.cid)}" data-fix-scene="${esc(todo.sid)}">${esc(label)}</button>`;
}
const motionWarningClosed = new Map();
function motionWarningContent(task) {
  return JSON.stringify(['action','story','direction','dialogue','mode','source_start','source_end','image_ids'].map(k=>task[k] ?? null));
}
function motionWarningHtml(check, task) {
  const warning = check.motion_warning;
  const details = warning?.details || check.issues?.find(i=>i.code === 'motion_additions')?.details || check.action_comparison?.errors || [];
  if (!details.length) return '';
  const content = motionWarningContent(task), closed = motionWarningClosed.get(`${P.id}:${task.id}`);
  if (closed?.fingerprint === warning?.fingerprint && closed?.content === content || warning?.dismissed && (!warning.content || JSON.stringify(warning.content) === content)) return '';
  return `<section class="caption-warning" data-motion-warning="${esc(task.id)}"><p>动作对比提醒：这一段的表演说明里有 ${details.length} 处和原片时机不一致，生成的视频可能和原片动作对不上。</p>${details.map(detail=>{
    const [reason,...quote] = detail.split('原句：');
    return `<p>${esc(reason.replace(/[；;]\s*$/,''))}</p>${quote.length ? `<details><summary>原句</summary><p>${esc(quote.join('原句：'))}</p></details>` : ''}`;
  }).join('')}<button class="small ghost" data-motion-edit="${esc(task.id)}">改这句</button> <button class="small ghost" data-motion-dismiss="${esc(task.id)}">不用改，继续</button></section>`;
}
async function dismissMotionWarning(tid) {
  const task = S.tasks.find(t=>t.id === tid), check = P.replacement_checks?.[tid], warning = check?.motion_warning;
  if (!warning?.fingerprint) return;
  const pid = P.id;
  await api('POST',`projects/${pid}/tasks/${tid}/dismiss-motion-warning`,{fingerprint:warning.fingerprint});
  if(P?.id !== pid)return;
  motionWarningClosed.set(`${pid}:${tid}`,{fingerprint:warning.fingerprint,content:motionWarningContent(task)});
  const box = $(`[data-replacement-check="${tid}"]`);
  if(box)updateHtml(box,replacementCheckHtml(check,task));
}
function replacementCheckHtml(check, task = {}) {
  if (!check) return '<p class="hint">正在检查替换设定与参考图…</p>' + actionComparisonHtml(null,task) + dialogueStatusHtml(task);
  const todos = fixGroups(check,task);
  const detailRows = checkIssues(check).map(issue=>({...issue,details:[...new Set([issue.title,...(issue.details || [])].filter(d=>d && !/^seg-[\w-]+$/.test(d.trim())))]}));
  const wizard = todos.length ? `<section class="fix-wizard" aria-label="预览修复向导">
    <h4>${esc(task.id || '本段')} 预览前还要做 ${todos.length} 件事</h4>
    ${todos.map((todo,index)=>`<div class="fix-step ${index ? 'pending' : 'next'}" data-todo-key="${esc(todo.key)}"><div class="fix-issue" data-fix-code="${esc(todo.key)}"><p class="fix-heading"><b>${index+1}. ${index ? '之后做' : '现在做'} · 第 ${todo.step} 步：${esc(todo.action)}</b></p><p class="hint">为什么：${esc(todo.reason)}</p>${todoButton(todo)}${todo.alternative ? `${todoButton(todo,todo.alternative.target,todo.alternative.label)}<p class="hint">${esc(todo.alternative.note)}</p>` : ''}</div></div>`).join('')}
    <details class="technical-details" data-detail-key="technical-details"><summary>技术明细</summary>${detailRows.map(i=>`<details data-detail-key="${esc([i.code,i.character_id,i.scene_id].filter(Boolean).join(':'))}"><summary>${esc(i.code)}（${i.details.length} 条）</summary><ul>${i.details.map(d=>`<li${i.code === 'legacy' ? ' class="hint" style="color:var(--pay)"' : ''}>${esc(d)}</li>`).join('')}</ul></details>`).join('')}</details></section>` : '<p class="hint">替换设定与参考图检查通过</p>';
  const conflicts = check.dialogue_conflicts || [];
  const warnings = (check.warnings || []).filter(w => !conflicts.some(c=>c.title === w) && !(conflicts.length && /台词里有[\s\S]*和新商品可能不符/.test(w))
    && (task.blank_captions === true || !/遮挡|去字幕|字幕残留|无字幕基准/.test(w)));
  const dialogueTodo = {...ISSUE_TODOS.dialogue_conflict,tid:task.id || ''};
  const conflictHtml = conflicts.map(c=>`<div class="caption-warning dialogue-conflict"><p>${esc(dialogueTodo.action)}</p><p>为什么：${esc(dialogueTodo.reason)}</p>${todoButton(dialogueTodo)}<details data-detail-key="dialogue-conflict"><summary>技术明细</summary><ul>${[c.title,...(c.details || [])].filter(Boolean).map(d=>`<li>${esc(d)}</li>`).join('')}</ul></details></div>`).join('');
  return wizard + `<div class="check-warnings">${motionWarningHtml(check,task)}${actionComparisonHtml(check.action_comparison,task)}${conflictHtml}${dialogueStatusHtml(task,check)}${warnings.map(w=>`<p class="caption-warning">${esc(w)}</p>`).join('')}</div>`;
}
function focusTodo(todo) {
  const taskCard = $$('[data-task]').find(el=>el.dataset.task === todo.tid);
  const characterRow = sceneEditorEnabled() ? $(`[data-cast-row="${todo.cid}"]`) : $$('#replacementForm tr[data-char]').find(el=>el.dataset.char === todo.cid);
  const camera = sceneEditorEnabled() ? $(`[data-scene-camera="${todo.sid}"]`) : $$('[data-camera-key]').find(el=>el.dataset.cameraKey === `${todo.cid}:${todo.sid}`);
  const seg = $$('#scriptView [data-seg-key]').find(el=>el.dataset.segKey === todo.tid);
  const target = ({analysis:()=>$('#analyzeBtn'),camera:()=>camera && $('[data-scene-draft], [data-camdraft]',camera),
    'camera-confirm':()=>camera && $('[data-scene-confirm], [data-imgconfirm]',camera),'camera-progress':()=>camera && $('.camera-state',camera),
    product:()=>sceneEditorEnabled() ? $('[data-cast="product"]') : $('#replacementForm tr[data-prod] [data-r="action"]'),person:()=>characterRow && $('[data-cast], [data-r="action"]',characterRow),
    'person-asset':()=>characterRow && $('[data-cast], [data-r="asset"]',characterRow),script:()=>$('#scriptBtn'),
    dialogue:()=>seg && $('[data-dialogue-text], [data-k="dialogue"]',seg),
    performance:()=>seg && $('[data-k="action"], [data-k="story"], [data-k="direction"]',seg),
    'dialogue-proposal':()=>seg && ($('[data-dialogue-box]',seg) || $('.speaker-editor',seg) || $('[data-dialogue-section]',seg)),
    references:()=>taskCard && $('.chips',taskCard),mode:()=>taskCard && $('[data-m="mode"]',taskCard)})[todo.target]?.();
  if (!target) return jumpToStep(todo.step,todo.tid,todo.cid,todo.sid);
  $('#statusToggle')?.setAttribute('aria-expanded','false'); updateScrollOffset();
  target.scrollIntoView({behavior:'smooth',block:'center'});
  if(todo.target === 'performance')target.focus({preventScroll:true});
  clearTimeout(target._highlightTimer); target.classList.add('fix-highlight');
  target._highlightTimer = setTimeout(()=>target.classList.remove('fix-highlight'),3500);
}
function renderLegacyNotice() {
  const box = $("#legacyProjectBanner");
  const overlays = P?.breakdown?.overlays || [];
  const hasCaptionBox = overlays.some((o) => ["caption", "typography", "sticker"].includes(o.kind)
    && Array.isArray(o.bbox) && o.bbox.length === 4 && o.bbox.every(Number.isFinite)
    && o.bbox[2] > o.bbox[0] && o.bbox[3] > o.bbox[1]);
  box.hidden = !P?.breakdown || !P?.script || hasCaptionBox || !!P.script.replacements_fingerprint;
  box.textContent = "这个项目是旧版本流程做的，请按修复向导更新（大约需要重新拆解和生成一次脚本）";
}
function updateScrollOffset() {
  const height = $(".topbar")?.getBoundingClientRect().height || 0;
  document.documentElement.style.setProperty("--topbar-height", `${Math.ceil(height)}px`);
}
function jumpToStep(step, tid, cid, sid) {
  $("#statusToggle")?.setAttribute("aria-expanded", "false");
  updateScrollOffset();
  if(Number(step)>=4 && $("#legacyWorkflow"))$("#legacyWorkflow").open=true;
  const panel = Number(step)>=4 ? $(`#legacy-step-${Number(step)}`) : $(`.panel[data-step="${Number(step)}"]`);
  let controls = [];
  if (Number(step) === 2) controls = [$("#analyzeBtn")];
  if (Number(step) === 3) {
    const task = S?.tasks?.find((t) => t.id === tid);
    const shots = (P.breakdown?.shots || []).filter((s) => Number(s.end) > Number(task?.source_start || 0) && Number(s.start) < Number(task?.source_end || Infinity));
    const visible = new Set(shots.flatMap((s) => s.characters || []));
    const rows = $$("#replacementForm tr[data-char]").filter((row) => !visible.size || visible.has(row.dataset.char));
    const issue = P.replacement_checks?.[tid]?.issues?.find(i => ['missing_camera_image','camera_image_stale'].includes(i.code));
    cid = cid || issue?.character_id;
    sid = sid || issue?.scene_id || (task && taskScene(task));
    const cards = $$('[data-camera-key]').filter(card => (!cid || sceneEditorEnabled() || card.dataset.cameraKey === `${cid}:${sid}`) && (!sid || card.dataset.scene === sid));
    controls = cards.map(card => $('[data-scene-draft], [data-camdraft]',card));
    if (!controls.length) controls = rows.flatMap(row => [$('[data-r="action"]',row),$('[data-camdraft]',row)]);
    controls.push($('#replacementForm tr[data-prod] [data-r="action"]'), $('#saveReplacements'));
  }
  if (Number(step) === 4) controls = [$("#scriptBtn")];
  if (Number(step) === 5) controls = [$(`[data-task="${tid}"] .chips`), $(`[data-preview="${tid}"]`)];
  controls = controls.filter(Boolean);
  (controls[0] || panel)?.scrollIntoView({ behavior: "smooth", block: "start" });
  controls.forEach((control) => {
    clearTimeout(control._highlightTimer); control.classList.add("fix-highlight");
    control._highlightTimer = setTimeout(() => control.classList.remove("fix-highlight"), 3500);
  });
}
function showPreviewBlocked(tid, check) {
  P.replacement_checks = { ...P.replacement_checks, [tid]: check };
  // Preview validation can be richer than the lightweight check. Keep its guide
  // until the relevant inputs change, and reject any older in-flight response.
  clearTimeout(replacementCheckTimer); ++replacementCheckSequence; replacementCheckPending = "";
  replacementCheckCache = { pid: P.id, key: replacementCheckInput().key, checks: P.replacement_checks };
  const task = S?.tasks?.find((t) => t.id === tid);
  const dialogue = $(`[data-dialogue-section="${tid}"]`);
  if (dialogue) updateHtml(dialogue, dialogueProposalHtml(task,check));
  const box = $(`[data-replacement-check="${tid}"]`);
  if (box) updateHtml(box, replacementCheckHtml(check, task));
  const reason = $(`[data-preview-reason="${tid}"]`);
  if (reason) updateHtml(reason, previewReasonHtml(check, tid));
  const button = $(`[data-preview="${tid}"]`);
  if (button) button.disabled = true;
  toast(`预览前还要做 ${fixGroups(check,task).length} 件事，已在 ${tid} 下方列出`, true);
}
async function fixSceneCamera(tid, cid, sid) {
  if(scriptSaveState())return;
  if ($('#scriptView .seg')) readSegEditors();
  const task = S.tasks.find(t=>t.id === tid), aid = cameraImageFor(cid,sid);
  if (!task || !aid) return;
  const previous = [...(task.image_ids || [])],pid=P.id;
  const previousCamera = taskCameraReference(task);
  const repair=(previousCamera?.cid === cid && previousCamera.sid === sid && previousCamera.aid !== aid) ||
    P.replacement_checks?.[tid]?.issues?.some(i => ['invalid_camera_image','camera_image_stale'].includes(i.code) && i.reference_index === 1);
  const wanted=[aid,...previous.slice(1).filter(id=>repair || id!==aid)];
  try {return await withScriptSave(`[data-fix-camera="${tid}"]`,async state=>{
    if (repair) {
      if (dirty.script) await saveScript(true,undefined,state);
      if(P?.id!==pid)return;
      const np=await api('POST',`projects/${pid}/script/camera-repair`,{task_id:tid,character_id:cid,scene_id:sid});
      if(P?.id!==pid)return;
      P={...P,...np};S=clone(P.script);dirty.script=false;seen.script='';renderAll();scheduleReplacementCheck();return;
    }
    task.image_ids = wanted; dirty.script = true;
    await saveScript(true,undefined,state);
    if(P?.id!==pid)return;
    toast(`已换成 ${sid} 的主镜头图`);scheduleReplacementCheck();
  },latest=>JSON.stringify(latest.script?.tasks?.find(t=>t.id===tid)?.image_ids)===JSON.stringify(wanted),{[tid]:['image_ids']});}
  catch(error) { if(P?.id===pid){task.image_ids=previous;toast(error.message,true);} }
}
async function fixImageOrder(tid) {
  if(scriptSaveState())return;
  if ($("#scriptView .seg")) readSegEditors();
  const task = S.tasks.find((t) => t.id === tid);
  if (!task) return;
  const index = firstPersonIndex(task);
  if (index <= 0) return;
  const ids = [...task.image_ids];
  const ordered = [ids[index], ...ids.filter((_, i) => i !== index)],pid=P.id;
  try {
    return await withScriptSave(`[data-fix-image1="${tid}"]`,async state=>{
      task.image_ids=ordered;
      // Explicit draft order also remaps unsaved text, exactly once, in the existing server rebase.
      dirty.script=true;
      await saveScript(true,{[tid]:ids},state);
      if(P?.id!==pid)return;
      toast("人物/主镜头图已放到图1，图号引用已同步保存");scheduleReplacementCheck();
    },latest=>JSON.stringify(latest.script?.tasks?.find(t=>t.id===tid)?.image_ids)===JSON.stringify(ordered));
  } catch (error) {
    if(P?.id!==pid)return;
    task.image_ids = ids;
    // Keep the existing save checks: an old motion script must first be regenerated.
    const checks = await api("POST", `projects/${P.id}/replacement-check`, { tasks: S.tasks }).catch(() => null);
    if (checks?.[tid]?.errors?.length) showPreviewBlocked(tid, checks[tid]);
    else toast(error.message, true);
  }
}
async function previewTask(tid, button, trigger = `[data-preview="${tid}"]`) {
  if(scriptSaveState())return;
  const check = P.replacement_checks?.[tid];
  if (check?.errors?.length) { showPreviewBlocked(tid, check); return; }
  const pid=P.id;
  try {
    return await withScriptSave(trigger,async state=>{
      await saveScript(true,undefined,state);
      if(P?.id!==pid)return;
      await api("POST", `projects/${pid}/tasks/${tid}/preview`);
      if(P?.id!==pid)return;
      toast("预览已生成，请核对后确认");await refresh();return true;
    });
  } catch (error) {
    if(P?.id!==pid)return;
    if (error.issues?.length) showPreviewBlocked(tid, { issues: error.issues, errors: error.errors || [error.message] });
    else {
      const checks = await api("POST", `projects/${P.id}/replacement-check`, { tasks: S.tasks }).catch(() => null);
      if (checks?.[tid]?.errors?.length) showPreviewBlocked(tid, checks[tid]);
      else toast(error.message, true);
    }
  } finally {
    if(P?.id!==pid)return;
    const current = $(`[data-preview="${tid}"]`);
    if (current) {
      current.disabled = !!P.replacement_checks?.[tid]?.errors?.length;
      current.textContent = S.tasks.find((t) => t.id === tid)?.mode === "video" ? "生成预览（含模型动作检查）" : "生成预览（免费）";
    }
    scheduleReplacementCheck();
  }
}
function actionComparisonHtml(check, task) {
  const completed = check?.status === 'completed' && check?.model;
  if (!completed && (P?.runs?.[task?.id] || []).some(r=>r.state === 'completed' || task.chosen_rev === r.rev && r.result)) {
    return '<p class="hint">下次生成新预览时会重新做动作对比</p>';
  }
  const text = completed ? `已完成（由 ${publicText(check.provider)} / ${publicText(check.model)}）`
    : `未完成（${check?.reason || '没有本次动作检查记录，请重新生成预览'}）`;
  return `<p class="${completed ? 'hint' : 'caption-warning'}">动作对比：${esc(text)}</p>`;
}
function captionResidueHtml(result) {
  if (!result) return "";
  if (result.status === "skipped") return `<p class="hint">${esc(result.reason)}</p>`;
  if (result.status === "unavailable") return `<p class="caption-warning" role="alert">${esc(result.reason)}</p>`;
  if (!result.suspected?.length) return '<p class="hint">本地抽帧未发现明显字幕残留，请播放参考片段复核。</p>';
  return `<div class="caption-warning" role="alert"><b>可能有字幕残留，请查看下方小图并播放参考片段判断；此提示不阻止生成。</b>
    <div class="caption-residue-frames">${result.suspected.map((s) => `<figure>
      ${s.thumbnail ? `<a href="${fileUrl(s.thumbnail)}" target="_blank" rel="noopener"><img src="${fileUrl(s.thumbnail)}" alt="片段 ${fmt(s.time)} 秒疑似字幕残留" loading="lazy"></a>` : ""}
      <figcaption>片段 ${fmt(s.time)} 秒 · 原片 ${fmt(s.source_time)} 秒 · 得分 ${esc(s.score)}</figcaption>
    </figure>`).join("")}</div></div>`;
}
function captionWarningsHtml(masks) {
  // Old pending previews can still carry warnings for the former 0.30 bands.
  return (masks || []).filter(m => m.motion_warning && m.bbox?.[3] - m.bbox?.[1] > .35 + 1e-9).map(m =>
    `<p class="caption-warning">${fmt(m.start)}–${fmt(m.end)} 秒：${esc(m.motion_warning)}</p>`).join('');
}
function readTaskCard(el) {
  const t = S.tasks[Number(el.dataset.i)];
  $$("[data-m]", el).forEach((f) => { t[f.dataset.m] = f.type === "checkbox" ? f.checked : (f.value || null); });
}
function renderRuns(t) {
  const live = $(`[data-live="${t.id}"]`);
  if (!live) return;
  const runs = (P.runs?.[t.id] || []).filter((r) => !["superseded", "cancelled"].includes(r.state));
  const pending = runs.filter((r) => r.state === "pending_confirmation").slice(-1)[0];
  const done = runs.filter((r) => r.state !== "pending_confirmation");
  const taskJobs = Object.entries(P.jobs || {}).filter(([k]) => k.startsWith(`gen:${t.id}:`));
  const job = taskJobs.map(([, j]) => j).slice(-1)[0];
  const imgs = Object.values(P.images || {}).filter((r) => r.for_task === t.id);
  const sig = JSON.stringify([P.id, runs, taskJobs, imgs, t.chosen_rev, t.blank_captions]);
  if (live.dataset.sig === sig) return;
  live.dataset.sig = sig;
  let html = "";
  if (pending) {
    const task = pending.preview.tasks[0];
    html += `<div class="preview" data-run-key="preview:${esc(pending.rev)}"><div class="meta"><b>${esc(pending.rev)} 待确认</b><span>模型 <b>${esc(publicText(pending.model))}</b></span><span>时长 ${fmt(task.duration)}s</span><span>比例 ${task.ratio}</span>
      <span>参考图 ${task.images.length} 张</span><span>参考视频 ${task.reference_videos.length} 段</span><span>参考音频 ${(task.reference_audios || []).length} 条</span>
      <span>计费约 <b>${fmt(pending.billing_seconds)} 秒</b>（${esc(publicText(pending.billing_note))}）</span></div>
      ${pending.clip ? `<p><a href="${fileUrl(pending.clip)}" target="_blank" rel="noopener">播放处理后参考片段</a></p><video src="${fileUrl(pending.clip)}" controls muted></video>` : ""}
      ${pending.clip_processing ? `<p class="hint">${pending.clip_processing.muted ? "已去掉原声" : "保留原声"}</p>` : ''}
      ${t.blank_captions === true && pending.clip_processing && pending.clip_processing.blank_captions !== false ? `<p class="hint">字幕区域处理：${pending.clip_processing.masks.length} 处</p>
      <details data-detail-key="caption-masks"><summary>字幕处理区域和时间（片段内秒数）</summary>${pending.clip_processing.masks.map((m) => `<p>${fmt(m.start)}–${fmt(m.end)} 秒 · ${esc(m.system || m.kind || "")} · 区域 ${esc(JSON.stringify(m.bbox))} · ${esc(m.text)}
        ${m.source_bboxes ? `<br>合并原框：${esc(JSON.stringify(m.source_bboxes))}；两侧各扩 ${esc(m.padding?.x)}、上下各扩 ${esc(m.padding?.y)}，时间前后各扩 ${fmt(m.padding?.time)} 秒` : ""}
        ${m.height_limit_reason ? `<br>${esc(m.height_limit_reason)}` : ""}</p>`).join("") || "无需要处理的区域"}</details>` : ""}
      ${t.blank_captions === true ? captionWarningsHtml(pending.clip_processing?.masks) : ''}
      ${(pending.warnings || []).filter(w=>t.blank_captions === true || !/遮挡|去字幕|字幕残留|无字幕基准/.test(w)).map((w) => `<p class="caption-warning">${esc(w)}</p>`).join('')}
      ${actionComparisonHtml(pending.action_comparison,t)}
      ${t.blank_captions === true ? captionResidueHtml(pending.caption_residue) : ''}
      <pre>${esc(task.prompt)}</pre>
      <details data-detail-key="preview-job"><summary>完整任务单（三方渠道请求） · sha256 ${esc(pending.job_sha256.slice(0, 12))}…</summary><pre>${esc(JSON.stringify(pending.preview, null, 2))}</pre></details>
      <div class="row end"><button class="ghost" data-cancel="${t.id}" data-rev="${pending.rev}">取消</button>
        <button class="pay" data-confirm="${t.id}" data-rev="${pending.rev}" data-sha="${pending.job_sha256}">确认生成（付费，提交 1 个三方渠道任务）</button></div></div>`;
  }
  if (job?.state === "error" && !runs.some(r=>r.query_retry && job === P.jobs?.[`gen:${t.id}:${r.rev}`])) html += `<p class="hint" style="color:var(--pay)">上次执行出错：${esc(publicText(job.error))}</p>`;
  if (done.length) {
    html += `<div class="runs">${done.map((r) => `<div data-run-key="${esc(r.rev)}" class="run ${t.chosen_rev === r.rev || (!t.chosen_rev && r === done.filter((x) => x.state === "completed").slice(-1)[0]) ? "chosen" : ""}">
      <div><b>${esc(r.rev)}</b> · ${esc(publicText(r.model))} · <span class="state ${r.state}">${{ completed: "已完成", failed: "失败", unknown: "提交结果未知", running: "生成中", query_pending: "等待查询", submitting: "提交中" }[r.state] || esc(r.state)}</span></div>${queryRetryHtml(r)}
      ${r.remote_status && !r.query_retry ? `<div class="hint">远端状态 ${esc(Object.hasOwn(REMOTE_STATUS_LABELS,r.remote_status) ? REMOTE_STATUS_LABELS[r.remote_status] : r.remote_status)}</div>` : ''}
      ${r.remote_task_id ? `<details data-detail-key="remote-task"><summary>详细信息</summary><p class="hint">任务号：<code>${esc(r.remote_task_id)}</code> <button class="small ghost" data-copy-task="${esc(r.remote_task_id)}">复制任务号</button></p></details>` : ''}
      ${r.result ? `<video src="${fileUrl(r.result)}" controls preload="metadata"></video>` : ""}
      ${r.copy_suspected ? `<p role="alert" style="color:var(--pay);font-weight:bold">成片与原片高度相似，替换可能没有生效，请检查后再使用（相似度 ${Number(r.similarity).toFixed(4)}）</p>` : ""}
      ${r.copy_check?.error ? `<p class="hint" style="color:var(--pay)">${esc(publicText(r.copy_check.error))}</p>` : ""}
      ${motionCheckHtml(r.motion_check || (r.mode === 'video' && r.state === 'completed' ? {status:'inconclusive'} : null), t.id, r.rev)}
      ${speechDelayHtml(r)}
      ${r.error ? `<div class="hint" style="color:var(--pay)">${esc(publicText(r.error))}</div>` : ""}
      ${r.state === "failed" && r.superseded_by ? `<div class="hint">已被 ${esc(r.superseded_by)} 取代，这次失败不影响成片</div>` : ""}
      ${r.state === "failed" && !r.superseded_by && !r.remote_task_id ? `<div class="hint">${r.retryable ? "还没在三方渠道创建任务，没有产生视频费用；检查网络/Key 后可使用原确认任务重新提交。重新提交成功创建任务后会正常计费。" : "无法确认是否已经创建远端任务，可能已经扣费；请用刷新/恢复，或填写远端任务 ID 恢复。"}</div>` : ""}
      ${r.state === "unknown" ? `<div class="hint">提交请求发出后没收到结果，可能已经扣费。不要重复提交：到三方渠道后台找到这次任务 ID 填进来恢复。</div>` : ""}
      <div class="row" style="margin:6px 0 0;flex-wrap:wrap">
        ${r.state === "completed" ? `<button class="small ghost" data-choose="${t.id}" data-rev="${r.rev}">选用这一版</button>` : ""}
        ${r.state === "failed" && !r.superseded_by && r.retryable ? `<button class="small ghost" data-resubmit="${t.id}" data-rev="${r.rev}">重新提交（上次没有扣费）</button>` : ""}
        ${!(r.state === "failed" && r.superseded_by) && ((["running", "submitting", "failed", "query_pending"].includes(r.state) && r.remote_task_id) || (r.state === "failed" && !r.retryable)) ? `<button class="small ghost" data-resume="${t.id}" data-rev="${r.rev}">${r.state === 'query_pending' ? '再查一次' : '刷新/恢复'}</button>` : ""}
        ${!(r.state === "failed" && r.superseded_by) && (r.state === "unknown" || (!r.remote_task_id && ((r.state === "failed" && !r.retryable) || (r.phase === "create_started" && P.jobs?.[`gen:${t.id}:${r.rev}`]?.state !== "running")))) ? `<button class="small ghost" data-recover="${t.id}" data-rev="${r.rev}">填写远端任务 ID 恢复</button>` : ""}
      </div></div>`).join("")}</div>`;
  }
  if (imgs.length) {
    html += `<div class="runs">${imgs.map(imageRecordCard).join("")}</div>`;
  }
  updateHtml(live, html);
}
function openImageForm(tid) {
  const box = $(`[data-imgbox="${tid}"]`);
  const t = S.tasks.find((x) => x.id === tid);
  box.hidden = !box.hidden;
  if (box.hidden) return;
  box.innerHTML = `<p class="hint">缺少“新人物/新商品在目标机位、持物或穿着状态”的画面时才需要补图。参考图会按顺序作为图1、图2…传给生图模型。</p>
    <textarea rows="3" data-ig="prompt">${esc(t.need_image ? `图1确定人物身份和服装，图2确定商品真实外观。${t.need_image}。保留人物与商品细节、手部接触和真实尺度，不要字幕、不要拼贴。` : "")}</textarea>
    <div class="row wrap"><label>模型 <select data-ig="model">${IMAGE_MODELS.map((m) => `<option>${m}</option>`).join("")}</select></label>
      <label>分辨率 <select data-ig="resolution"><option>2k</option><option>1k</option><option>4k</option></select></label>
      <span class="hint">参考图沿用上面选中的图（${(t.image_ids || []).length} 张）</span>
      <button class="small" data-imgpreview="${tid}">预览生图任务（免费）</button></div>`;
}

// ---------- final ----------
function usageHtml() {
  const preview=r=>r.kind==='preview' || (r.kind==='image' && !r.network_request && !r.request_sent && !r.remote_task_id
    && !Object.values(r.tokens || {}).some(n=>Number(n)>0) && !(Number(r.billing?.amount)>0));
  const rows=(P?.usage || []).filter(r=>!preview(r)), billed=r=>Number(r.billing?.amount || 0);
  const official=rows.filter(r=>['official','openai'].includes(r.channel) && (r.kind==='reason' || r.kind==='image' || r.billing?.amount!=null));
  const tokens=official.filter(r=>r.billing?.unit==='tokens').reduce((n,r)=>n+billed(r),0);
  const points=rows.filter(r=>r.billing?.unit==='点').reduce((n,r)=>n+billed(r),0);
  const seconds=rows.filter(r=>r.billing?.unit==='秒').reduce((n,r)=>n+billed(r),0);
  return `<details data-detail-key="project-usage"><summary>本项目用量：官方 ${official.length} 次 / 约 ${tokens} 个 token；三方渠道 ${Number(points.toFixed(6))} 点；视频 ${fmt(seconds)} 秒</summary><p class="hint">仅列出响应提供的用量，不估算金额；未知用量显示“未提供”。</p><table><tr><th>时间</th><th>步骤 / 渠道 / 模型</th><th>结果</th><th>用量</th></tr>${rows.map(r=>`<tr><td>${esc(r.at || '')}</td><td>${esc(r.step || '')} / ${esc(providerLabel(r.channel,STATUS?.providers?.[r.channel]))} / ${esc(publicText(r.model || ''))}</td><td>${r.ok ? '成功' : '失败：'+esc(r.error_kind || 'unknown')}</td><td>${r.billing?.unit==='tokens' ? tokenUsageText({...r.tokens,total_tokens:r.billing.amount ?? r.tokens?.total_tokens}) : r.billing?.amount==null ? '未提供' : esc(r.billing.amount)+' '+esc(r.billing.unit)}</td></tr>`).join('')}</table></details>`;
}
const assemblySaving=new Set(),assemblyErrors=new Map();
async function saveAssemblyOption(input) {
  const pid=P.id;if(assemblySaving.has(pid))return;
  const trim=input.checked;assemblySaving.add(pid);assemblyErrors.delete(pid);renderFinal();
  try {
    const updated=await api('PUT',`projects/${pid}/assembly-options`,{trim_to_source:trim});
    if(P?.id===pid)P.assembly_options=updated.assembly_options;
  }catch(error){assemblyErrors.set(pid,publicText(error.message || '这次没保存成功，请重试'));}
  finally {assemblySaving.delete(pid);if(P?.id===pid)renderFinal();}
}
function renderFinal() {
  const tasks = S?.tasks || [];
  const rows = tasks.map((t) => {
    const ok = (P.runs?.[t.id] || []).filter((r) => r.state === "completed");
    const pick = ok.find((r) => r.rev === t.chosen_rev) || ok.slice(-1)[0];
    return `<li>${esc(t.id)} ${esc(t.title)}：${pick ? `使用 ${esc(pick.rev)}` : `<span style="color:var(--pay)">还没有成功版本</span>`}</li>`;
  }).join("");
  const usage = P.usage || [];
  const usageFingerprint = [usage.length, usage.at(-1)?.at, usage];
  const editsStale=!!P.scene_edits_script_stale;
  const trim=P.assembly_options?.trim_to_source!==false,saving=assemblySaving.has(P.id),error=assemblyErrors.get(P.id);
  const sig = JSON.stringify([P.id, rows, P.final, usageFingerprint,editsStale,trim,saving,error]);
  const box = $("#finalView");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  updateHtml(box, `${usageHtml()}${editsStale?'<p class="caption-warning">替换设定在生成脚本之后改过，请重新生成新脚本</p>':''}<ul>${rows}</ul><label class="check"><input type="checkbox" id="trimToSource" ${trim?'checked':''} ${saving?'disabled':''}> 按原片时长裁剪每段（保持原片节奏）</label>${error?`<p class="caption-warning" role="status">${esc(error)}</p>`:''}<div class="row"><button id="assembleBtn" ${tasks.length && !editsStale && !saving ? "" : "disabled"}>按顺序拼接成片</button></div>
    ${P.final ? `<div class="final"><video src="${fileUrl(P.final)}" controls></video><p><a href="${fileUrl(P.final)}" download>下载成片</a> · 文件在项目目录 ${esc(P.final)}</p></div>` : ""}`);
}
function renderLog() {
  updateHtml($("#logView"), (P.log || []).slice().reverse().map((l) => `<div class="${l.level}">[${esc(l.t)}] ${esc(publicText(l.msg))}</div>`).join(""));
}

// Round 18 workflow uses persisted project state; payment remains at its original confirmation.
function workflowState() {
  const replacements=P.replacements?.characters || [];
  const saved=!!P.replacements && !dirty.rep && (P.breakdown?.characters || []).every(c=>replacements.some(r=>r.source_id===c.id && ['keep','replace'].includes(r.action) && (r.action!=='replace' || r.asset_id)))
    && replacements.filter(c=>c.action==='add').every(c=>c.asset_id && c.name?.trim());
  const rows=[
    {label:'01 上传对标视频',step:'01',done:!!P.source,target:'#sourceDrop'},
    {label:'02 拆解对标视频',step:'02',done:!!P.breakdown && !!P.storyboard && !P.storyboard_status?.stale,target:P.breakdown?'#storyboardView':'#analyzeBtn'},
    {label:'03 选角和商品',step:'03',done:saved,target:'#replacementForm'},
    {label:'04 对照和修改',step:'04',done:finalPreviewStatus().ready,target:'#step-4'},
    {label:'05 成片',step:'05',done:!!P.final_preview && !P.final_preview_status?.stale,target:'#step-5'},
  ];
  let current=false;
  return rows.map(r=>({...r,state:r.done?'已完成':!current?(current=true,'当前'):'待办'}));
}
function renderWorkflow() {
  const rows=workflowState(), next=rows.find(r=>r.state==='当前');
  updateHtml($('#workflow'),`<nav class="workflow-progress">${rows.map((r,i)=>`<a href="#step-${Number(r.step)}" data-flow-index="${i}" title="${esc(({1: '自动读取视频信息', 2: '人物、剧情、产品位置', 3: '换成自己的人和货', 4: '对照新旧画面，按段修改和生成', 5: '直接拼接预览'})[Number(r.step)])}" class="flow-${r.state==='当前'?'current':r.done?'done':'todo'} ${r.paid?'flow-paid':''}"><b>${r.label}</b><span>${r.state}</span>${r.paid?`<small>${esc(r.cost)}</small>`:''}</a>`).join('')}</nav><button id="workflowNext" ${next?'':'disabled'}>${next?'下一步：'+next.label.replace(/^\d+ /,''):'全部完成'}</button>`);
  // On narrow screens the step strip scrolls sideways; keep the current step visible without moving the page.
  const strip=$('#workflow .workflow-progress'),current=strip?.querySelector('.flow-current');
  if(strip && current && strip.scrollWidth>strip.clientWidth)strip.scrollLeft=Math.max(0,current.offsetLeft-strip.offsetLeft-(strip.clientWidth-current.offsetWidth)/2);
}
function focusWorkflow(row) {
  if(row && Number(row.step)<=3){completedStepViews.set(`${P.id}:${Number(row.step)}`,true);renderCompletedSteps();}
  if (row?.todo) return focusTodo(row.todo);
  const target=row?.target && $(row.target);
  if (target) { target.scrollIntoView({behavior:'smooth',block:'center'});target.classList.add('fix-highlight');setTimeout(()=>target.classList.remove('fix-highlight'),3500); }
  else jumpToStep(row?.step || '03');
}
async function workflowNext() {
  if(scriptSaveState())return;
  const row=workflowState().find(r=>r.state==='当前');
  if (!row) return;
  if (row.paid || !row.action) return focusWorkflow(row);
  if (row.action==='prepare') {
    const pair=row.pairs.find(p=>!cameraImageFor(p.cid,p.sid) && !cameraRecords(p.cid,p.sid).some(r=>r.state==='pending_confirmation' && !cameraRecordStale(r)));
    if (pair) {if(pair.cid==='scene')await draftPlannedScene(pair.sid);else await draftSceneCamera(pair.cid,pair.sid,true);}
  } else if (row.action==='script') {
    if (S && dirty.script) {focusWorkflow(row);return;}
    await submitScript();
  } else if (row.action==='preview') {
    for (const task of S?.tasks || []) {
      const latest=(P.runs?.[task.id] || []).filter(r=>!['cancelled','superseded'].includes(r.state)).at(-1);
      if (['pending_confirmation','running','submitting','completed'].includes(latest?.state)) continue;
      const button=$(`[data-preview="${task.id}"]`);
      if (button && !await previewTask(task.id,button,`#workflowNext, [data-preview="${task.id}"]`))return;
    }
  } else if (row.action==='assemble') {if(assemblySaving.has(P.id))return;await api('POST',`projects/${P.id}/assemble`);await refresh();}
  renderSteps();
}
function updateImageWaits() {
  $$('[data-image-wait]').forEach(el=>{
    const r=P.images?.[el.dataset.imageWait], job=P.jobs?.[`img:${el.dataset.imageWait}`];
    const start=Number(r?.started_at || job?.started || 0);
    el.textContent=start ? `已等待 ${Math.max(0,Math.floor(Date.now()/1000-start))} 秒` : '正在等待结果';
  });
}
async function submitAnalyze() {
  const pid=P.id;operationErrors.delete(operationKey('analyze'));
  const file=$('#srtInput').files?.[0];
  const body={...requestRoute('analysis',$('#analyzeProvider')),skip_asr:$('#skipAsr').checked,send_video:!$('#sendVideo').disabled && $('#sendVideo').checked,srt_text:file ? await file.text() : null};
  try {await api('POST',`projects/${pid}/analyze`,body);if(P.id===pid){finishTemporaryProvider('analysis',body);toast('开始拆解');await refresh(true);}}
  catch(error){if(P.id===pid){operationFailure('analyze',error);renderAnalysis();}throw error;}
}
async function submitScript() {
  if(scriptSaveState())return;
  if(S && dirty.script && !await pageConfirm('重新生成会覆盖你对脚本的修改，继续？'))return;
  const pid=P.id,a=$('#rangeStart').value,b=$('#rangeEnd').value;operationErrors.delete(operationKey('script'));
  const route=requestRoute('script',$('#scriptProvider'));
  try {await api('POST',`projects/${pid}/script`,{...route,dialogue_mode:$('#dialogueMode').value || 'keep',instruction:$('#scriptInstruction').value || '',range:a!=='' && b!==''?[Number(a),Number(b)]:null});if(P.id===pid){finishTemporaryProvider('script',route);dirty.script=false;await refresh(true);}}
  catch(error){if(P.id===pid){operationFailure('script',error);renderScript();}throw error;}
}
function readDialogueProposal(tid,box) {
  $$('[data-dialogue-line]',box).forEach(updateDialogueProposalInput);
  const task=S.tasks.find(t=>t.id===tid);
  return task.dialogue_proposal ? {...clone(task.dialogue_proposal),lines:dialogueProposalLines(task)} : undefined;
}
async function requestDialogueProposal(tid,box) {
  const pid=P.id,key=operationKey('dialogue',tid),instruction=$('[data-dialogue-instruction]',box)?.value || '';
  if(dialogueRequests.has(key) || scriptSaveState())return;
  dialogueInstructions.set(key,instruction);
  const previous_proposal=readDialogueProposal(tid,box);
  const state={startedAt:Date.now()};
  dialogueRequests.set(key,state);dialogueNotices.delete(key);
  operationErrors.delete(key);dialogueFailureKinds.delete(key);
  $('[data-dialogue-error]',box)?.remove();$('[data-dialogue-notice]',box)?.remove();
  updateDialogueRequestUi();scheduleDialogueWait(key,state);
  try {
    if (dirty.script && !await saveScript(true,undefined,undefined,`[data-dialogue-propose="${tid}"]`))return;
    if(P.id!==pid)return;
    const route=requestRoute('dialogue_proposal',$('#scriptProvider'));
    const proposal=await api('POST',`projects/${pid}/script/dialogue-proposal`,{task_id:tid,instruction,previous_proposal,...route});
    if(P.id!==pid)return;
    finishTemporaryProvider('dialogue_proposal',route);
    const task=P.script.tasks.find(t=>t.id===tid);task.dialogue_proposal=proposal;
    dialogueProposalEdits.delete(key);
    const message='已生成改写方案，请核对后点“采用这版”';
    const notice={message};dialogueNotices.set(key,notice);toast(message);
    setTimeout(()=>{
      if(dialogueNotices.get(key)!==notice || dialogueRequests.has(key))return;
      dialogueNotices.delete(key);
      if(P?.id===pid)$(`#scriptView [data-dialogue-box="${tid}"] [data-dialogue-notice]`)?.remove();
    },4000);
    S=clone(P.script);seen.script='';renderScript();renderTasks(true);
  } catch(error) {if(P.id===pid){
    const kind=error.kind || (/额度不足|余额不足|quota|rate.?limit|HTTP 429/i.test(error.message) ? 'quota' : /key 无效|没有权限|unauthorized|HTTP 40[13]/i.test(error.message) ? 'auth' : /网络|连接|超时|timeout|failed to fetch|network|HTTP 50[0234]/i.test(error.message) ? 'transient' : 'unknown');
    dialogueFailureKinds.set(key,kind);operationFailure('dialogue',error,tid);
    // Preserve unsaved script fields; only refresh step 04's proposal feedback.
    const section=$(`[data-dialogue-section="${tid}"]`);
    const task=S.tasks.find(t=>t.id===tid);
    if(section)updateHtml(section,dialogueProposalHtml(task,P.replacement_checks?.[tid]));
  }throw error;} finally {
    clearTimeout(state.timer);dialogueRequests.delete(key);
    if(P?.id===pid)updateDialogueRequestUi();
  }
}
async function adoptDialogueProposal(tid,box) {
  if(dialogueRequests.has(operationKey('dialogue',tid)) || scriptSaveState())return;
  const lines=readDialogueProposal(tid,box).lines,problem=dialogueAdoptionProblem(lines);
  if(problem){toast('暂不能采用：'+problem,true);return;}
  // Keep source metadata; the server still validates each sentence and its attribution.
  const submitted=S.tasks.find(t=>t.id===tid).dialogue_proposal.lines.map((line,i)=>({...clone(line),proposed:lines[i].proposed}));
  const pid=P.id;
  return withScriptSave(`[data-dialogue-adopt="${tid}"]`,async state=>{
    if (dirty.script) await saveScript(true,undefined,state);
    if(P?.id!==pid)return;
    const np=await api('POST',`projects/${pid}/script/dialogue-adopt`,{task_id:tid,lines:submitted});
    if(P?.id!==pid)return;
    P={...P,...np};S=clone(P.script);dirty.script=false;seen.script='';renderScript();renderTasks(true);renderSteps();scheduleReplacementCheck();
    toast('已采用改写后的台词');
  },latest=>{
    const task=latest.script?.tasks?.find(t=>t.id===tid);
    return task?.dialogue_choice==='adopt' && task.dialogue===submitted.map(l=>l.proposed).join('\n');
  },{script:['dialogue_mode'],[tid]:['dialogue','dialogue_lines']});
}
async function keepOriginalDialogue(tid) {
  if(dialogueRequests.has(operationKey('dialogue',tid)) || scriptSaveState())return;
  const pid=P.id,task=S.tasks.find(t=>t.id===tid);
  const original=task.dialogue_proposal?.lines?.map(l=>l.original).join('\n') ?? dialogueLines(task).map(l=>l.text).join('\n');
  return withScriptSave(`[data-dialogue-keep="${tid}"]`,async state=>{
    if(dirty.script)await saveScript(true,undefined,state);
    if(P?.id!==pid)return;
    const np=await api('POST',`projects/${pid}/script/dialogue-keep`,{task_id:tid});
    if(P?.id!==pid)return;
    P={...P,...np};S=clone(P.script);dirty.script=false;seen.script='';renderAll();scheduleReplacementCheck();
  },latest=>{
    const current=latest.script?.tasks?.find(t=>t.id===tid),text=s=>String(s || '').replace(/\s+/g,' ').trim();
    return current?.dialogue_choice==='keep' && text(current.dialogue)===text(original);
  },{[tid]:['dialogue','dialogue_lines']});
}
async function deleteAsset(assetId) {
  if(scriptSaveState())return;
  const pid=P.id,a=P.assets.find(a=>a.id===assetId);
  if(!await pageConfirm(a?.camera_character || a?.roles?.includes('target_state') ? "删除后，用到它的分段需要换图或重新生成脚本；要再用这张图可以点‘恢复’，不会重新扣费" : "删除这个素材？"))return;
  return withScriptSave(`[data-delasset="${assetId}"]`,async()=>{
    const np=await api('DELETE',`projects/${pid}/assets/${assetId}`);
    if(P?.id!==pid)return;
    P={...P,...np};seen.assets='';seen.script='';renderAll();scheduleReplacementCheck(true);
  },latest=>!latest.assets?.some(a=>a.id===assetId));
}
async function resumeImage(iid) {
  const pid=P.id,aid=P.images?.[iid]?.asset_id || iid;
  return withScriptSave(`[data-imgresume="${iid}"]`,async()=>{
    await api('POST',`projects/${pid}/images/${iid}/resume`);
    if(P?.id===pid)await refresh();
  },latest=>latest.assets?.some(a=>a.id===aid) && !latest.images?.[iid]?.restore_error);
}

// ---------- settings ----------
const SETTINGS_CHANNELS = ['openai','jimmy','deepseek','doubao','custom'];
const SETTINGS_KEY_NAMES = {openai:'官方 OpenAI',jimmy:'三方渠道',deepseek:'DeepSeek',doubao:'豆包',custom:'自定义渠道'};
let settingsDraft = null;
const settingsKeyDrafts = new Map(), settingsKeyEditors = new Set();
function keyChannel(channel) {channel=channel.split(':')[0];return {official:'openai',third_party:'jimmy'}[channel] || channel;}
function keyField(channel) {return channel.toUpperCase()+'_API_KEY';}
function settingsKeyFilled(s,channel) {
  const field=keyField(channel);
  return field in s ? !!s[field] : channel==='jimmy' && s._jimmy_configured!==undefined ? s._jimmy_configured : !!s._providers?.[channel]?.ready;
}
function settingsOptions(s,kind) {
  if(kind==='image') {
    const options=[['official:gpt-image-2|auto','官方 · gpt-image-2 · 自动画质（一张约 $0.05）'],['official:gpt-image-2|high','官方 · gpt-image-2 · 高画质（一张约 $0.20，细节更好，更慢）'],['official:gpt-image-2.6-sunburst|high','官方 · gpt-image-2.6 sunburst · 高画质（一张约 $0.08）'],['third_party:gpt-image-2|auto','三方渠道 · gpt-image-2']];
    const custom=(route,quality)=>{const value=route+'|'+quality;if(!options.some(([v])=>v===value)){const [channel,...model]=route.split(':');options.push([value,`${channel==='official'?'官方':'三方渠道'} · ${imageModelLabel(model.join(':'))} · ${quality==='high'?'高画质':'自动画质'}`]);}};
    if(s.OPENAI_IMAGE_MODEL && !['gpt-image-2','gpt-image-2.6-sunburst'].includes(s.OPENAI_IMAGE_MODEL))custom('official:'+s.OPENAI_IMAGE_MODEL,'auto');
    for(const step of ['storyboard_image','camera_image']){const {route,quality}=settingsImageChoice(s,step);custom(route,quality);}
    return options;
  }
  if(kind==='video')return [['third_party','三方渠道 · Seedance 2.0 mini']];
  return SETTINGS_CHANNELS.map(channel=>{
    const provider=s._providers?.[channel],model=s[channel==='jimmy' ? 'JIMMY_LLM_MODEL' : channel.toUpperCase()+'_MODEL'];
    let label=providerLabel(channel,provider || {label:SETTINGS_KEY_NAMES[channel]});
    if(model && model!==provider?.model) {
      const old=provider?.model==='gpt-5.6-sol' ? 'GPT-5.6 Sol' : provider?.model;
      if(old && label.endsWith(' · '+old))label=label.slice(0,-(old.length+3));
      label+=' · '+(model==='gpt-5.6-sol' ? 'GPT-5.6 Sol' : model);
    }
    return [channel,label];
  });
}
function settingsImageChoice(s,step) {
  let route=s['ROUTE_'+step.toUpperCase()] || 'official';
  if(!route.includes(':'))route+=':'+ (route==='official'?s.OPENAI_IMAGE_MODEL || 'gpt-image-2':'gpt-image-2');
  const quality=s[step.toUpperCase()+'_QUALITY']==='high'?'high':'auto';
  return {route,quality,value:route+'|'+quality};
}
function changeSettingsImageRoute(select) {
  const step=select.dataset.imageRoute,[route,quality]=select.value.split('|'),key=step.toUpperCase(),form=$('#settingsForm');
  for(const [name,value] of [['ROUTE_'+key,route],[key+'_QUALITY',quality]]){$(`[name="${name}"]`,form).value=value;settingsDraft[name]=value;}
}
const SETTINGS_MODEL_CHANNELS = {OPENAI_MODEL:'openai',JIMMY_LLM_MODEL:'jimmy',DEEPSEEK_MODEL:'deepseek',DOUBAO_MODEL:'doubao',CUSTOM_MODEL:'custom',OPENAI_IMAGE_MODEL:'official'};
function changeSettingsModel(input) {
  const key=input.getAttribute('name'),channel=SETTINGS_MODEL_CHANNELS[key],old=settingsDraft[key];
  if(!channel || old===input.value)return;
  settingsDraft[key]=input.value;
  // Preserve independent per-step models, while defaults track the channel's edited model.
  for(const select of $$('[name]', $('#settingsStepRoutes'))) {
    if(select.value===channel+':'+old)select.value=key==='OPENAI_IMAGE_MODEL'?channel+':'+(input.value || 'gpt-image-2'):channel;
    settingsDraft[select.getAttribute('name')]=select.value;
  }
  $('#settingsStepRoutes').innerHTML=settingsRoutes(settingsDraft);
  if(key==='OPENAI_IMAGE_MODEL'){
    const selected=settingsImageChoice(settingsDraft,'storyboard_image').value;
    $('[data-model-card="image"]').innerHTML=settingsOptions(settingsDraft,'image').map(([value,label])=>`<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
  }
  renderSettingsKeys();
}
function settingsRoutes(s) {
  const steps={segment_plan:'分段方案',director:'提示词',storyboard_plan:'改图方案',storyboard_image:'新分镜生图',cast:'选角识别与剧情检查',analysis:'拆解',speech_lines:'台词断句',script:'生成新脚本',check:'动作对比与安全检查',dialogue_proposal:'台词改写方案',camera_prompt:'主镜头图提示词',camera_image:'主镜头图生图',video:'分段视频生成'};
  return '<h3>单独给某一步换模型</h3><p class="hint">不设置时，各步骤跟随上面三张卡片</p>'+Object.entries(steps).map(([step,title])=>{
    const key='ROUTE_'+step.toUpperCase(),follow={segment_plan:'script',director:'script',speech_lines:'analysis',storyboard_plan:'analysis',cast:'analysis',check:'script',dialogue_proposal:'script',camera_prompt:'analysis'}[step];
    const options=settingsOptions(s,['camera_image','storyboard_image'].includes(step) ? 'image' : step==='video' ? 'video' : 'reason');
    if(follow)options.unshift(['follow:'+follow,'跟随'+steps[follow]]);
    const image=['camera_image','storyboard_image'].includes(step),choice=image?settingsImageChoice(s,step):null;
    const selected=image?choice.value:s[key] || (follow ? 'follow:'+follow : options[0]?.[0]);
    if(selected && !options.some(([v])=>v===selected))options.push([selected,publicText(selected)]);
    return `<div class="field"><label for="setting-${key}">${title}</label><select id="setting-${key}" ${image?`data-image-route="${step}"`:`name="${key}"`}>${options.map(([v,l])=>`<option value="${esc(v)}" ${v===selected ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>${image?`<input type="hidden" name="${key}" value="${esc(choice.route)}"><input type="hidden" name="${step.toUpperCase()}_QUALITY" value="${esc(choice.quality)}">`:''}</div>`;
  }).join('');
}
function connectionButton(channel) {return `<p><button type="button" class="small ghost" data-test-channel="${channel}">测试连接</button> <span class="hint">会产生极少量费用（使用已保存的配置）</span> <span data-connection-result="${channel}" role="status"></span></p>`;}
async function testChannel(channel,button) {
  const result=$(`[data-connection-result="${channel}"]`);button.disabled=true;result.textContent='测试中…';
  try {const r=await api('POST','settings/test-connection',{channel});result.textContent=`${r.status || '网络失败'} · ${r.elapsed_ms ?? 0} ms`;}
  catch(error){result.textContent=publicText(error.message);}
  finally {button.disabled=false;}
}
function settingsKeyHtml(channel) {
  const filled=settingsKeyFilled(settingsDraft,channel),editing=settingsKeyEditors.has(channel),name=SETTINGS_KEY_NAMES[channel];
  return `<div class="settings-channel-key" data-channel-key="${channel}">${filled ? `<p class="key-ready">✓ ${name} Key 已填 <button type="button" class="small ghost" data-change-key="${channel}">更换</button></p>${connectionButton(channel)}` : ''}
    ${!filled || editing ? `<label class="field" for="setting-${keyField(channel)}"><span>${name} Key</span><input id="setting-${keyField(channel)}" name="${keyField(channel)}" type="password" autocomplete="new-password" value="${esc(settingsKeyDrafts.get(channel) || '')}" placeholder="${filled ? '粘贴新 Key（留空保持不变）' : '粘贴 '+name+' Key'}"></label><p class="hint">${filled ? '留空保持已保存的 Key 不变。' : channel==='openai' ? '推理和生图共用这个 Key，填一次后保存即可。' : channel==='jimmy' ? '推理、生图和视频共用这个 Key，填一次后保存即可。' : '填入这个渠道的 Key 后保存即可。'}</p>` : ''}</div>`;
}
function renderSettingsKeys() {
  for(const channel of SETTINGS_CHANNELS){const input=$(`[name="${keyField(channel)}"]`);if(input)settingsKeyDrafts.set(channel,input.value);}
  const used=new Set();
  for(const kind of ['reason','image','video']) {
    const channel=keyChannel($(`[data-model-card="${kind}"]`).value),box=$(`[data-card-key="${kind}"]`);
    box.innerHTML=used.has(channel) ? `<p class="hint">✓ 和上面用的是同一个 ${SETTINGS_KEY_NAMES[channel]} Key，不用再填</p>` : settingsKeyHtml(channel);
    used.add(channel);
  }
  $('#settingsOtherKeys').innerHTML=SETTINGS_CHANNELS.filter(channel=>!used.has(channel)).map(settingsKeyHtml).join('');
  for(const select of $$('[data-model-card]')) {
    const selected=select.value;
    select.innerHTML=settingsOptions(settingsDraft,select.dataset.modelCard).map(([channel,label])=>`<option value="${esc(channel)}" ${channel===selected ? 'selected' : ''}>${esc(label)}${settingsKeyFilled(settingsDraft,keyChannel(channel)) ? '' : '（未填 Key）'}</option>`).join('');
    select.value=selected;
  }
}
function changeSettingsCard(select) {
  const channel=select.value,kind=select.dataset.modelCard,form=$('#settingsForm');
  const set=(key,value)=>{const field=$(`[name="${key}"]`,form);field.value=value;};
  if(kind==='reason') {
    set('ROUTE_ANALYSIS',channel);set('ROUTE_SCRIPT',channel);set('DEFAULT_LLM',channel);
    set('ROUTE_CAST','follow:analysis');set('ROUTE_CHECK','follow:script');set('ROUTE_DIALOGUE_PROPOSAL','follow:script');set('ROUTE_CAMERA_PROMPT','follow:analysis');
  } else if(kind==='image') {
    for(const step of ['storyboard_image','camera_image']){const field=$(`[data-image-route="${step}"]`,form);field.value=channel;changeSettingsImageRoute(field);}
  } else set('ROUTE_VIDEO',channel);
  renderSettingsKeys();
}
function localModelsHtml(models) {
  return models.map(model=>`<div class="local-model-row" data-local-model-state="${esc(model.status)}"><p><span class="model-light ${model.status==='ok' ? 'ok' : 'bad'}" aria-label="${model.status==='ok' ? '已安装' : '不可用'}">●</span> ${esc(model.name)}　${esc(model.status==='ok' ? '已安装' : model.status==='missing' ? '缺失' : '不可用：'+(model.detail || '模型文件不完整或损坏'))}</p>
    ${model.path || model.model || model.download_url ? `<details><summary>查看详情</summary><p class="hint mono">${model.model ? '模型：'+esc(model.model)+'<br>' : ''}${model.path ? '路径：'+esc(model.path)+'<br>' : ''}${model.download_url ? '下载来源：'+esc(model.download_url) : ''}</p></details>` : ''}</div>`).join('');
}
async function checkLocalModels(button) {
  const box=$('#localModelsInfo');
  if(!box.hidden){box.hidden=true;button.setAttribute('aria-expanded','false');return;}
  box.hidden=false;button.disabled=true;button.textContent='检查中…';button.setAttribute('aria-expanded','true');box.textContent='检查中…';
  try {const result=await api('GET','settings/local-models');box.innerHTML=localModelsHtml(result.models || []);}
  catch(error){box.textContent='不可用：'+publicText(error.message);}
  finally {button.disabled=false;button.textContent='检查本地模型';}
}
async function openSettings() {
  const s = await api('GET','settings');settingsDraft=s;settingsKeyDrafts.clear();settingsKeyEditors.clear();
  const f=(key,label)=>`<div class="field"><label for="setting-${key}">${label}</label><input id="setting-${key}" name="${key}" type="text" value="${esc(s[key] || '')}"></div>`;
  const rt=STATUS?.runtime || {};
  const card=(kind,title,hint,route)=>{
    const selected=kind==='image'?settingsImageChoice(s,'storyboard_image').value:String(s[route] || (kind==='reason'?'openai':'third_party')).split(':')[0];
    return `<section class="settings-model-card"><h3>${title}</h3><p class="hint">${hint}</p><label class="field" for="model-card-${kind}"><span>用哪个</span><select id="model-card-${kind}" data-model-card="${kind}">${settingsOptions(s,kind).map(([value,label])=>`<option value="${esc(value)}" ${value===selected ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>${kind==='video' ? '<p class="hint">以后接入即梦官方、火山等渠道会出现在这里</p>' : ''}<div data-card-key="${kind}"></div></section>`;
  };
  $('#settingsFields').innerHTML=`${card('reason','推理模型','拆解、写脚本、改写台词、安全检查都用它','ROUTE_ANALYSIS')}
    ${card('image','生图模型','生成新分镜图，包括按格改图和批量裂变。费用按实测估算，参考图越多越贵。','ROUTE_STORYBOARD_IMAGE')}${card('video','生视频模型','生成分段视频','ROUTE_VIDEO')}
    <details id="settingsAdvanced"><summary>高级设置（一般不用动）</summary>
      <div id="settingsStepRoutes">${settingsRoutes(s)}</div>
      <h3>各渠道地址与模型</h3>
      ${f('OPENAI_BASE_URL','官方 Base URL')}${f('OPENAI_MODEL','官方推理模型 ID')}${f('OPENAI_IMAGE_MODEL','官方生图模型 ID')}${f('OPENAI_REASONING_EFFORT','推理强度（留空不发送）')}
      ${f('JIMMY_LLM_BASE_URL','三方渠道 Base URL')}${f('JIMMY_LLM_MODEL','三方渠道理解模型')}
      ${f('DEEPSEEK_BASE_URL','DeepSeek Base URL')}${f('DEEPSEEK_MODEL','DeepSeek 模型 ID')}${f('DOUBAO_BASE_URL','豆包 Base URL')}${f('DOUBAO_MODEL','豆包模型 ID')}
      ${f('CUSTOM_BASE_URL','自定义 Base URL')}${f('CUSTOM_MODEL','自定义模型 ID')}
      <input type="hidden" name="DEFAULT_LLM" value="${esc(s.DEFAULT_LLM || 'jimmy')}">
      <h3>其他渠道 Key（可选）</h3><div id="settingsOtherKeys"></div>
      <h3>本地拆解</h3>${f('ASR_MODEL','Whisper 模型')}${f('ASR_COMPUTE_TYPE','计算精度')}${f('ASR_LANGUAGE','语言（留空 = 自动识别（推荐））')}
      ${f('ASR_ENGINE','转录引擎（auto / whisperx / faster-whisper）')}${f('HF_ENDPOINT','模型下载镜像（国内可填 https://hf-mirror.com）')}${f('MAX_PASSAGES','最多细读段落数')}${f('LLM_PARALLEL','细读并发数')}
    </details>
    <div class="settings-utilities"><button type="button" class="ghost" data-installation-toggle aria-controls="installationInfo" aria-expanded="false">查看安装位置</button>
    <div id="installationInfo" hidden><p class="hint">删除整个工作台文件夹即可完全卸载</p><p class="hint mono">工作台：${esc(rt.root)}<br>配置与 Key：${esc(rt.config)}<br>依赖：${esc(rt.runtime)}（${esc(rt.runtime_mb)} MB${rt.models?.length ? '，模型：'+esc(rt.models.join('、')) : '，还没有下载模型'}）<br>项目：${esc(rt.projects)}<br>FFmpeg：${esc(rt.ffmpeg || '未找到')}${rt.ffmpeg && !rt.ffmpeg_local ? '（系统级，卸载工作台时不会一起删除）' : ''}</p></div>
    <button type="button" class="ghost" data-local-models aria-controls="localModelsInfo" aria-expanded="false">检查本地模型</button><div id="localModelsInfo" role="status" hidden></div></div>`;
  renderSettingsKeys();$('#settingsDialog').showModal();
}
async function saveSettings(e) {
  e.preventDefault();
  for(const input of $$('input[name]', $('#settingsForm')))if(SETTINGS_MODEL_CHANNELS[input.getAttribute('name')])changeSettingsModel(input);
  const body=Object.fromEntries(new FormData($('#settingsForm')).entries());
  await guard(()=>api('POST','settings',body),'设置已保存');$('#settingsDialog').close();
  STATUS=await api('GET','status');renderStatus();
  if(P) {
    // Route changes should immediately update old-project warnings and effective labels.
    delete P.resolved_routes;
    renderProviderSelect($('#analyzeProvider'),'analysis');renderProviderSelect($('#scriptProvider'),'script');
    renderReplacements();
  }
  updateSendVideo();updateBuildBanner();updateDialogueModelLabels();
}

// ---------- events ----------
function bindDrop(label, input, handler) {
  input.addEventListener("change", () => { if (input.files.length) handler([...input.files]); input.value = ""; });
  label.addEventListener("dragover", (e) => { e.preventDefault(); label.classList.add("over"); });
  label.addEventListener("dragleave", () => label.classList.remove("over"));
  label.addEventListener("drop", (e) => { e.preventDefault(); label.classList.remove("over"); handler([...e.dataTransfer.files]); });
}

document.addEventListener("click", async (e) => {
  if(storyboardLightboxClick(e))return;
  const settingsAction=e.target.closest('[data-change-key], [data-installation-toggle], [data-local-models], [data-clear-route]');
  if(settingsAction && ['data-change-key','data-installation-toggle','data-local-models','data-clear-route'].some(attr=>settingsAction.hasAttribute?.(attr))) {
    e.preventDefault();
    if(settingsAction.hasAttribute('data-change-key')) {settingsKeyEditors.add(settingsAction.dataset.changeKey);renderSettingsKeys();return;}
    if(settingsAction.hasAttribute('data-installation-toggle')) {
      const info=$('#installationInfo');info.hidden=!info.hidden;settingsAction.setAttribute('aria-expanded',String(!info.hidden));return;
    }
    if(settingsAction.hasAttribute('data-local-models'))return checkLocalModels(settingsAction);
    const pid=P.id,step=settingsAction.dataset.clearRoute;
    const updated=await guard(()=>api('DELETE',`projects/${pid}/routes/${step}`),'已恢复跟随设置');
    if(P?.id!==pid)return;
    P.routes=updated.routes || {};P.resolved_routes=updated.resolved_routes;
    if(step==='camera_image')for(const key of cameraChannels.keys())if(key.startsWith(pid+':')){cameraChannels.delete(key);cameraRouteSelected.delete(key);}
    for(const [key,id] of [['analysis','analyzeProvider'],['script','scriptProvider']])if(key===step){$('#'+id).dataset.userSelected='';$('#'+id).value='';}
    renderProviderSelect($('#analyzeProvider'),'analysis');renderProviderSelect($('#scriptProvider'),'script');renderReplacements();updateDialogueModelLabels();return;
  }
  if (e.target.id === 'motionCheckModal') { closeMotionCheck(); return; }
  if(P?.segments_plan?.segments?.length && !comparisonSuppressClick && comparisonSelection().cell && !e.target.closest?.('button,a,input,textarea,select,summary,label,.comparison-preview-cell,.edit-cell-detail,.inline-confirm,.material-dialog,#fissionPanel,#libraryPanel,#batchBoard'))clearComparisonCells();
  const el = e.target.closest("button, a, .chip, #cameraLightbox, [data-story-row]");
  if (!el || el.disabled || el.hasAttribute?.('disabled')) return;
  const d = el.dataset;
  if(d.batchBoard)return openBatchBoard(d.batchBoard);
  if('batchRefresh' in d)return refreshBatchBoard();
  if('batchNew' in d)return batchNewBatch();
  if(d.batchPrepare)return batchBoardAction(d.batchPrepare,'prepare');
  if(d.batchConfirm)return batchBoardAction(d.batchConfirm,'confirm',d.token);
  if(d.batchCancel){if(batchBoardState?.data?.pending?.[d.batchCancel])batchBoardState.hidden[d.batchCancel]=batchBoardState.data.pending[d.batchCancel].token;renderBatchBoard();return;}
  if(d.batchShow){delete batchBoardState.hidden[d.batchShow];renderBatchBoard();return;}
  if(d.batchOpenProject)return batchOpenProject(d.batchOpenProject,d.segment);
  if('batchRetry' in d)return batchRetry(d.project,Number(d.batchRetry));
  if('batchZoom' in d)return batchZoom(d.project,d.batchZoom,el);
  if('fissionOpen' in d)return openFission();
  if('fissionClose' in d)return closeFission();
  if('fissionNewBatch' in d)return fissionNewBatch();
  if(d.openMaster)return openProject(d.openMaster);
  if('copyError' in d){const text=$('pre',el.closest('.error-details'))?.textContent || '';try{await navigator.clipboard.writeText(text);el.textContent='已复制';}catch{toast('复制没有完成，请展开详细信息手动复制。',true);}return;}
  if(d.fissionDelete)return fissionDeleteRow(d.fissionDelete);
  if(d.fissionRemove)return fissionRemovePhoto(d.key,d.fissionRemove);
  if(d.fissionPicker)return fissionOpenPicker(d.fissionPicker);
  if('fissionPickerClose' in d)return closeFissionPicker();
  if('fissionPickerDone' in d)return fissionPickerDone();
  if(d.fissionItem)return fissionToggleItem(d.fissionItem);
  if(d.fissionCheck)return fissionQuestions(d.fissionCheck);
  if(d.fissionAnswer)return fissionAnswer(d.item,d.question,d.fissionAnswer);
  if('fissionCreate' in d)return createFission();
  if('fissionSave' in d)return fissionChange(()=>{});
  if('fissionReload' in d){fissionState.unsaved=false;return openFission();}
  if('fissionRefresh' in d)return fissionPoll();
  if(d.fissionRetry)return retryFission(d.fissionRetry);
  if(d.fissionProject){closeFission();await openProject(d.fissionProject);$('#step-4')?.scrollIntoView?.({block:'start'});return;}
  if(d.librarySave)return saveCastingToLibrary(d.librarySave);
  if('libraryOpen' in d)return openLibrary();
  if('libraryProject' in d)return openLibrary('project');
  if('libraryClose' in d)return closeLibrary();
  if(d.libraryTab){if(!libraryState.busy){libraryState.tab=d.libraryTab;renderLibrary();}return;}
  if(d.libraryRename)return libraryStartRename(d.libraryRename,d.libraryScope);
  if(d.libraryDelete)return libraryDelete(d.libraryDelete);
  if(d.librarySelect){const s=libraryState;if(!s.busy){s.selected=s.selected.includes(d.librarySelect)?s.selected.filter(i=>i!==d.librarySelect):[...s.selected,d.librarySelect];renderLibrary();}return;}
  if('libraryImport' in d)return libraryImport();
  if(d.pageConfirm)return finishPageConfirmation(d.pageConfirm==='accept');
  if('castingSaveFocus' in d){completedStepViews.set(`${P.id}:3`,true);renderCompletedSteps();const save=$('#saveReplacements');save?.scrollIntoView({behavior:'smooth',block:'center'});save?.focus({preventScroll:true});return;}
  if(d.stepToggle){const key=`${P.id}:${d.stepToggle}`;completedStepViews.set(key,!completedStepViews.get(key));renderCompletedSteps();return;}
  if(d.compareSegmentTab){if(comparisonSelectionLocked())return;setComparisonCells(d.compareSegmentTab,[]);comparisonSelection().hint='';renderSteps();return;}
  if('compareCancelCell' in d)return clearComparisonCells();
  if(d.compareView){v5View(comparisonSelection().sid).view=d.compareView;renderComparison();return;}
  if(d.compareVersion){const view=v5View(d.segment);view.rev=d.compareVersion;view.view='video';renderComparison();return;}
  if('comparisonSync' in d)return toggleComparisonPlayback();
  if(d.segmentAdjust){const key=editSheetKey(d.segmentAdjust);segmentAdjustments.set(key,!segmentAdjustments.get(key));renderSegmentFoot();return;}
  if(d.cellPlan){const details=$(`[data-detail-key="cell-plan:${d.cellPlan}:${comparisonSelection().cell}"]`);if(details)details.open=true;return;}
  if(d.v5Confirm)return confirmSegmentAction(d.v5Confirm);
  if(d.v5Cancel){
    const key=editSheetKey(d.v5Cancel),state=v5Actions.get(key);
    if(state?.status==='confirming')rememberCancelledSegmentPreview(d.v5Cancel,state.kind,state.rec,state.cell);
    v5Actions.delete(key);renderSteps();return;
  }
  if(d.compareCell){if(comparisonSuppressClick){comparisonSuppressClick=false;return;}return selectStoryboardComparisonCell(el,e);}
  if('compareTouchAdd' in d){if(comparisonSelectionLocked())return;comparisonSelection().touchAdd=!comparisonSelection().touchAdd;renderComparison();return;}
  if(d.segmentModeCancelPending)return dismissSegmentModePending(d.segmentModeCancelPending);
  if(d.segmentModeChoice){
    const segment=P.segments_plan?.segments?.find(s=>s.id===d.segment);
    if(!segment || segment.mode===d.segmentModeChoice)return;
    return segmentPlanAction('save',d.segment,{mode:d.segmentModeChoice});
  }
  if(d.comparisonBatch)return comparisonBatch(d.comparisonBatch);
  if('directorGenerate' in d)return directorAction('generate');
  if('directorSave' in d)return directorAction('save');
  if('directorRetry' in d){const last=directorErrors.get(P.id);if(last)return last.videoAction?directorVideoAction(last.videoAction,last.segment_id,last.extra):directorAction(last.action,last.extra);return;}
  if('segmentReplan' in d){
    if(!P.segments_plan || segmentPlanBusy.has(P.id) || comparisonBatches.get(P.id)?.busy || v5PreparingOrConfirming(P.id))return;
    segmentPlanConfirmation={pid:P.id,plan:JSON.stringify(P.segments_plan)};renderSegmentPlan();return;
  }
  if('segmentCancel' in d){segmentPlanConfirmation=null;renderSegmentPlan();return;}
  if('segmentGenerate' in d){
    if('segmentConfirm' in d && (d.segmentConfirm!==P.id || !segmentPlanConfirmationCurrent()))return;
    return segmentPlanAction('generate');
  }
  if('finalPreview' in d)return generateFinalPreview();
  if(d.directorZoom){e.preventDefault();return openCameraLightbox(d.directorZoom,d.caption || '新分镜');}
  if(d.directorRewrite)return directorAction('rewrite',{segment_id:d.directorRewrite});
  if(d.directorPreview)return prepareSegmentAction(d.directorPreview,'video');
  if(d.directorConfirm)return directorVideoAction('confirm',d.directorConfirm,{rev:d.rev,job_sha256:d.sha});
  if(d.directorUse)return directorAction('select-run',{segment_id:d.directorUse,rev:d.rev});
  if(d.reviewSeek!==undefined)return seekReview(d.sid,d.rev,d.reviewSeek);
  if(d.fixPlan)return makeFixPlan(d.fixPlan,d.rev);
  if(d.fixApply)return applyFixPlan(d.fixApply,d.rev);
  if(d.fixConfirm)return applyFixPlan(d.fixConfirm,d.rev,true);
  if(d.fixCancel)return cancelFixPlan(d.fixCancel,d.rev);
  if(d.fixVideo)return prepareFixVideo(d.fixVideo,d.rev);
  if(d.reviewAccept)return acceptReview(d.reviewAccept,d.rev,d.accepted==='true');
  if(d.reviewMic)return toggleReviewMic(d.reviewMic,d.rev);
  if('finalCheck' in d)return checkFinal();
  if(d.finalAccept!==undefined)return checkFinal(d.finalAccept==='true');
  if(d.finalSeek!==undefined){const video=$('#finalPreviewView video');if(video){video.currentTime=Number(d.finalSeek);video.pause?.();}return;}
  if(d.finalIssueSegment)return handleFinalIssue(d.finalIssueSegment,Number(d.issue));
  if(d.directorReview)return reviewDirectorRun(d.directorReview,d.rev);

  if(d.directorResume)return directorVideoAction('resume',d.directorResume,{rev:d.rev});
  if(d.directorRecover){const remote=prompt('填写这次生成的远端任务 ID');if(remote?.trim())return directorVideoAction('recover',d.directorRecover,{rev:d.rev,remote_task_id:remote.trim()});return;}
  if(d.storyAction)return editStoryboard(d.storyAction);
  if(d.storyMerge)return editStoryboard('merge',d.storyMerge);
  if(e.target.closest?.('[data-story-detail]')?.hasAttribute?.('data-story-detail'))return;
  if(d.editMic)return toggleStoryboardMic(d.editMic,d.editMicCell || null);
  if(d.editPlan)return performStoryboardEdit(d.editPlan,'plan');
  if(d.editSave)return performStoryboardEdit(d.editSave,'save');
  if(d.editPreview)return prepareSegmentAction(d.editPreview,'board');
  if(d.editCellPreview)return prepareSegmentAction(d.editCellPreview,'cell');
  if(d.editConfirm)return performStoryboardEdit(d.editConfirm,'confirm',{image_id:d.imageId});
  if(d.editReslice)return performStoryboardEdit(d.sheet,'reslice',{version_id:d.editReslice});
  if(d.editThirdRetry){const sid=d.editThirdRetry,rec=Object.values(P.images || {}).findLast(r=>r.storyboard_context?.sheet_id===sid && r.state==='failed'),ctx=rec?.storyboard_context || {},ids=ctx.selected_cells || (ctx.cell_id?[ctx.cell_id]:[]);if(ids.length){setComparisonCells(sid,ids);newStoryboardCellRequests.set(editSheetKey(sid),ctx.cells_revision?.revised_by_request || ctx.cell_revision?.revised_by_request || newStoryboardCellRequests.get(editSheetKey(sid)) || '');}return prepareSegmentAction(sid,ids.length?'cell':'board',0,null,'third_party');}
  if(d.editUse)return performStoryboardEdit(d.sheet,'use',{version:d.editUse});
  if(d.editResume)return guard(async()=>{await api('POST',`projects/${P.id}/images/${d.editResume}/resume`,{});await refresh();});
  if(d.editResultCell)return selectComparisonCell(d.editResultCell,'target');
  if(d.storyCell || d.storyRow){const cell=P.storyboard?.cells.find(c=>c.id===(d.storyCell || d.storyRow));if(cell)openStoryboardLightbox(storyboardPreviewCell(cell.id,'source'),el);return;}
  if('castAdd' in d)return addCastPerson();
  if(d.castRemove){const rep=captureCasting();rep.characters=rep.characters.filter(c=>c.source_id!==d.castRemove);renderReplacements(true);return;}
  if(d.castIdentify)return requestCasting(d.castIdentify);
  if(d.castApply)return applyCastSuggestion(d.castApply,d.mode);
  if('storyCheck' in d)return requestCasting('product',true);
  if('productNoteLink' in d){e.preventDefault();const note=$('[data-cast-note="product"]');if(note)note.open=true;const input=$('[data-cast-desc="product"]');input?.focus({preventScroll:true});input?.scrollIntoView({block:'center',behavior:'smooth'});return;}
  if(d.sceneMerge)return guard(()=>editPlannedScenes('merge',d.sceneMerge,d.nextScene));
  if(d.sceneSplit)return guard(()=>editPlannedScenes('split',d.sceneSplit));
  if(d.sceneDrop)return guard(()=>editPlannedScenes('drop',d.sceneDrop));
  if(d.sceneRestore)return guard(()=>editPlannedScenes('restore',d.sceneRestore));
  if(d.sceneFrameAuto)return guard(()=>captureSceneFrame(d.sceneFrameAuto));
  if(d.sceneFrameOpen){sceneFrameOpen.add(cameraDraftKey('scene',d.sceneFrameOpen));const details=$('[data-detail-key="scene-frames-'+d.sceneFrameOpen+'"]');if(details)details.open=true;return;}
  if('sceneFrameAt' in d)return guard(()=>captureSceneFrame(d.scene,Number(d.sceneFrameAt)));
  if(d.sceneDraft)return guard(()=>draftPlannedScene(d.sceneDraft));
  if(d.sceneRepreview)return guard(()=>previewPlannedScene(d.sceneRepreview));
  if(d.sceneUse)return guard(()=>usePlannedSceneCamera(d.scene,d.sceneUse));
  if(d.sceneConfirm){const sid=d.sceneConfirm,rec=P.images?.[d.imageId];if(sceneConfirmReason(sid,rec))return;if(!await pageConfirm(`确认生图（${imageFee(rec)}）？`))return;const pid=P.id;cameraBusy.add(cameraDraftKey('scene',sid));renderReplacements(true);try{await guard(()=>api('POST',`projects/${pid}/images/${rec.id}/confirm`),'已提交生图');finishTemporaryImage(rec,pid);return await refresh();}finally{cameraBusy.delete(`${pid}:scene:${sid}`);if(P?.id===pid)renderReplacements(true);} }
  if(d.scenePhoto){sceneCaptureCasting();scenePhotoChoice={sid:d.scene,key:d.scenePhoto};renderReplacements(true);return;}
  if('scenePickCancel' in d){closeScenePhotoDialog();return;}
  if(d.sceneFollow)return sceneChangePlan(d.scene,plan=>{const setting=plan.subjects?.[d.sceneFollow];if(setting){delete setting.asset_id;delete setting.description;}});
  if(d.sceneRemove)return sceneChangePlan(d.scene,plan=>{plan.added=(plan.added || []).filter(key=>key!==d.sceneRemove);if(plan.subjects)delete plan.subjects[d.sceneRemove];});
  if(d.sceneAdd){sceneCaptureCasting();sceneAddChoice=d.sceneAdd;renderReplacements(true);return;}
  if ('motionClose' in d) { closeMotionCheck(); return; }
  if (d.motionCheck) { openMotionCheck(d.motionCheck, d.rev, el); return; }
  if(d.testChannel)return guard(()=>testChannel(d.testChannel,el));
  if (d.thirdRetry) {
    if(d.retryProject !== P?.id)return;
    if(d.thirdRetry === 'scene-camera') {const key=cameraDraftKey('scene',d.scene);cameraChannels.set(key,'third_party');cameraRouteSelected.add(key);$('#analyzeProvider').value='jimmy';$('#analyzeProvider').dataset.userSelected='1';return guard(()=>draftPlannedScene(d.scene));}
    if(d.thirdRetry === 'analyze') {$('#analyzeProvider').value='jimmy';$('#analyzeProvider').dataset.userSelected='1';updateSendVideo();return guard(submitAnalyze);}
    if(d.thirdRetry === 'script') {$('#scriptProvider').value='jimmy';$('#scriptProvider').dataset.userSelected='1';return guard(submitScript);}
    if(d.thirdRetry === 'dialogue') {$('#scriptProvider').value='jimmy';$('#scriptProvider').dataset.userSelected='1';return guard(()=>requestDialogueProposal(d.taskId,el.closest('[data-dialogue-box]')));}
    if(d.thirdRetry === 'camera' || d.thirdRetry === 'camera-image') {
      const key=cameraDraftKey(d.character,d.scene);cameraChannels.set(key,'third_party');cameraRouteSelected.add(key);
      if(d.thirdRetry === 'camera') {$('#analyzeProvider').value='jimmy';$('#analyzeProvider').dataset.userSelected='1';updateSendVideo();return guard(()=>draftSceneCamera(d.character,d.scene));}
      return guard(()=>previewSceneCamera(d.character,d.scene));
    }
  }
  if (el.id === 'refreshPageBtn') { location.reload(); return; }
  if (el.id === 'workflowNext') return guard(workflowNext);
  if ('flowIndex' in d) { e.preventDefault(); return focusWorkflow(workflowState()[Number(d.flowIndex)]); }
  if ('cameraFrame' in d) { const input=$('[data-camera-at]',el.closest('[data-camera-key]')); input.value=d.cameraFrame; updateCameraDraftInput(input); return; }
  if (d.cameraAuto) { cameraFrameSelections.set(cameraDraftKey(d.cameraAuto,d.scene),'auto'); renderCameraFrameSelection(el.closest('[data-camera-key]')); return; }
  if (d.cameraUse) return guard(() => useSceneCamera(d.character,d.scene,d.cameraUse));
  if (d.copyTask) return guard(async()=>{
    try { await navigator.clipboard.writeText(d.copyTask); toast('任务号已复制'); }
    catch { toast('复制未完成，请选中任务号手动复制',true); }
  });
  if (d.dialoguePropose) return guard(() => requestDialogueProposal(d.dialoguePropose,el.closest('[data-dialogue-box]')));
  if (d.dialogueAdopt) return guard(() => adoptDialogueProposal(d.dialogueAdopt,el.closest('[data-dialogue-box]')));
  if (d.dialogueKeep) return guard(()=>keepOriginalDialogue(d.dialogueKeep));
  if (d.dialogueJump) return focusTodo({target:'dialogue-proposal',step:'04',tid:d.dialogueJump});
  if (d.motionEdit) return focusTodo({target:'performance',step:'04',tid:d.motionEdit});
  if (d.motionDismiss) return guard(()=>dismissMotionWarning(d.motionDismiss));
  if (el.id === "statusToggle") { el.setAttribute("aria-expanded", String(el.getAttribute("aria-expanded") !== "true")); return; }
  if (d.todoTarget) { e.preventDefault(); return focusTodo({target:d.todoTarget,step:d.fixStep,tid:d.fixTask,cid:d.fixCharacter,sid:d.fixScene}); }
  if (d.go || d.fixStep) { e.preventDefault(); jumpToStep(d.go || d.fixStep, d.fixTask, d.fixCharacter, d.fixScene); return; }
  if (d.fixGuide) { updateScrollOffset(); $(`[data-replacement-check="${d.fixGuide}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  if (d.fixCamera) return fixSceneCamera(d.fixCamera, d.character, d.scene);
  if (d.fixImage1) return fixImageOrder(d.fixImage1);
  if (d.selver) { P = { ...P, ...(await guard(() => api("POST", `projects/${P.id}/breakdown/select`, { provider: d.selver }), "已切换拆解版本")) }; seen.breakdown = ""; renderAll(); scheduleReplacementCheck(true); return; }
  if (d.editcard) return editCard(d.editcard);
  if (d.savecard) return saveCard(d.savecard);
  if ("cancelcard" in d) { dirty.breakdown = false; seen.breakdown = ""; return renderAnalysis(); }
  if (d.delasset) return guard(()=>deleteAsset(d.delasset));
  if ("addseg" in d) {
    readSegEditors();
    const n = S.tasks.length + 1;
    S.tasks.push({ id: `seg-${String(n).padStart(2, "0")}-${Date.now() % 10000}`, order: n, title: "新段落", source_start: 0, source_end: 0, duration: 8, ratio: S.tasks[0]?.ratio || "9:16", mode: "image", prompt: "", start_state: "", end_state: "", need_image: "", image_ids: [], mute_clip: true, blank_captions: false, audio_id: null, voice_approved: false });
    dirty.script = true; return rerenderScriptDraft();
  }
  if ("delseg" in d) { readSegEditors(); S.tasks.splice(Number(el.closest(".seg").dataset.seg), 1); dirty.script = true; return rerenderScriptDraft(); }
  if (d.mv) { readSegEditors(); const i = Number(el.closest(".seg").dataset.seg), j = i + Number(d.mv); if (j < 0 || j >= S.tasks.length) return; [S.tasks[i], S.tasks[j]] = [S.tasks[j], S.tasks[i]]; dirty.script = true; return rerenderScriptDraft(); }
  if (d.chip) {
    const card = el.closest("[data-task]"); const t = S.tasks[Number(card.dataset.i)];
    t.image_ids = t.image_ids || [];
    const k = t.image_ids.indexOf(d.chip);
    if (k >= 0) t.image_ids.splice(k, 1); else if (t.image_ids.length < 4) t.image_ids.push(d.chip); else return toast("每段最多 4 张参考图", true);
    dirty.script = true; card.outerHTML = taskCard(t, Number(card.dataset.i)); renderRuns(t); scheduleReplacementCheck(true); return;
  }
  if (d.preview) {
    const card = el.closest("[data-task]"); readTaskCard(card);
    return previewTask(d.preview, el);
  }
  if (d.confirm) {
    if (!await pageConfirm(`确认提交 ${d.confirm} ${d.rev} 到 三方渠道？这会产生费用。`)) return;
    await guard(() => api("POST", `projects/${P.id}/tasks/${d.confirm}/confirm`, { rev: d.rev, job_sha256: d.sha }), "已提交，正在生成"); return refresh();
  }
  if (d.cancel) { await guard(() => api("POST", `projects/${P.id}/tasks/${d.cancel}/cancel`, { rev: d.rev })); return refresh(); }
  if (d.choose) { const np = await guard(() => api("POST", `projects/${P.id}/tasks/${d.choose}/choose`, { rev: d.rev }), "已选用"); P = { ...P, script: np.script }; seen.script = ""; return renderAll(); }
  if (d.resume) { await guard(() => api("POST", `projects/${P.id}/tasks/${d.resume}/resume`, { rev: d.rev }), "正在恢复"); return refresh(); }
  if (d.resubmit) { await guard(() => api("POST", `projects/${P.id}/tasks/${d.resubmit}/resubmit`, { rev: d.rev }), "正在重新提交"); return refresh(); }
  if (d.recover) {
    const id = prompt("填写三方渠道后台里这次任务的远端 task_id（不会重新创建付费任务）"); if (!id) return;
    await guard(() => api("POST", `projects/${P.id}/tasks/${d.recover}/recover`, { rev: d.rev, remote_task_id: id }), "已关联，正在查询"); return refresh();
  }
  if (d.doc) {
    e.preventDefault();
    const pre = el.closest("#framesStrip").querySelector(".docview");
    if (pre.dataset.f === d.doc && !pre.hidden) { pre.hidden = true; return; }
    const txt = await guard(() => workbenchFetch(fileUrl(d.doc)).then((r) => r.text()));
    pre.textContent = txt; pre.dataset.f = d.doc; pre.hidden = false; return;
  }
  if (d.cameraZoom) return openCameraLightbox(d.cameraZoom, d.cameraCaption || '主镜头图');
  if ('cameraClose' in d || (el.id === 'cameraLightbox' && e.target === el)) { $('#cameraLightbox').hidden = true; return; }
  if (d.cameraBatchDraft) return guard(() => batchSceneCameras(d.cameraBatchDraft, false));
  if (d.cameraBatchConfirm) return guard(() => batchSceneCameras(d.cameraBatchConfirm, true));
  if (d.camdraft) return guard(() => draftSceneCamera(d.camdraft, d.scene || firstCharacterScene(d.camdraft)));
  if ('campreview' in d) return guard(() => previewSceneCamera(d.campreview, d.scene, {recordId:d.cameraRecord}));
  if (d.imgform) return openImageForm(d.imgform);
  if (d.imgpreview) {
    const box = $(`[data-imgbox="${d.imgpreview}"]`); const t = S.tasks.find((x) => x.id === d.imgpreview);
    const body = { prompt: $('[data-ig="prompt"]', box).value, model: $('[data-ig="model"]', box).value, resolution: $('[data-ig="resolution"]', box).value, image_ids: t.image_ids || [], ratio: t.ratio, for_task: t.id };
    if (!body.prompt.trim()) return toast("先写生图提示词", true);
    await guard(() => api("POST", `projects/${P.id}/images/preview`, body), "生图预览已生成"); box.hidden = true; return refresh();
  }
  if (d.imgconfirm) {
    const rec=P.images?.[d.imgconfirm], card=el.closest('[data-camera-key]');
    if (card) {
      const cid=card.closest('[data-camera-person]').dataset.cameraPerson, sid=card.dataset.scene;
      if (cameraRecords(cid,sid).at(-1)?.id !== rec?.id || cameraConfirmReason(cid,sid,rec)) return;
    }
    if (!await pageConfirm(`确认生图（${imageFee(rec)}）？`)) return;
    const pid=P.id;
    await guard(() => api("POST", `projects/${pid}/images/${d.imgconfirm}/confirm`), "已提交生图");
    finishTemporaryImage(rec,pid);return refresh();
  }
  if (d.imgresume) return guard(()=>resumeImage(d.imgresume));
  if (el.id === "assembleBtn") { if(assemblySaving.has(P.id))return;el.disabled = true; el.textContent = "拼接中…"; try { await guard(() => api("POST", `projects/${P.id}/assemble`), "成片已生成"); } finally { await refresh(); } }
  if (el.id === "saveScript") return guard(()=>saveScript());
  if (el.id === "clearSourceBtn") {
    if (!await pageConfirm("清除这个参考视频，以及它的拆解结果、脚本和预览记录？素材库会保留。")) return;
    P = { ...P, ...(await guard(() => api("DELETE", `projects/${P.id}/source`), "已清除，可以上传新视频")) };
    Object.keys(seen).forEach((k) => (seen[k] = "")); S = null; dirty.script = false;
    return refresh();
  }
});
document.addEventListener('dblclick',event=>{
  const thumb=event.target.closest?.('[data-batch-thumb]');
  if(thumb){event.preventDefault();batchZoom(thumb.dataset.project,thumb.dataset.batchThumb,thumb.querySelector('[data-batch-zoom]'));return;}
  const cell=event.target.closest?.('[data-compare-cell]') || event.target.closest?.('[data-story-preview-holder]')?.querySelector('[data-compare-cell]');
  if(!cell || storyboardPointerSnapshot?.marquee || storyboardPointerCandidate?.marquee || comparisonSuppressClick)return;
  const item=storyboardPreviewCell(cell.dataset.compareCell,cell.dataset.compareColumn);if(!item)return;
  event.preventDefault();restoreStoryboardPointer(cell);comparisonDrag=null;
  openStoryboardLightbox(item,$$('[data-compare-cell]').find(el=>el.dataset.compareKey===cell.dataset.compareKey) || cell);
});
document.addEventListener('pointerdown',event=>{
  if(!storyboardLightbox?.zoom || event.button!==0 || !event.target.hasAttribute?.('data-story-preview-image'))return;
  storyboardSuppressImageClick=false;storyboardPan={pointer:event.pointerId,x:event.clientX,y:event.clientY,moved:false};event.target.setPointerCapture?.(event.pointerId);event.preventDefault();
});
document.addEventListener('pointermove',event=>{
  const pan=storyboardPan;if(!pan || pan.pointer!==event.pointerId)return;
  const dx=event.clientX-pan.x,dy=event.clientY-pan.y;if(!pan.moved && Math.hypot(dx,dy)<4)return;
  pan.moved=true;pan.x=event.clientX;pan.y=event.clientY;panStoryboardLightbox(dx,dy);event.preventDefault();
});
document.addEventListener('pointerup',event=>{if(storyboardPan?.pointer===event.pointerId){storyboardSuppressImageClick=storyboardPan.moved;storyboardPan=null;}});
document.addEventListener('pointercancel',()=>{storyboardPan=null;storyboardSuppressImageClick=false;});
document.addEventListener('wheel',event=>{
  if(!storyboardLightbox || !event.target.closest?.('#storyboardLightbox .story-preview-stage'))return;
  event.preventDefault();const unit=event.deltaMode===1?16:event.deltaMode===2?storyboardLightbox.height:1;
  panStoryboardLightbox(-(event.deltaX || 0)*unit,-(event.deltaY || 0)*unit);
},{passive:false});
document.addEventListener('load',event=>{if(event.target.hasAttribute?.('data-story-preview-image'))layoutStoryboardLightbox();},true);
if(typeof window!=='undefined')window.addEventListener('resize',()=>{if(storyboardLightbox)layoutStoryboardLightbox();});

document.addEventListener('pointerdown',startComparisonDrag);
document.addEventListener('pointermove',moveComparisonDrag);
document.addEventListener('pointerup',e=>finishComparisonDrag(e));
document.addEventListener('pointercancel',e=>finishComparisonDrag(e,true));
document.addEventListener('dragstart',e=>{if(e.target.closest?.('[data-compare-side="target"]'))e.preventDefault();});
document.addEventListener("mouseover", e=>{
  if(!P)return;
  const cell=e.target.closest?.('[data-compare-cell]'),sid=cell?.dataset.compareSegment;
  for(const outline of $$('[data-segment-outline]'))outline.setAttribute('class',[outline.dataset.segmentOutline===comparisonSelection().sid?'is-selected':'',sid && !cell.disabled && outline.dataset.segmentOutline===sid?'is-hovered':''].join(' '));
});
document.addEventListener('focusout', e => {if(e.target.dataset?.libraryName)return libraryRename(e.target);});
document.addEventListener("keydown", async e => {
  if(storyboardLightboxKey(e))return;
  if(['Enter',' '].includes(e.key) && e.target.matches?.('[data-story-row]')){e.preventDefault();openStoryboardLightbox(storyboardPreviewCell(e.target.dataset.storyRow,'source'),e.target);return;}
  if(e.target.dataset?.libraryName && !libraryRenameState(e.target.dataset.libraryScope)?.busy && ['Enter','Escape'].includes(e.key)){e.preventDefault();return libraryRename(e.target,e.key==='Escape');}
  if(pageConfirmation){
    if(e.key==='Escape'){e.preventDefault();finishPageConfirmation(false);}
    if(e.key==='Tab'){const buttons=$$('button',$('#pageConfirmation')),index=buttons.indexOf(document.activeElement);e.preventDefault();buttons[(index+(e.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus();}
    return;
  }
  const materialDialog=libraryState?.open?$('#libraryPanel'):fissionCurrent() && fissionState.open?(fissionState.picker?$('.fission-picker'):$('#fissionPanel')):null;
  if(materialDialog && !materialDialog.hidden){
    if(e.key==='Escape'){e.preventDefault();if(libraryState?.open)closeLibrary();else if(fissionState.picker)closeFissionPicker();else closeFission();return;}
    if(e.key==='Tab'){const fields=$$('button,input,select,textarea,a',materialDialog).filter(el=>!el.disabled && !el.hidden),first=fields[0],last=fields.at(-1);if(!fields.length){e.preventDefault();materialDialog.focus();}else if(e.shiftKey && (document.activeElement===first || !materialDialog.contains(document.activeElement))){e.preventDefault();last.focus();}else if(!e.shiftKey && (document.activeElement===last || !materialDialog.contains(document.activeElement))){e.preventDefault();first.focus();}return;}
  }
  if(e.key==='Escape' && comparisonDrag){finishComparisonDrag({pointerId:comparisonDrag.pointer},true);clearComparisonCells();return;}
  if(e.key==='Escape' && P && comparisonSelection().cell){clearComparisonCells();return;}
  const modal = $('#motionCheckModal');
  if (modal?.hidden === false) {
    if (e.key === 'Escape') { closeMotionCheck(); return; }
    if (e.key === 'Tab') { e.preventDefault(); $('[data-motion-close]', modal)?.focus(); }
  }
  if(e.key==='Escape' && scenePhotoChoice){closeScenePhotoDialog();return;}
  if (e.key === "Escape" && $("#cameraLightbox")) $("#cameraLightbox").hidden = true;
});
function rerenderScriptDraft() {
  const keep = S; renderScript(true); S = keep; dirty.script = true;
  $("#scriptView").querySelectorAll(".seg").forEach((el) => el.remove());
  const anchor = $("#scriptView .row.end");
  anchor.insertAdjacentHTML("beforebegin", S.tasks.map((t, i) => segEditor(t, i)).join(""));
  renderTasks(true);
}

function updateCameraDraftInput(el) {
  if (el.hasAttribute('data-camera-at')) {
    const card = el.closest('[data-camera-key]'), row = el.closest('[data-camera-person]');
    if (!card || !row) return false;
    const key = cameraDraftKey(row.dataset.cameraPerson,card.dataset.scene);
    if (el.value === '' || !Number.isFinite(Number(el.value))) cameraFrameSelections.delete(key);
    else cameraFrameSelections.set(key,Number(el.value));
    renderCameraFrameSelection(card);
    return true;
  }
  if (!el.dataset.cam) return false;
  const card = el.closest('[data-camera-key]'), row = el.closest('[data-camera-person]');
  if (!card || !row) return false;
  const draft = cameraDrafts.get(cameraDraftKey(row.dataset.cameraPerson,card.dataset.scene));
  if (draft) draft[el.dataset.cam] = el.value;
  return true;
}
document.addEventListener("input", (e) => {
  if(e.target.dataset.reviewNote){if(e.target.disabled)return;return changeReviewInput(e.target.dataset.reviewNote,e.target.dataset.rev,{note:e.target.value});}
  if(directorInput(e.target))return;
  if(newStoryboardInput(e.target))return;
  if(changeCasting(e.target))return;
  if(e.target.dataset.scenePrompt){const sid=e.target.dataset.scenePrompt,key=cameraDraftKey('scene',sid),record=sceneCameraRecords(sid).at(-1),draft=cameraDrafts.get(key) || (record && {...record.preview,image_ids:record.image_ids,for_task:record.for_task,camera_binding:record.camera_binding,frame_asset_id:record.frame_asset_id,frame_at:record.frame_at});if(draft){draft.prompt=e.target.value;cameraDrafts.set(key,draft);}return;}
  if(e.target.hasAttribute("data-camera-channel") || e.target.hasAttribute("data-scene-channel") || e.target.hasAttribute("data-scene-prompt") || e.target.hasAttribute("data-scene-time"))return;
  if(sceneEditorEnabled() && (e.target.dataset.castDesc || e.target.id==='sceneRep')) {sceneCaptureCasting();dirty.rep=true;for(const row of sceneEditorRows())if(e.target.id==='sceneRep' || row.subjects.some(s=>s.key===e.target.dataset.castDesc && !s.override))sceneEdited.add(`${P.id}:${row.scene_id}`);scheduleReplacementCheck();return;}
  if(updateDialogueProposalInput(e.target))return;
  if (updateCameraDraftInput(e.target)) return;
  const segEl = e.target.closest("#scriptView .seg");
  if (segEl && (["dialogue", "pace", "padding"].includes(e.target.dataset.k) || e.target.hasAttribute("data-dialogue-text"))) {
    readSegEditors(); const t = S.tasks[Number(segEl.dataset.seg)]; const est = estimate(t); const span = $("[data-est]", segEl);
    if (span) span.textContent = `台词估算 ${est ? fmt(est) : "—"} 秒${est && Math.ceil(est) > 15 ? " · 超过 15 秒，建议拆段" : ""}`;
  }
  if (e.target.closest("#scriptView")) dirty.script = true;
  if (e.target.closest("#replacementForm")) dirty.rep = true;
  if (e.target.closest("[data-task]")) dirty.script = true;
  if (e.target.closest("#scriptView, #replacementForm, [data-task]")) scheduleReplacementCheck();
});
document.addEventListener("change", (e) => {
  if(e.target.dataset?.fissionUpload)return fissionUpload(e.target);
  if(e.target.dataset?.fissionNote)return fissionAnswer(e.target.dataset.item,e.target.dataset.fissionNote,undefined,e.target.value);
  if(e.target.dataset?.libraryUpload)return libraryUpload(e.target);
  if(e.target.dataset.reviewIssue!==undefined){if(e.target.disabled)return;const d=e.target.dataset,run=P.runs?.[d.sid]?.find(r=>r.rev===d.rev),draft=reviewDraft(d.sid,run),index=Number(d.reviewIssue);return changeReviewInput(d.sid,d.rev,{issues:e.target.checked?[...new Set([...draft.issues,index])].sort((a,b)=>a-b):draft.issues.filter(k=>k!==index)});}
  if(e.target.hasAttribute('data-segment-mode'))return segmentPlanAction('save',e.target.dataset.segmentMode,{mode:e.target.value});
  if(directorInput(e.target))return;
  if(e.target.hasAttribute("data-edit-material")){if(e.target.disabled || segmentPlanBusy.has(P.id))return;const sid=e.target.dataset.sheet;editDraft(sid).materials[e.target.dataset.editMaterial]=e.target.checked;newStoryboardPreviews.delete(editSheetKey(sid));newStoryboardInvalidPreviews.add(editSheetKey(sid));renderNewStoryboards();return performStoryboardEdit(sid,'materials');}
  if(newStoryboardInput(e.target))return;
  if(changeCasting(e.target))return;
  if(e.target.id==='trimToSource')return saveAssemblyOption(e.target);
  const sceneEl=e.target, sd=sceneEl.dataset;
  if(sd.sceneTime){
    if(sceneEl.value==='')return;
    const requested=Number(sceneEl.value),known=[...cameraAnalysisFrames(),...(P.scene_cast?.[sd.sceneTime]?.frame_choices || [])].some(f=>Number(f.at)===requested && f.file)
      || P.assets?.some(a=>a.scene_frame && a.camera_scene===sd.sceneTime && a.frame_at===requested && a.file);
    sceneEl.value=fmt(requested);
    return guard(()=>captureSceneFrame(sd.sceneTime,known?requested:Number(sceneEl.value)));
  }
  if(sd.sceneChannel){const key=cameraDraftKey('scene',sd.sceneChannel);if(sceneEl.value){cameraChannels.set(key,sceneEl.value);cameraRouteSelected.add(key);}else{cameraChannels.delete(key);cameraRouteSelected.delete(key);}renderReplacements(true);return;}
  if(sd.sceneOn){const subject=sceneEditorRows().find(r=>r.scene_id===sd.scene)?.subjects.find(s=>s.key===sd.sceneOn);if(sceneEl.checked && !subject?.asset_id){sceneCaptureCasting();scenePhotoChoice={sid:sd.scene,key:sd.sceneOn};sceneEl.checked=false;renderReplacements(true);return;}return sceneChangePlan(sd.scene,plan=>{plan.subjects ||= {};plan.subjects[sd.sceneOn]={...(plan.subjects[sd.sceneOn] || {}),on:sceneEl.checked};});}
  if(sd.sceneAddChoice){if(!sceneEl.value)return;const key=sceneEl.value,subject=sceneGlobalSubject(key);if(!subject.asset_id){sceneCaptureCasting();scenePhotoChoice={sid:sd.sceneAddChoice,key,add:true};renderReplacements(true);return;}sceneAddChoice=null;return sceneChangePlan(sd.sceneAddChoice,plan=>{plan.added=[...new Set([...(plan.added || []),key])];plan.subjects ||= {};plan.subjects[key]={...(plan.subjects[key] || {}),on:true};});}
  if(sd.scenePick){if(!sceneEl.value || !scenePhotoChoice)return;const choice=scenePhotoChoice,aid=sceneEl.value;scenePhotoChoice=null;sceneAddChoice=null;sceneChangePlan(choice.sid,plan=>{if(choice.add)plan.added=[...new Set([...(plan.added || []),choice.key])];plan.subjects ||= {};plan.subjects[choice.key]={...(plan.subjects[choice.key] || {}),on:true,asset_id:aid};});$(`[data-scene-camera="${choice.sid}"] [data-scene-photo="${choice.key}"]`)?.focus({preventScroll:true});return;}
  if(sd.cast || sd.castDesc || sceneEl.id==='sceneRep' && sceneEditorEnabled()) {sceneCaptureCasting();dirty.rep=true;for(const row of sceneEditorRows())if(sceneEl.id==='sceneRep' || row.subjects.some(s=>s.key===(sd.cast || sd.castDesc) && !s.override))sceneEdited.add(`${P.id}:${row.scene_id}`);renderReplacements(true);scheduleReplacementCheck();return;}
  if(e.target.hasAttribute('data-model-card'))return changeSettingsCard(e.target);
  if(e.target.hasAttribute('data-image-route'))return changeSettingsImageRoute(e.target);
  if(e.target.closest('#settingsForm') && SETTINGS_MODEL_CHANNELS[e.target.getAttribute('name')])return changeSettingsModel(e.target);
  if(['analyzeProvider','scriptProvider'].includes(e.target.id))e.target.dataset.userSelected=e.target.value ? '1' : '';
  if(e.target.id==='scriptProvider')updateDialogueModelLabels();
  if(updateDialogueProposalInput(e.target))return;
  if(e.target.hasAttribute('data-camera-channel')) {
    const el=e.target,cid=el.dataset.cameraChannel,sid=el.dataset.scene,key=cameraDraftKey(cid,sid),rec=cameraRecords(cid,sid).at(-1),card=el.closest('[data-camera-key]');
    const old=cameraDrafts.get(key) || (rec?.preview?.prompt ? {...rec.preview,image_ids:rec.image_ids,for_task:rec.for_task,camera_binding:rec.camera_binding,frame_reason_code:rec.frame_reason_code || rec.frame_reason} : null);
    if(old?.prompt)cameraDrafts.set(key,{...old,prompt:$('[data-cam="prompt"]',card)?.value || old.prompt});
    if(el.value){cameraChannels.set(key,el.value);cameraRouteSelected.add(key);}
    else {cameraChannels.delete(key);cameraRouteSelected.delete(key);}
    renderReplacements(true);return;
  }
  const el = e.target;
  if (updateCameraDraftInput(el)) return;
  if (el.closest("[data-asset]")) return patchAsset(el);
  if (el.closest("#scriptView .seg") && el.dataset.k === "kind") { readSegEditors(); dirty.script = true; return rerenderScriptDraft(); }
  if (el.closest("#scriptView .seg") && el.hasAttribute('data-speaker')) {
    readSegEditors(); dirty.script = true;
    const section = el.closest('.speaker-editor'), t = S.tasks[Number(el.closest('.seg').dataset.seg)];
    const count = dialogueLines(t).filter(l => !l.speaker || l.speaker === 'unknown').length;
    const warning = $('[data-speaker-warning]', section);
    if (warning) { warning.hidden = !count; warning.textContent = `有 ${count} 句台词还没确定是谁说的，请在第 04 步确认`; }
    else if (count) { const p = document.createElement('p'); p.setAttribute('class','caption-warning'); p.setAttribute('data-speaker-warning',''); p.setAttribute('role','status'); p.textContent = `有 ${count} 句台词还没确定是谁说的，请在第 04 步确认`; section.insertBefore(p, $('.speaker-lines',section)); }
    scheduleReplacementCheck(); return;
  }
  const card = el.closest("[data-task]");
  if (card && el.dataset.m) {
    if (el.dataset.m === "mode" && $("#scriptView .seg")) readSegEditors();
    readTaskCard(card); dirty.script = true;
    if (el.dataset.m === "mode") return rerenderScriptDraft();
    if (["mode", "blank_captions"].includes(el.dataset.m)) { const i = Number(card.dataset.i); card.outerHTML = taskCard(S.tasks[i], i); renderRuns(S.tasks[i]); }
    scheduleReplacementCheck();
  }
});

$("#newProjectBtn").onclick = newProject;
$("#deleteProjectBtn").onclick = async () => {
  const button = $("#deleteProjectBtn");
  if (!P || button.disabled || projectUnavailable(P.id)) return;
  const pid = P.id;
  if (!await pageConfirm(`删除项目「${P.name}」？它的原片、拆解文档、生成的视频和成片都会被永久删除。删除后无法恢复。`)) return;
  const label = button.textContent;
  button.disabled = true; button.textContent = "正在删除…";
  deletingProjects.add(pid);
  const generation = beginProjectView();
  let deleted = false;
  try {
    await api("DELETE", `projects/${pid}`);
    deleted = true; deletedProjects.add(pid);
    try { if (storeGet("vrc.project") === pid) localStorage.removeItem("vrc.project"); } catch (_) {}
    $(`#projectSelect option[value="${pid}"]`)?.remove();
    toast("项目已删除");
    if (P?.id === pid) { P = null; S = null; $("#workspace").hidden = true; $("#emptyState").hidden = false; }
    if (generation === projectGeneration || !P && !openingProjectId) await boot();
  } catch (error) {
    deletingProjects.delete(pid);
    toast(deleted ? "项目已删除，加载项目列表失败：" + error.message : error.message, true);
    if (!deleted && P?.id === pid) schedulePoll();
  } finally {
    deletingProjects.delete(pid);
    button.disabled = false; button.textContent = label;
  }
};
$("#heroNew").onclick = newProject;
$("#projectSelect").onchange = (e) => e.target.value.startsWith("batch:") ? openBatchBoard(e.target.value.slice(6)) : openProject(e.target.value);
$("#settingsBtn").onclick = openSettings;
$("#saveSettings").onclick = saveSettings;
$("#saveReplacements").onclick = saveReplacements;
$("#projectName").addEventListener("blur", async (e) => {
  const name = e.target.textContent.trim(); if (!name || name === P.name) return;
  const pid=P.id;await guard(() => api("PATCH", `projects/${pid}`, { name }), "已重命名");if(P?.id!==pid)return;P.name=name;
  projectList=projectList.map(p=>p.id===pid?{...p,name}:p);renderProjectOptions(pid);
});
$("#analyzeProvider").onchange = updateSendVideo;
$("#analyzeBtn").onclick = () => guard(submitAnalyze);
$("#scriptBtn").onclick = () => guard(submitScript);
bindDrop($("#sourceDrop"), $("#sourceInput"), async ([f]) => {
  toast("上传中…");
  await guard(() => upload(`projects/${P.id}/source`, f), "参考视频已上传");
  $("#sourceView").dataset.k = ""; refresh();
});
bindDrop($("#assetInput").closest("label"), $("#assetInput"), async (files) => {
  for (const f of files) {
    const role = /商品|产品|product/i.test(f.name) ? "product_truth" : /场景|scene/i.test(f.name) ? "scene" : "person_identity";
    await guard(() => upload(`projects/${P.id}/assets?kind=image&roles=${role}&label=${encodeURIComponent(f.name.replace(/\.[^.]+$/, ""))}`, f));
  }
  toast("素材已上传，记得补充说明并确认类型"); refresh(true);
});
bindDrop($("#audioInput").closest("label"), $("#audioInput"), async ([f]) => {
  await guard(() => upload(`projects/${P.id}/assets?kind=audio&roles=voice_identity&label=${encodeURIComponent(f.name.replace(/\.[^.]+$/, ""))}`, f), "声线样本已上传");
  refresh(true);
});
window.addEventListener("beforeunload", (e) => { if (dirty.script || dirty.rep || dirty.breakdown || [...fissionDraftStates.values()].some(s=>s.unsaved || s.busy) || P && directorDrafts.get(P.id)?.dirty) { e.preventDefault(); e.returnValue = ""; } });

// guard() already showed the error as a toast
window.addEventListener("unhandledrejection", (e) => e.preventDefault());

updateScrollOffset();
if (typeof ResizeObserver !== "undefined") new ResizeObserver(updateScrollOffset).observe($(".topbar"));
window.addEventListener("resize", updateScrollOffset);
boot().catch((e) => toast("启动失败：" + e.message, true));
