/* Actual renderers, no server or network. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../web/app.js"), "utf8");
const live = { dataset: {}, innerHTML: "" };
const context = vm.createContext({
  STATUS: { providers: {} },
  P: { id: "mock", assets: [], runs: {}, replacement_checks: { "seg-01": {
    errors: ["第 03 步人物处理是‘保持’，但这一段用了新人物图。"], force_mute: true,
    mute_reason: "原声会把旧人物的声音带进来，Seedance 会按新人物生成声音。" } } },
  $: () => live, esc: (s) => String(s || "").replaceAll("<", "&lt;"),
  fmt: String, fileUrl: String, assetOptions: () => "", KIND_LABELS: { speaker: "口播" },
});
vm.runInContext(source.slice(source.indexOf('function joinDisplayText('), source.indexOf('function issuesByTime(')) +
  source.slice(source.indexOf('function targetSeconds(t)'), source.indexOf('const IMAGE_MODELS')) +
  source.slice(source.indexOf("// Generated HTML"), source.indexOf("// ---------- infra ----------")) +
  source.slice(source.indexOf('function selectedProvider(sel)'), source.indexOf('function providerOptions(sel)')) +
  source.slice(source.indexOf("function taskCard(t, i) {"), source.indexOf("function readTaskCard(el) {")) +
  source.slice(source.indexOf("function renderRuns(t) {"), source.indexOf("function openImageForm(tid) {")), context);
context.t = { id: "seg-01", kind: "speaker", mode: "video", mute_clip: false,
  replacement_safety: [{ field: "action", before: "blonde hair", after: "the person from @image1" }] };
const html = vm.runInContext("taskCard(t, 0)", context);
assert.match(html, /第 03 步人物处理是/);
assert.match(html, /映射安全改写：1 处/);
assert.match(html, /blonde hair/);
assert.match(html, /the person from @image1/);
assert.doesNotMatch(html, /data-m="blank_captions"[^>]*checked/);
assert.match(html, /data-m="mute_clip"[^>]*checked[^>]*disabled/);
assert.match(html, /Seedance 会按新人物生成声音/);
assert.ok(html.indexOf("第 03 步人物处理是") < html.indexOf('data-preview='));
context.t.blank_captions = false;
assert.doesNotMatch(vm.runInContext("taskCard(t, 0)", context), /字幕可能被复制进成片/);
context.t.blank_captions = true; // Existing residue/mask assertions exercise explicit opt-in.
context.P.runs["seg-01"] = [{ state: "completed", rev: "r1", copy_suspected: true, similarity: .97 }];
vm.runInContext("renderRuns(t)", context);
assert.match(live.innerHTML, /成片与原片高度相似，替换可能没有生效，请检查后再使用/);
context.P.runs["seg-01"] = [{ state: "pending_confirmation", rev: "r2", job_sha256: "1234567890abcdef",
  clip: "clips/processed.mp4", clip_processing: { muted: true, masks: [{ bbox: [.1,.7,.9,.9], start: 1, end: 2 }] },
  preview: { tasks: [{ images: [], reference_videos: [], prompt: "test" }] } }];
vm.runInContext("renderRuns(t)", context);
assert.match(live.innerHTML, /处理后参考片段/);
assert.match(live.innerHTML, /clips\/processed.mp4/);
assert.match(live.innerHTML, /已去掉原声/);
assert.match(live.innerHTML, /字幕区域处理：1 处/);
context.P.replacement_checks['seg-01'] = { errors: [], warnings: ['台词提到原片外观 <slit>'] };
const warningCard = vm.runInContext('taskCard(t, 0)', context);
assert.match(warningCard, /class="[^"]*warning[^"]*"[^>]*>台词提到原片外观 &lt;slit>/);
assert.doesNotMatch(warningCard, /data-preview="seg-01"[^>]*disabled/);
context.P.replacement_checks['seg-01'].errors = ['必须修复的错误'];
const errorCard = vm.runInContext('taskCard(t, 0)', context);
assert.match(errorCard, /color:var\(--pay\)[^>]*>必须修复的错误/);
assert.match(errorCard, /data-preview="seg-01"[^>]*disabled/);
assert.match(errorCard, /台词提到原片外观/);
context.P.runs['seg-01'][0].caption_residue = {
  status: 'checked', suspected: [{ time: 9, source_time: 10, score: .004, thumbnail: 'packages/r2/residue/frame-9.jpg' }]
};
vm.runInContext('renderRuns(t)', context);
assert.match(live.innerHTML, /class="[^"]*warning/);
assert.match(live.innerHTML, /可能有字幕残留/);
assert.match(live.innerHTML, /<img[^>]*src="packages\/r2\/residue\/frame-9.jpg"/);
assert.match(live.innerHTML, /0.004/);
assert.doesNotMatch(live.innerHTML, /data-confirm[^>]*disabled/);
context.P.runs['seg-01'][0].caption_residue = { status: 'skipped', reason: '原片没有字幕，跳过残留检查', suspected: [] };
vm.runInContext('renderRuns(t)', context);
assert.match(live.innerHTML, /原片没有字幕，跳过残留检查/);
const css = fs.readFileSync(require('node:path').join(__dirname, '../web/style.css'), 'utf8');
assert.match(css, /\.caption-warning\s*\{[^}]*background:\s*#fff3cd/);
console.log("PASS: replacement errors, safety diff, caption option, forced mute, copy warning, processed clip (offline)");
