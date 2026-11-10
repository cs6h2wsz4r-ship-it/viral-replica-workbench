/* Offline integration regression: real renderAll/refresh, cloned project, mock API
 * and timers. The small DOM models node identity, focus, selection and scrolling.
 * No workbench server, model, package install or protected-directory writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const project = JSON.parse(fs.readFileSync(path.join(root, 'tools/fixtures/round24/p1027180021-c16a/project.json'), 'utf8'));
const decode = s => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, k) => ({ amp:'&', lt:'<', gt:'>', quot:'"', '#39':"'" }[k]));
let document;
class Node {
  constructor(tag, text = '') {
    this.nodeType = tag ? 1 : 3; this.tagName = tag?.toUpperCase(); this.nodeValue = text;
    this.childNodes = []; this.attrs = {}; this.style = { setProperty() {} };
    this.dataset = new Proxy({}, { get: (_, k) => this.getAttribute('data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())) ?? undefined,
      set: (_, k, v) => { this.setAttribute('data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase()), v); return true; } });
    this.classList = { add: c => this.setAttribute('class', (this.getAttribute('class') || '') + ' ' + c), remove() {} };
    this.selectionStart = 0; this.selectionEnd = 0; this.selectionDirection = 'none'; this.scrollTop = 0;
    this.currentTime = 0; this.paused = true; this.reloads = 0;
  }
  get attributes() { return Object.entries(this.attrs).map(([name, value]) => ({ name, value })); }
  get id() { return this.getAttribute('id') || ''; }
  get parentElement() { return this.parentNode; }
  get children() { return this.childNodes.filter(n => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] || null; }
  get nextElementSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  get isConnected() { return this === document?.body || !!this.parentNode?.isConnected; }
  get open() { return this.hasAttribute('open'); }
  set open(v) { v ? this.setAttribute('open', '') : this.removeAttribute('open'); }
  get src() { return this.getAttribute('src'); }
  get value() { return this._value ?? (this.tagName === 'TEXTAREA' ? this.textContent : this.tagName === 'SELECT' ? (this.querySelector('option[selected]') || this.querySelector('option'))?.value || '' : this.getAttribute('value') || ''); }
  set value(v) { this._value = v; }
  get type() { return this.getAttribute('type') || ''; }
  get checked() { return this._checked ?? this.hasAttribute('checked'); }
  set checked(v) { this._checked = v; }
  getAttribute(k) { return Object.hasOwn(this.attrs, k) ? this.attrs[k] : null; }
  hasAttribute(k) { return Object.hasOwn(this.attrs, k); }
  setAttribute(k, v) { if (k === 'src' && this.attrs[k] !== String(v)) this.reloads++; this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  contains(n) { return n === this || this.childNodes.some(c => c.contains(n)); }
  appendChild(n) { return this.insertBefore(n, null); }
  insertBefore(n, ref) { n.remove(); const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length; this.childNodes.splice(i, 0, n); n.parentNode = this; return n; }
  replaceChild(n, old) { this.insertBefore(n, old); old.remove(); return old; }
  replaceWith(n) { this.parentNode.replaceChild(n, this); }
  removeChild(n) { n.remove(); return n; }
  remove() { if (this.parentNode) { this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1); this.parentNode = null; } if (this.contains(document?.activeElement)) document.activeElement = document.body; }
  cloneNode(deep) { const n = new Node(this.tagName, this.nodeValue); n.attrs = { ...this.attrs }; if (deep) this.childNodes.forEach(c => n.appendChild(c.cloneNode(true))); return n; }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(c => c.textContent).join(''); }
  set textContent(v) { this.childNodes.slice().forEach(c => c.remove()); if (v) this.appendChild(new Node(null, String(v))); }
  get innerHTML() { return this.childNodes.map(n => n.nodeType === 3 ? n.nodeValue : `<${n.tagName.toLowerCase()}${n.attributes.map(a => ` ${a.name}="${a.value}"`).join('')}>${n.innerHTML}</${n.tagName.toLowerCase()}>`).join(''); }
  set innerHTML(html) {
    this.childNodes.slice().forEach(c => c.remove()); this.writes = (this.writes || 0) + 1;
    const stack = [this];
    for (const token of html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
      if (token.startsWith('<!--')) continue;
      if (token.startsWith('</')) { stack.pop(); continue; }
      if (token[0] !== '<') { stack.at(-1).appendChild(new Node(null, decode(token))); continue; }
      const [, tag, attrs] = token.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/) || [];
      if (!tag) continue;
      const n = new Node(tag);
      for (const [, k, a, b, c] of attrs.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) n.setAttribute(k, decode(a ?? b ?? c ?? ''));
      stack.at(-1).appendChild(n);
      if (!/^(input|img|br|hr|meta|link|col|source)$/i.test(tag)) stack.push(n);
    }
    if (this.tagName === 'TEMPLATE') this.content = this;
  }
  matches(selector) {
    return selector.split(',').some(part => {
      const pieces = part.trim().split(/\s+(?![^\[]*\])/); const last = pieces.pop();
      const simple = (n, s) => {
        const tag = s.match(/^[\w-]+/); if (tag && n.tagName !== tag[0].toUpperCase()) return false;
        for (const m of s.matchAll(/#([\w-]+)|\.([\w-]+)|\[([^\]=]+)(?:="([^"]*)")?\]/g)) {
          if (m[1] && n.id !== m[1]) return false;
          if (m[2] && !(n.getAttribute('class') || '').split(/\s+/).includes(m[2])) return false;
          if (m[3] && (!n.hasAttribute(m[3]) || (m[4] !== undefined && n.getAttribute(m[3]) !== m[4]))) return false;
        }
        return n.nodeType === 1;
      };
      if (!simple(this, last)) return false;
      let parent = this.parentNode;
      while (pieces.length) { const s = pieces.pop(); while (parent && !simple(parent, s)) parent = parent.parentNode; if (!parent) return false; parent = parent.parentNode; }
      return true;
    });
  }
  closest(s) { return this.matches(s) ? this : this.parentNode?.closest(s) || null; }
  querySelectorAll(s) { return this.childNodes.flatMap(n => [...(n.nodeType === 1 && n.matches(s) ? [n] : []), ...n.querySelectorAll(s)]); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  focus() { document.activeElement = this; }
  setSelectionRange(a, b, d) { this.selectionStart = a; this.selectionEnd = b; this.selectionDirection = d; }
  getBoundingClientRect() { return { top: this._top ?? 100, bottom: (this._top ?? 100) + 100, height: 100 }; }
  addEventListener() {}
}
async function main() {
  const body = new Node('body');
  document = { body, documentElement: body, activeElement: body, createElement: tag => new Node(tag),
    querySelector: s => body.querySelector(s), querySelectorAll: s => body.querySelectorAll(s), elementFromPoint: () => document.anchor || null };
  // Parse the actual page, then run the actual app up to event wiring.
  body.innerHTML = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8').replace(/<!doctype[^>]*>/gi, '').replace(/<script[\s\S]*?<\/script>/g, '');
  const timers = new Map(); let timerId = 0, checks = 0, gets = 0;
  const window = { scrollX: 0, scrollY: 480, innerHeight: 800, innerWidth: 1280, scrollTo(x, y) { this.scrollY = typeof x === 'object' ? x.top : y; } };
  const server = structuredClone(project);
  server.script.tasks = server.script.tasks.slice(0, 1);
  server.script.tasks[0].blank_captions = true; // Caption-details preservation requires opt-in in 24.11.
  const tid = server.script.tasks[0].id;
  server.runs = { [tid]: [
    { rev:'r1', state:'completed', result:'offline-result.mp4', model:'mock' },
    { rev:'r2', state:'pending_confirmation', model:'mock', job_sha256:'offline-preview-hash',
      preview:{tasks:[{images:[],reference_videos:[],duration:15,ratio:'9:16',prompt:'预览提示词'}]},
      clip_processing:{muted:true,masks:[{start:0,end:1,bbox:[0,.8,1,1],text:'字幕'}]} }
  ] };
  server.replacements.characters[0].action = 'replace';
  server.replacements.product.action = 'replace';
  server.images = { image1: { id:'image1', for_task:'camera:C1', state:'pending_confirmation', preview:{prompt:'离线提示词'} } };
  server.script.tasks[0].replacement_safety = [{before:'旧描述',after:'新描述'}];
  server.script.tasks[0].performance_cleanup = [{field:'action',before:'旧动作',after:'微笑'}];
  const check = () => ({ errors:['动作不一致'], issues:[{code:'reference_mapping',step:'04',title:'动作不一致 ' + (server.replacements.scene || ''),fix:'重新生成脚本',details:['动作一','动作二','动作三','动作四']}], warnings:[], source_motion_timeline:[{start:0,end:2,action:'原片动作'}] });
  const context = vm.createContext({document, window, console, structuredClone, project:server,
    setTimeout:(fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; }, clearTimeout:id => timers.delete(id), localStorage:{getItem(){},setItem(){}} });
  vm.runInContext(source.slice(0, source.indexOf('// ---------- settings ----------')), context);
  context.mockApi = async (method, url) => {
    if (method === 'GET' && url === `projects/${server.id}`) { gets++; return structuredClone(server); }
    if (method === 'POST' && url.endsWith('/replacement-check')) { checks++; return { [tid]:check() }; }
    throw Error(`Forbidden unexpected API: ${method} ${url}`);
  };
  vm.runInContext('api = mockApi; P = clone(project); STATUS = {providers:{},runtime:{}}; renderStatus(); renderAll();', context);
  const flush = async () => { for (const [id, timer] of [...timers]) if (timer.ms <= 250) { timers.delete(id); await timer.fn(); } };
  const poll = async () => { await vm.runInContext('refresh()', context); await flush(); };
  const $ = s => document.querySelector(s);
  await flush();
  const details = $('[data-replacement-check] details');
  assert.match(details.textContent, /技术明细/); assert.match(details.textContent, /reference_mapping（5 条）/);
  assert.equal(details.querySelectorAll("li").length, 5); details.open = true;
  const video = $('[data-live] video'); video.currentTime = 8; video.paused = false;
  const textarea = $('#rep-product-note'); textarea.focus(); textarea.setSelectionRange(3, 7, 'backward');
  const scrollY = window.scrollY;
  const regions = ['#statusPills','#steps','#analysisSteps','#framesStrip','#breakdownVersions','#storyboardView','#logView','[data-preview-reason]','[data-motion-timeline]'];
  const children = regions.map(s => $(s)?.firstChild);
  for (let i = 0; i < 3; i++) { vm.runInContext('renderStatus()', context); await poll(); }
  assert.ok($('[data-replacement-check] details') === details, 'unchanged polling must keep the same details node');
  assert.equal(details.open, true); assert.equal(checks, 1, 'unchanged checks must be reused'); assert.equal(gets, 3);
  assert.ok($('[data-live] video') === video); assert.equal(video.src, `/files/${server.id}/offline-result.mp4`);
  assert.equal(video.currentTime, 8); assert.equal(video.paused, false);
  assert.ok(document.activeElement === textarea); assert.deepEqual([textarea.selectionStart,textarea.selectionEnd,textarea.selectionDirection],[3,7,'backward']);
  assert.equal(window.scrollY, scrollY);
  regions.forEach((s, i) => assert.ok($(s)?.firstChild === children[i], `${s} unchanged nodes`));
  server.replacements.scene = '更新后的场景'; await poll();
  assert.equal(checks, 2); assert.match($('[data-replacement-check]').textContent, /更新后的场景/); assert.equal($('[data-replacement-check] details').open, true);
  // Changed runs and a changed script must retain active playback and keyed details.
  for (const d of document.querySelectorAll('#taskCards details, .scene-camera details')) d.open = true;
  const reloads = video.reloads;
  server.runs[tid][0].remote_status = '更新'; server.images.image1.preview.prompt += '更新';
  server.runs[tid][1].preview.tasks[0].prompt += '更新'; server.runs[tid][1].clip_processing.masks[0].end = 2;
  await poll();
  assert.equal($('[data-detail-key="caption-masks"]').open, true);
  assert.equal($('[data-detail-key="preview-job"]').open, true);
  assert.ok($('[data-live] video') === video); assert.equal(video.reloads, reloads); assert.equal(video.paused, false);
  assert.equal($('.scene-camera'),null);
  server.script.tasks[0].title += '更新'; await poll();
  assert.ok($('[data-live] video') === video, 'parent task rerender must retain the running video');
  assert.ok(document.querySelectorAll('#taskCards details').every(d => d.open));
  // A changed replacement form retains focus, selection and the user's in-progress value.
  const note = $('#rep-product-note'); note.value = '尚未保存的说明'; note.focus(); note.setSelectionRange(2, 5, 'forward');
  server.replacements.product.description = '服务端更新'; await poll();
  assert.ok(document.activeElement === $('#rep-product-note')); assert.equal(document.activeElement.value, '尚未保存的说明');
  assert.deepEqual([document.activeElement.selectionStart,document.activeElement.selectionEnd],[2,5]);
  assert.equal(window.scrollY, scrollY);
  // Same number of frames/passages but changed content must update too.
  server.analysis.shots = [{index:1,start:0,end:1,frame:'changed.jpg'}]; await poll();
  server.analysis.shots[0].frame = 'changed-again.jpg'; await poll();
  assert.match($('#framesStrip').innerHTML, /changed-again.jpg/);
  const projectName = $('#projectName'); projectName.focus(); projectName.textContent = '尚未提交的新项目名';
  await poll(); assert.equal(projectName.textContent, '尚未提交的新项目名', 'contenteditable title must not lose its draft');
  note.focus();
  // Irrelevant run/log updates reuse checks; edits debounce and pending requests coalesce.
  const beforeLog = checks;
  server.log = [...(server.log || []), {level:'info',t:'offline',msg:'仅更新日志'}];
  await poll(); assert.equal(checks, beforeLog);
  const scriptInput = $('#scriptView [data-k="title"]');
  scriptInput.value = '正在编辑';
  vm.runInContext('dirty.script = true; scheduleReplacementCheck();', context);
  scriptInput.value = '正在编辑，最终内容';
  vm.runInContext('scheduleReplacementCheck(); scheduleReplacementCheck();', context);
  assert.equal([...timers.values()].filter(t => t.ms === 250).length, 1);
  await flush(); assert.equal(checks, beforeLog + 1);
  await poll(); assert.equal(checks, beforeLog + 1, 'dirty task cache survives refresh');
  const scene = $('[data-cast-description="C1"]'); scene.value = '未保存的替换设定';
  vm.runInContext('captureCasting(); scheduleReplacementCheck();', context); await flush();
  assert.equal(checks, beforeLog + 2);
  await poll(); assert.equal(checks, beforeLog + 2, 'dirty replacement cache survives refresh');
  const originalApi = context.mockApi;
  let resolveCheck;
  context.mockApi = async (method, url, body) => {
    if (url.endsWith('/replacement-check')) { checks++; return new Promise(resolve => { resolveCheck = resolve; }); }
    return originalApi(method, url, body);
  };
  vm.runInContext('api = mockApi;', context);
  scene.value = '请求进行中'; vm.runInContext('scheduleReplacementCheck()', context);
  const pendingFlush = flush();
  const pendingCount = checks;
  await poll(); await poll(); assert.equal(checks, pendingCount, 'same-input in-flight request is shared');
  // Superseded responses must not overwrite newer inputs.
  scene.value = '最新设定'; vm.runInContext('scheduleReplacementCheck()', context);
  resolveCheck({[tid]:{...check(),issues:[{code:'obsolete',step:'04',title:'过期响应',fix:''}]}});
  await pendingFlush;
  assert.doesNotMatch($('[data-replacement-check]').textContent, /过期响应/);
  context.mockApi = originalApi; vm.runInContext('api = mockApi;', context); await flush();
  const beforeRetry = checks;
  context.mockApi = async () => { checks++; throw Error('离线故障'); };
  vm.runInContext('api = mockApi;', context); scene.value = '失败后重试'; vm.runInContext('captureCasting()', context);
  vm.runInContext('scheduleReplacementCheck()', context); await flush();
  assert.match($('[data-replacement-check]').textContent, /离线故障/);
  context.mockApi = originalApi; vm.runInContext('api = mockApi;', context); await poll();
  assert.equal(checks, beforeRetry + 2); assert.match($('[data-replacement-check]').textContent, /技术明细.*reference_mapping（5 条）/s);
  vm.runInContext('showPreviewBlocked(S.tasks[0].id, {errors:["预览图号校验失败"], issues:[{code:"reference_mapping",step:"04",title:"预览图号校验失败",fix:"更新脚本",details:["新增动作"]}]})', context);
  await poll(); assert.match($('[data-replacement-check]').textContent, /预览图号校验失败/, 'cached checks must retain the preview rejection');
  // Stable issue keys survive reordering, and removed issues do not transfer open state.
  const guide = $('[data-replacement-check]');
  context.issues = [
    {code:'first',step:'04',title:'一',fix:'修复',details:['一']},
    {code:'second',step:'04',title:'二',fix:'修复',details:['二']}
  ];
  vm.runInContext('updateHtml(document.querySelector("[data-replacement-check]"), replacementCheckHtml({issues,errors:[]}));', context);
  guide.querySelector('[data-detail-key="first"]').open = true;
  context.issues.reverse();
  vm.runInContext('updateHtml(document.querySelector("[data-replacement-check]"), replacementCheckHtml({issues,errors:[]}));', context);
  assert.equal(guide.querySelector('[data-detail-key="first"]').open, true);
  assert.equal(guide.querySelector('[data-detail-key="second"]').open, false);
  context.issues[1] = {...context.issues[1],code:'new'};
  vm.runInContext('updateHtml(document.querySelector("[data-replacement-check]"), replacementCheckHtml({issues,errors:[]}));', context);
  assert.equal(guide.querySelector('[data-detail-key="new"]').open, false);
  // Model a layout shift above the visible anchor: retain its viewport position.
  document.activeElement = document.body; document.anchor = $('#step-5');
  document.anchor._top = 116; context.anchor = document.anchor;
  const beforeShift = window.scrollY;
  vm.runInContext('preserveView(() => { anchor._top += 80; });', context);
  assert.equal(window.scrollY, beforeShift + 80, 'height change compensated against the visible anchor');
  // Removing the script invalidates an outstanding check before it can touch S.
  context.mockApi = async (method, url, body) => {
    if (url.endsWith('/replacement-check')) return new Promise(resolve => { resolveCheck = resolve; });
    return originalApi(method, url, body);
  };
  scene.value = '清除前的检查'; vm.runInContext('captureCasting()', context); vm.runInContext('api = mockApi; scheduleReplacementCheck()', context);
  const removedFlush = flush();
  server.script = null; vm.runInContext('dirty.script = false;', context);
  await vm.runInContext('refresh()', context);
  resolveCheck({[tid]:check()}); await removedFlush;
  assert.match($('#taskCards').textContent, /生成新脚本后/);
  console.log('PASS: debounce, dirty drafts, in-flight coalescing, stale response rejection, error retry, issue reorder/removal, viewport anchor');
  console.log('PASS: unchanged polling ×3, one check, keyed details, changed guide/runs/script, video identity/playback, focused draft/selection, scroll, frame content (offline)');
}
main().catch(e => { console.error(e.stack.split('\n').slice(0, 8).join('\n')); process.exitCode = 1; });
