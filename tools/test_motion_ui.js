/* Real render functions in a minimal offline DOM. No server/model requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/app.js'), 'utf8');
const live = { dataset: {}, innerHTML: '' };
const context = vm.createContext({ console, document: { querySelector: () => live }, fileUrl: String, assetOptions: () => '' });
vm.runInContext(source.slice(0, source.indexOf('// ---------- infra ----------')) +
  source.slice(source.indexOf('function segEditor(t, i) {'), source.indexOf('function readSegEditors() {')) +
  source.slice(source.indexOf('function taskCard(t, i) {'), source.indexOf('function readTaskCard(el) {')) +
  source.slice(source.indexOf('function renderRuns(t) {'), source.indexOf('function openImageForm(tid) {')), context);
vm.runInContext(`P = {id:'mock', assets:[], runs:{}}; S = {tasks:[]};`, context);
context.task = { id:'seg-01', kind:'speaker', mode:'video', source_start:0, source_end:15, duration:15,
  dialogue:'Hello!', action:'Look at the viewer.', recipe:{},
  source_motion_timeline:[{start:4.7,end:6.15,action:'手横过胸前并启动转身 <script>'}] };
let html = vm.runInContext('segEditor(task, 0)', context);
assert.equal((html.match(/data-recipe="[^"]+" disabled/g) || []).length, 5);
assert.match(html, /这一段的动作和镜头都跟着原片走，这里只需要写语气、表情和重音/);
html = vm.runInContext('taskCard(task, 0)', context);
assert.match(html, /<details[^>]*>[\s\S]*<summary>原片动作时间轴（只读）/);
assert.match(html, /4\.70–6\.15/);
assert.match(html, /手横过胸前并启动转身 &lt;script&gt;/);
assert.doesNotMatch(html, /<textarea[^>]*>[\s\S]*手横过胸前/);
context.task.performance_cleanup = [{field:'action', before:'She turns <script>.', after:'A warm smile <b>.'},
  {field:'story', before:'She walks.', after:''}];
html = vm.runInContext('taskCard(task, 0)', context);
assert.match(html, /<details[^>]*><summary>表演描述清理：2 处<\/summary>/);
assert.match(html, /<del>She turns &lt;script&gt;\.<\/del>/);
assert.match(html, /<ins>A warm smile &lt;b&gt;\.<\/ins>/);
assert.match(html, /<ins>（已删除）<\/ins>/);
assert.doesNotMatch(html, /<script>|<b>\./);
delete context.task.performance_cleanup;
assert.doesNotMatch(vm.runInContext('taskCard(task, 0)', context), /表演描述清理/);
context.task.kind = 'broll';
html = vm.runInContext('segEditor(task, 0)', context);
assert.match(html, /这一段的动作和镜头都跟着原片走，这里只需要写语气、表情和重音/);
assert.doesNotMatch(html, /微故事（按参考图顺序写每张图的作用和动作）/);
assert.equal((html.match(/data-recipe="[^"]+" disabled/g) || []).length, 5);
context.task.kind = 'speaker';
context.task.mode = 'image';
assert.doesNotMatch(vm.runInContext('segEditor(task, 0)', context), /data-recipe="[^"]+" disabled/);
vm.runInContext(`P.runs['seg-01']=[{state:'completed',rev:'r1',motion_check:{status:'misaligned',
  motion_lag_seconds:-.8,motion_correlation:.3,speech_lags:[{text:'The back',lag_seconds:-1.03}],
  warnings:['成片整体比原片提前约 0.8 秒']}}]`, context);
vm.runInContext('renderRuns(task)', context);
assert.doesNotMatch(live.innerHTML, /caption-warning|The back|0\.8|1\.03/);
assert.match(live.innerHTML, /data-motion-check="seg-01"[\s\S]*查看自动检查（仅供参考）/);
const details = vm.runInContext('motionCheckDetails(P.runs["seg-01"][0].motion_check,task,P.runs["seg-01"][0])', context);
assert.match(details, /0\.80/);assert.match(details, /The back/);assert.match(details, /1\.03/);
context.task.blank_captions = true; // Timed mask warnings remain opt-in.
vm.runInContext(`P.runs['seg-01']=[{state:'pending_confirmation',rev:'r2',job_sha256:'abcdef1234567890',
  warnings:['改写后的台词没有原片时间对照，口型和动作节奏可能偏离'],
  clip_processing:{muted:true,masks:[{bbox:[0,.4,1,.9],start:1,end:2,
    motion_warning:'这段字幕很大（覆盖画面高度 40–90%），这段时间里下半身动作的参考会变弱'}]},
  preview:{tasks:[{images:[],reference_videos:[],prompt:'test'}]}}]`, context);
vm.runInContext('renderRuns(task)', context);
assert.match(live.innerHTML, /1\.00–2\.00 秒：这段字幕很大（覆盖画面高度 40–90%）/);
assert.match(live.innerHTML, /改写后的台词没有原片时间对照/);
// Both locations must distinguish execution status from whether additions were found.
context.task.mode = 'video';
for (const [comparison, expected] of [
  [{status:'completed',provider:'jimmy',model:'gpt-5.6-sol'}, /动作对比：已完成（由 三方渠道 \/ gpt-5.6-sol）/],
  [{status:'incomplete',reason:'推理占满 <script>'}, /动作对比：未完成（推理占满 &lt;script&gt;）/],
]) {
  context.comparison = comparison;
  vm.runInContext(`P.replacement_checks={'seg-01':{errors:[],warnings:[],action_comparison:comparison}};
    P.runs['seg-01'][0].action_comparison=comparison`, context);
  assert.match(vm.runInContext('taskCard(task, 0)', context), expected);
  vm.runInContext('renderRuns(task)', context);
  assert.match(live.innerHTML, expected);
  assert.doesNotMatch(live.innerHTML, /<script>/);
}
vm.runInContext(`P.replacement_checks={'seg-01':{errors:[],warnings:[]}};
  delete P.runs['seg-01'][0].action_comparison`, context);
assert.match(vm.runInContext('taskCard(task, 0)', context), /动作对比：未完成（[^）]+）/);
vm.runInContext('renderRuns(task)', context);
assert.match(live.innerHTML, /动作对比：未完成（[^）]+）/);
console.log('PASS: expandable read-only timeline, video recipe disabled, image recipe enabled, alignment and caption warnings');
