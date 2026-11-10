/* Real renderers and event handlers in an offline DOM surface. Browser layout is
 * independently checked by test_fix_wizard_browser.js's isolated static page. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
execFileSync(path.join(root, 'runtime/venv/bin/python'), [path.join(__dirname, 'test_fix_wizard.py')], { cwd: root });
const project = JSON.parse(fs.readFileSync(path.join(root, 'runtime/temp/fix-wizard-project.json')));
const originalIds = [...project.script.tasks[0].image_ids];
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
function node() { return { dataset: {}, innerHTML: '', textContent: '', hidden: false, disabled: false,
  style: { setProperty() {} }, classList: { values: new Set(), add(c) { this.values.add(c); }, remove(c) { this.values.delete(c); } },
  scrollIntoView() { this.scrolled = true; }, setAttribute(k, v) { this[k] = v; }, getBoundingClientRect() { return { height: 100 }; },
  addEventListener() {}, querySelector(s) { return s === "details" ? { addEventListener() {} } : null; } }; }
const nodes = new Map();
const get = s => { if (!nodes.has(s)) nodes.set(s, node()); return nodes.get(s); };
const row = node(); row.dataset.char = 'C1'; row.querySelector = s => get('character ' + s);
const document = { querySelector: get, querySelectorAll: s => s === '#replacementForm tr[data-char]' ? [row] : [], documentElement: node() };
const requests = [], timers = [];
let clickHandler;
document.addEventListener = (event, callback) => { if (event === 'click') clickHandler = callback; };
const context = vm.createContext({ document, console, setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {},
  fileUrl: String, assetOptions: () => '', project, renderScript() {}, renderSteps() {}, renderRuns() {},
  scheduleReplacementCheck() {}, readSegEditors() {}, refresh: async () => {}, api: async (method, url, body) => {
    requests.push({ method, url, body: structuredClone(body) });
    return { script: body.script };
  }, guard: fn => fn() });
vm.runInContext(source.slice(0, source.indexOf('// ---------- infra ----------')) +
  source.slice(source.indexOf('function toast('), source.indexOf('const fileUrl =')) +
  source.slice(source.indexOf('async function saveScript('), source.indexOf('// ---------- tasks')) +
  source.slice(source.indexOf('let replacementCheckTimer,'), source.indexOf('function applyReplacementChecks(')) +
  source.slice(source.indexOf('function materialNameHtml('), source.indexOf('function libraryNameHtml(')) +
  source.slice(source.indexOf('// ---------- storyboard image inspection'), source.indexOf('// ---------- paired storyboard workspace')) +
  source.slice(source.indexOf('function taskCard('), source.indexOf('function readTaskCard(')) + '\nP = project; S = clone(P.script);', context);
const eventStart = source.indexOf('document.addEventListener("click", async (e) => {');
vm.runInContext(source.slice(eventStart, source.indexOf("\n});", eventStart) + 4), context);
const click = dataset => clickHandler({ target: { closest: selector => /story-preview|storyboardLightbox/.test(selector) ? null : ({ dataset }) }, preventDefault() {} });
function guideText(html) {
  return html.match(/<section class="fix-wizard"[\s\S]*?<\/section>/)[0]
    .replace(/<details[^>]*><summary>([^<]+)<\/summary>[\s\S]*?<\/details>/g, '$1（默认折叠）\n')
    .replace(/<\/(?:h4|div|p|button)>/g, '\n').replace(/<[^>]*>/g, '')
    .replace(/^[ \t]+/gm, '').replace(/\n{3,}/g, '\n\n').trim();
}
let html = vm.runInContext('taskCard(S.tasks[0], 0)', context);
fs.writeFileSync(path.join(root, 'runtime/temp/fix-wizard-guide.txt'), guideText(html));
const reversed = JSON.parse(fs.readFileSync(path.join(root, 'runtime/temp/fix-wizard-reversed-project.json')));
context.reversed = reversed;
const reversedHtml = vm.runInContext('replacementCheckHtml(reversed.replacement_checks["seg-01"], reversed.script.tasks[0])', context);
fs.writeFileSync(path.join(root, 'runtime/temp/fix-wizard-reversed-guide.txt'), guideText(reversedHtml));
assert.match(reversedHtml, /预览前还要做 3 件事/);

assert.match(html, /预览前还要做 3 件事/); assert.match(html, /1\. 现在做/);
assert.equal((html.match(/class="fix-step pending"/g) || []).length, 2);
assert.match(html, /data-preview-reason="seg-01"[^>]*>[\s\S]*还要做 3 件事/);
assert.match(html, /动作描述里有 4 处身体动作/);
assert.doesNotMatch(html, /<details open/);
for (const [step, selector] of [['02', '#analyzeBtn'], ['03', 'character [data-r="action"]'], ['04', '#scriptBtn'], ['05', '[data-task="seg-01"] .chips']]) {
  click({ fixStep: step, fixTask: 'seg-01' });
  assert.ok(get(selector).scrolled); assert.ok(get(selector).classList.values.has('fix-highlight'));
}
assert.ok(get('character [data-camdraft]').classList.values.has('fix-highlight'));
vm.runInContext('renderLegacyNotice(); showPreviewBlocked("seg-01", P.replacement_checks["seg-01"])', context);
assert.equal(get('#legacyProjectBanner').hidden, false);
assert.equal(get('#toast').innerHTML, '预览前还要做 3 件事，已在 seg-01 下方列出');
vm.runInContext('toast("错误内容".repeat(80), true)', context);
assert.match(get('#toast').innerHTML, /<summary>详细信息<\/summary>/);
assert.ok(get('#toast').innerHTML.match(/class="error-summary">([^<]*)/)[1].length <= 121);
assert.match(get('#toast').innerHTML, /data-copy-error/);
async function main() {
  await click({ fixGuide: 'seg-01' });
  assert.ok(get('[data-replacement-check="seg-01"]').scrolled);
  const css = fs.readFileSync(path.join(root, 'web/style.css'), 'utf8');
  assert.match(css, /\.pill\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(css, /@media \(max-width: 1100px\)/);
  assert.match(css, /#statusPills\s*\{[^}]*display:\s*none;[^}]*position:\s*absolute/);
  assert.match(css, /scroll-margin-top:\s*calc\(var\(--topbar-height\) \+ 16px\)/);
  assert.match(css, /#statusToggle\[aria-expanded="true"\] \+ #statusPills/);

  // Test the real HTTP parsing and preview rejection without guard's full-error toast.
  context.fetch = async () => ({ ok: false, status: 400, json: async () => ({ error: '原文'.repeat(200),
    errors: ['拦截'], issues: [{ code: 'reference_mapping', step: '04', title: '图号不一致', fix: '核对本段图号后保存' }] }) });
  vm.runInContext(source.slice(source.indexOf('async function api('), source.indexOf('async function upload(')), context);
  vm.runInContext('P.replacement_checks["seg-01"] = { errors: [], issues: [] }', context);
  // Save is mocked here; server's actual save/remapping is tested in Python.
  vm.runInContext('saveScript = async () => {};', context);
  await vm.runInContext('previewTask("seg-01", document.querySelector("preview"))', context);
  assert.equal(get('#toast').innerHTML, '预览前还要做 1 件事，已在 seg-01 下方列出');
  assert.match(get('[data-replacement-check="seg-01"]').innerHTML, /图号不一致/);
  vm.runInContext(`S.tasks[0].image_ids.reverse();
    P.replacement_checks['seg-01'] = {errors:['图1错误'], issues:[{code:'image1_not_person',step:'05',title:'图1错误',fix:'调整顺序'}]};`, context);
  html = vm.runInContext('taskCard(S.tasks[0], 0)', context);
  assert.match(html, /把人物或主镜头图放到本段图1/);
  assert.match(html, /图1 应该是出镜人物或主镜头图/);
  vm.runInContext(`S.tasks[0].action = 'Smile at @image1 beside @image2.';
    S.tasks[0].story = '@image2 and @image1'; S.tasks[0].direction = '@image1 beside @image2'; P.script = clone(S);`, context);
  const oldTask = JSON.parse(vm.runInContext('JSON.stringify(P.script.tasks[0])', context));
  vm.runInContext("S.tasks[0].action = 'Smile warmly at @image1 beside @image2.';", context);
  context.api = async (method, url, body) => {
    requests.push({ method, url });
    assert.equal(method, 'PUT'); assert.match(url, /\/script$/);
    const output = execFileSync(path.join(root, 'runtime/venv/bin/python'), ['-c',
      'import json,sys; from console.pipeline import rebase_task_references; d=json.load(sys.stdin); rebase_task_references(d["task"],d["old"],order_before=d["order"]); print(json.dumps(d["task"]))'],
      { cwd: root, input: JSON.stringify({ task: body.script.tasks[0], old: oldTask, order: body.reference_orders[oldTask.id] }), encoding: 'utf8' });
    return { script: { ...body.script, tasks: [JSON.parse(output)] } };
  };
  vm.runInContext(source.slice(source.indexOf('async function saveScript('), source.indexOf('// ---------- tasks')), context);
  await click({ fixImage1: 'seg-01' });
  const savedTask = JSON.parse(vm.runInContext('JSON.stringify(P.script.tasks[0])', context));
  assert.deepEqual(savedTask.image_ids, originalIds);
  assert.equal(savedTask.action, 'Smile warmly at @image2 beside @image1.');
  assert.equal(savedTask.story, '@image1 and @image2');
  assert.equal(savedTask.direction, '@image2 beside @image1');
  assert.equal(requests.length, 1);
  vm.runInContext(`P.assets = [{id:'product',type:'image',roles:['target_state']},
    {id:'person',type:'image',roles:['person_identity']},{id:'camera',type:'image',roles:['target_state']}];
    P.replacements = {characters:[{source_id:'C1',action:'replace',asset_id:'person',camera_asset_id:'camera'}],product:{asset_id:'product'}};
    P.breakdown = {};`, context);
  assert.equal(vm.runInContext('isPersonImage("product")', context), false);
  assert.equal(vm.runInContext('firstPersonIndex({mode:"video",image_ids:["product","person","camera"]})', context), 2);
  assert.equal(vm.runInContext('firstPersonIndex({mode:"image",image_ids:["product","person","camera"]})', context), 1);
  // Remove all person images: no impossible one-click repair is offered.
  vm.runInContext('S.tasks[0].image_ids = [S.tasks[0].image_ids[1]];', context);
  assert.doesNotMatch(vm.runInContext('taskCard(S.tasks[0], 0)', context), /data-fix-image1=/);
  console.log('PASS: wizard grouping, targets/highlights, reason link, collapsed details, summary rejection, full error expansion, image order/save, missing-person fallback, old-project banner (offline)');
}
main().catch(e => { console.error(e); process.exitCode = 1; });
