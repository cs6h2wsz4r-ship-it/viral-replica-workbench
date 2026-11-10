/* Real app lifecycle, DOM, HTTP parsing and fake timers; entirely offline. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createDocument, Node, installPageConfirmationHarness} = require('./ui_test_dom');
for (const property of ['hidden', 'disabled']) Object.defineProperty(Node.prototype, property, {
  get() { return this.hasAttribute(property); },
  set(value) { value ? this.setAttribute(property, '') : this.removeAttribute(property); },
  configurable: true,
});
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const page = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const response = (data, code = 200) => ({ok: code < 400, status: code, statusText: code === 409 ? 'Conflict' : 'OK', json: async () => structuredClone(data)});
const project = id => ({id, name: '项目 ' + id, created_at: 'offline', source: {}, analysis: {}, assets: [], images: {}, jobs: {}, runs: {}, breakdown: {}, replacements: {}, script: {tasks: []}});

async function setup(ids = ['A', 'B']) {
  const document = createDocument(), events = {}, requests = [], confirmations = [], timers = new Map(), cleared = [], storage = new Map();
  document.body.innerHTML = page.replace(/<!doctype[^>]*>/gi, '').replace(/<script[\s\S]*?<\/script>/g, '');
  document.addEventListener = (name, fn) => (events[name] ||= []).push(fn);
  const projects = new Map(ids.map(id => [id, project(id)])), routes = new Map();
  const status = {providers: {openai: {ready: true, label: '官方', model: 'offline'}}, default_llm: 'openai', runtime: {}};
  let timerId = 0, confirmed = true;
  const context = vm.createContext({document, console, structuredClone,
    setTimeout(fn, ms) { timers.set(++timerId, {fn, ms}); return timerId; },
    clearTimeout(id) { cleared.push(id); timers.delete(id); },
    confirm(text) { confirmations.push(text); return confirmed; },
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key)},
    fetch: async (url, options) => {
      assert.ok(url.startsWith('/api/'), 'only synthetic relative API requests are permitted');
      const route = url.slice('/api/'.length), method = options.method;
      requests.push({method, url: route});
      const handler = routes.get(method + ' ' + route);
      if (handler) return handler();
      if (method === 'GET' && route === 'status') return response(status);
      if (method === 'GET' && route === 'projects') return response([...projects.values()].map(p => ({id: p.id, name: p.name, created_at: p.created_at})));
      const match = route.match(/^projects\/([^/]+)$/);
      if (match && method === 'GET' && projects.has(match[1])) return response(projects.get(match[1]));
      if (match && method === 'DELETE') { projects.delete(match[1]); return response({deleted: true}); }
      throw Error('Unexpected offline request: ' + method + ' ' + route);
    },
  });
  const run = code => vm.runInContext(code, context), $ = selector => document.querySelector(selector);
  run(source.slice(0, source.indexOf('// ---------- events ----------')));
  const clickStart = source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(clickStart, source.indexOf('\n$("#newProjectBtn").onclick', clickStart)));
  installPageConfirmationHarness({run, context, document, dispatch: async target => {for (const fn of events.click || []) await fn({target, preventDefault(){}});}});
  const wiring = source.indexOf('$("#newProjectBtn").onclick');
  run(source.slice(wiring, source.indexOf('$("#settingsBtn").onclick', wiring)));
  await run('boot()');
  const pollTimers = () => [...timers.entries()].filter(([, timer]) => [3000, 15000].includes(timer.ms));
  const hold = (method, url) => {
    const pending = deferred(); routes.set(method + ' ' + url, () => pending.promise); return pending;
  };
  const tickPoll = async () => { const due = pollTimers(); assert.equal(due.length, 1, 'one project poll is scheduled'); timers.delete(due[0][0]); await due[0][1].fn(); };
  return {run, $, projects, routes, requests, confirmations, timers, cleared, storage, pollTimers, tickPoll, hold, cancel: () => { confirmed = false; }};
}

const tests = [], test = (name, fn) => tests.push([name, fn]);
test('取消删除保留项目、按钮和轮询，确认文字明确不可恢复', async () => {
  const t = await setup(); t.cancel(); const poll = t.pollTimers()[0][0], before = t.requests.length;
  await t.$('#deleteProjectBtn').onclick();
  assert.match(t.confirmations[0], /原片、拆解文档、生成的视频和成片都会被永久删除/);
  assert.match(t.confirmations[0], /删除后无法恢复/);
  assert.equal(t.run('P.id'), 'A'); assert.equal(t.$('#deleteProjectBtn').disabled, false);
  assert.equal(t.$('#deleteProjectBtn').textContent, '删除项目'); assert.ok(t.timers.has(poll)); assert.equal(t.requests.length, before);
});
test('确认后立即禁用按钮、停轮询，重复点击和后续项目请求不会提交', async () => {
  const t = await setup(), hold = t.hold('DELETE', 'projects/A'), button = t.$('#deleteProjectBtn');
  const poll = t.pollTimers()[0][0], staleTimer = t.timers.get(poll).fn;
  const pending = button.onclick(); await Promise.resolve();
  assert.equal(button.disabled, true); assert.equal(button.textContent, '正在删除…');
  assert.ok(t.cleared.includes(poll)); assert.equal(t.pollTimers().length, 0);
  await button.onclick(); await t.run('refresh()'); await staleTimer();
  await assert.rejects(t.run('api("POST", "projects/A/analyze", {})'));
  assert.equal(t.requests.filter(r => r.url.startsWith('projects/A')).length, 2, 'initial load plus one DELETE only');
  t.projects.delete('A'); hold.resolve(response({deleted: true})); await pending;
  assert.equal(button.disabled, false); assert.equal(button.textContent, '删除项目');
});
test('成功后提示已删除、清除旧项目记忆并按列表选择下一项目', async () => {
  const t = await setup(['A', 'C', 'B']); await t.$('#deleteProjectBtn').onclick();
  assert.equal(t.$('#toast').textContent, '项目已删除'); assert.doesNotMatch(t.$('#toast').className, /err/);
  assert.equal(t.run('P.id'), 'C'); assert.equal(t.$('#projectSelect').value, 'C'); assert.equal(t.storage.get('vrc.project'), 'C');
  assert.equal(t.$('#projectSelect option[value="A"]'), null); assert.equal(t.$('#workspace').hidden, false); assert.equal(t.pollTimers().length, 1);
});
test('删除唯一项目进入空状态并彻底停止项目轮询', async () => {
  const t = await setup(['A']); await t.$('#deleteProjectBtn').onclick();
  assert.equal(t.$('#toast').textContent, '项目已删除'); assert.equal(t.run('P'), null); assert.equal(t.run('S'), null);
  assert.equal(t.$('#workspace').hidden, true); assert.equal(t.$('#emptyState').hidden, false);
  assert.equal(t.storage.has('vrc.project'), false); assert.equal(t.$('#projectSelect').querySelectorAll('option').length, 0);
  assert.equal(t.pollTimers().length, 0); await t.run('refresh()'); assert.equal(t.pollTimers().length, 0);
});
test('后端拒绝删除时显示红色原始原因、恢复按钮与轮询', async () => {
  const t = await setup();
  t.routes.set('DELETE projects/A', () => response({error: '任务仍在提交，请稍后重试'}, 409));
  await t.$('#deleteProjectBtn').onclick();
  assert.equal(t.$('#toast').textContent, '任务仍在提交，请稍后重试'); assert.equal(t.$('#toast').className, 'toast show err');
  assert.equal(t.$('#deleteProjectBtn').disabled, false); assert.equal(t.$('#deleteProjectBtn').textContent, '删除项目');
  assert.equal(t.run('P.id'), 'A'); assert.equal(t.storage.get('vrc.project'), 'A');
  await t.tickPoll(); assert.equal(t.run('P.id'), 'A'); assert.equal(t.pollTimers().length, 1);
});
test('删除期间完成的旧轮询不能覆盖项目或重新启动轮询', async () => {
  const t = await setup(), oldPoll = t.hold('GET', 'projects/A'), deleting = t.hold('DELETE', 'projects/A');
  const refresh = t.run('refresh()'), pending = t.$('#deleteProjectBtn').onclick(); await Promise.resolve();
  oldPoll.resolve(response({...project('A'), name: '过期轮询'})); await refresh;
  assert.equal(t.run('P.name'), '项目 A'); assert.equal(t.pollTimers().length, 0);
  t.projects.delete('A'); deleting.resolve(response({deleted: true})); await pending;
  assert.equal(t.run('P.id'), 'B'); assert.equal(t.$('#projectName').textContent, '项目 B');
});
test('删除成功并打开下一项目后，迟到的旧轮询不能改写视图、选择、记忆或轮询', async () => {
  const t = await setup(), oldPoll = t.hold('GET', 'projects/A'), pending = t.run('refresh()');
  await t.$('#deleteProjectBtn').onclick();
  assert.equal(t.run('P.id'), 'B'); const poll = t.pollTimers()[0][0];
  oldPoll.resolve(response({...project('A'), name: '已删除项目的迟到轮询'})); await pending;
  assert.equal(t.run('P.id'), 'B'); assert.equal(t.$('#projectName').textContent, '项目 B');
  assert.equal(t.$('#projectSelect').value, 'B'); assert.equal(t.storage.get('vrc.project'), 'B');
  assert.equal(t.$('#workspace').hidden, false); assert.equal(t.$('#emptyState').hidden, true);
  assert.equal(t.pollTimers().length, 1); assert.equal(t.pollTimers()[0][0], poll);
});
test('普通 API 的迟到响应被拒绝，不会在新项目显示旧操作成功提示', async () => {
  const t = await setup(), oldRequest = t.hold('POST', 'projects/A/analyze');
  const pending = t.run('guard(() => api("POST", "projects/A/analyze", {}), "旧项目操作成功")');
  await t.run('openProject("B")'); const poll = t.pollTimers()[0][0], toast = t.$('#toast').textContent;
  oldRequest.resolve(response(project('A')));
  await assert.rejects(pending, error => error.staleProject === true);
  assert.equal(t.run('P.id'), 'B'); assert.equal(t.$('#projectName').textContent, '项目 B');
  assert.equal(t.$('#toast').textContent, toast); assert.equal(t.pollTimers()[0][0], poll);
});
test('上传的迟到响应在删除后被拒绝，保留下一项目与删除成功提示', async () => {
  const t = await setup(), oldUpload = t.hold('POST', 'projects/A/source');
  const pending = t.run('guard(() => upload("projects/A/source", {name:"offline.mp4"}), "旧项目上传成功")');
  await t.$('#deleteProjectBtn').onclick(); const poll = t.pollTimers()[0][0];
  oldUpload.resolve(response(project('A')));
  await assert.rejects(pending, error => error.staleProject === true);
  assert.equal(t.run('P.id'), 'B'); assert.equal(t.$('#projectName').textContent, '项目 B');
  assert.equal(t.$('#projectSelect').value, 'B'); assert.equal(t.storage.get('vrc.project'), 'B');
  assert.equal(t.$('#toast').textContent, '项目已删除'); assert.doesNotMatch(t.$('#toast').className, /err/);
  assert.equal(t.pollTimers()[0][0], poll);
});
test('轮询即使切换出去再切回同一项目，也必须忽略旧代响应', async () => {
  const t = await setup(), old = t.hold('GET', 'projects/A'), refresh = t.run('refresh()');
  t.routes.delete('GET projects/A'); await t.run('openProject("B")'); await t.run('openProject("A")');
  const poll = t.pollTimers()[0][0]; old.resolve(response({...project('A'), name: '过期同项目轮询'})); await refresh;
  assert.equal(t.run('P.name'), '项目 A'); assert.equal(t.$('#projectName').textContent, '项目 A');
  assert.equal(t.pollTimers()[0][0], poll, 'an obsolete refresh must not replace the active poll timer');
});
test('openProject 切换出去再切回时，较早响应不能覆盖新视图', async () => {
  const t = await setup(), old = t.hold('GET', 'projects/A'), opening = t.run('openProject("A")');
  t.routes.delete('GET projects/A'); await t.run('openProject("B")'); await t.run('openProject("A")');
  old.resolve(response({...project('A'), name: '过期打开响应'})); await opening;
  assert.equal(t.run('P.name'), '项目 A'); assert.equal(t.$('#projectName').textContent, '项目 A'); assert.equal(t.storage.get('vrc.project'), 'A');
});
test('删除旧项目完成后，不打断用户新选择的项目', async () => {
  const t = await setup(), deleting = t.hold('DELETE', 'projects/A'), pending = t.$('#deleteProjectBtn').onclick(); await Promise.resolve();
  await t.run('openProject("B")'); const poll = t.pollTimers()[0][0];
  t.projects.delete('A'); deleting.resolve(response({deleted: true})); await pending;
  assert.equal(t.run('P.id'), 'B'); assert.equal(t.$('#projectSelect').value, 'B'); assert.equal(t.storage.get('vrc.project'), 'B');
  assert.equal(t.pollTimers()[0][0], poll); assert.equal(t.$('#projectSelect option[value="A"]'), null);
});
test('删除期间已排队的替换检查必须取消，不能在确认后请求项目', async () => {
  const t = await setup(), deleting = t.hold('DELETE', 'projects/A');
  t.run('P.script={tasks:[{id:"seg-01",kind:"speaker",dialogue:"Hi"}]};S=clone(P.script);scheduleReplacementCheck()');
  const checks = [...t.timers.entries()].filter(([, timer]) => timer.ms === 250); assert.equal(checks.length, 1);
  const pending = t.$('#deleteProjectBtn').onclick(); await Promise.resolve();
  assert.equal(t.timers.has(checks[0][0]), false); await checks[0][1].fn();
  assert.equal(t.requests.some(r => r.url.endsWith('/replacement-check')), false);
  t.projects.delete('A'); deleting.resolve(response({deleted: true})); await pending;
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log('PASS ' + name); }
    catch (error) { failed++; console.error('FAIL ' + name + '\n' + error.stack); }
  }
  assert.equal(failed, 0, `${failed} round24.16 scenarios failed`);
  console.log(`Round 24.16 UI: ${tests.length} scenarios passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
