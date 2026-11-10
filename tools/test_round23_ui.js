/* Round 23: actual renderers, delegated clicks and polling; all transport mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createDocument} = require('./ui_test_dom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const page = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const usage = (at, amount) => ({at, channel:'official', kind:'reason', ok:true, billing:{unit:'tokens', amount}});

function setup() {
  const document = createDocument(), events = {}, requests = [], timers = new Map();
  let timerId = 0, serial = 0;
  document.body.innerHTML = page.replace(/<!doctype[^>]*>/gi, '').replace(/<script[\s\S]*?<\/script>/g, '');
  document.addEventListener = (name, fn) => (events[name] ||= []).push(fn);
  const project = {id:'offline-round23', name:'离线测试', assets:[],
    breakdown:{characters:[{id:'C1', name:'人物'}], scene:[{id:'S1', name:'场景'}]},
    replacements:{characters:[{source_id:'C1', action:'replace'}], product:{action:'keep'}},
    images:{old:{id:'old', for_task:'camera:C1:S1', state:'pending_confirmation', channel:'official',
      image_ids:['old-frame'], preview:{prompt:'A'}}},
    usage:[usage('10:00:00', 6433)], script:{tasks:[]}};
  const context = vm.createContext({document, console, project, structuredClone,
    setTimeout:(fn, ms) => {timers.set(++timerId, {fn, ms}); return timerId;},
    clearTimeout:id => timers.delete(id), localStorage:{getItem(){}, setItem(){}}});
  const run = code => vm.runInContext(code, context), $ = s => document.querySelector(s);
  run(source.slice(0, source.indexOf('// ---------- settings ----------')));
  const start = source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start, source.indexOf('\n$("#newProjectBtn").onclick', start)));
  const state = {beforeDraft(){}, failPreview:false};
  context.mockApi = async (method, url, body) => {
    requests.push({method, url, body:structuredClone(body)});
    if (method === 'GET' && url === `projects/${project.id}`) return structuredClone(project);
    if (method === 'POST' && url.endsWith('/camera-image/draft')) {
      state.beforeDraft(); project.usage.push(usage('10:01:00', 6607));
      return {prompt:'B', image_ids:['clean-frame'], for_task:'camera:C1:S1', camera_binding:'new-binding', ratio:'9:16'};
    }
    if (method === 'POST' && url.endsWith('/images/preview')) {
      if (state.failPreview) throw Error('mock preview failure');
      const rec = {id:`new-${++serial}`, state:'pending_confirmation', channel:body.channel,
        for_task:body.for_task, image_ids:body.image_ids, camera_binding:body.camera_binding, preview:{prompt:body.prompt}};
      project.images[rec.id] = rec; return structuredClone(rec);
    }
    throw Error(`Unexpected offline API: ${method} ${url}`);
  };
  run('api=mockApi; P=clone(project); S=clone(P.script); STATUS={default_image_channel:"official"}; renderAll()');
  const click = async el => {
    assert.ok(el, 'click target exists');
    for (const fn of events.click || []) await fn({target:el, preventDefault(){}});
  };
  const poll = async () => {
    const entry = [...timers].find(([, t]) => [3000, 15000].includes(t.ms));
    assert.ok(entry, 'real project polling is scheduled');
    timers.delete(entry[0]); await entry[1].fn();
  };
  return {run, $, click, poll, context, document, project, requests, state,
    preview:() => requests.filter(r => r.url.endsWith('/images/preview')).at(-1)?.body};
}

const tests = [
  ['e: usage changes on next poll; unchanged polls ×3 never patch it', async () => {
    const t = setup();
    t.run('let finalWrites=0; const originalUpdateHtml=updateHtml; updateHtml=(box,...args)=>{if(box.id==="finalView") finalWrites++; return originalUpdateHtml(box,...args);}; schedulePoll()');
    const details = t.$('[data-detail-key="project-usage"]'); details.open = true;
    for (let i = 0; i < 3; i++) await t.poll();
    assert.equal(t.run('finalWrites'), 0);
    t.project.usage.push(usage('10:01:00', 6607)); await t.poll();
    assert.match(details.textContent, /官方 2 次 \/ 约 13040 个 token/);
    assert.equal(t.run('finalWrites'), 1);
    const summary = details.firstChild;
    for (let i = 0; i < 3; i++) await t.poll();
    assert.equal(t.run('finalWrites'), 1);
    assert.equal(t.$('[data-detail-key="project-usage"]'), details);
    assert.equal(details.firstChild, summary); assert.equal(details.open, true);
    t.project.usage[1].at = '10:02:00'; await t.poll();
    assert.match(details.textContent, /10:02:00/); assert.equal(t.run('finalWrites'), 2);
  }],
  ['usage updates even if automatic preview fails', async () => {
    const t = setup(); t.state.failPreview = true;
    await assert.rejects(t.run('draftSceneCamera("C1","S1")'), /mock preview failure/);
    assert.match(t.$('[data-detail-key="project-usage"]').textContent, /官方 2 次 \/ 约 13040 个 token/);
  }],
];
(async () => {
  let failures = 0;
  for (const [name, test] of tests) {
    try {await test(); console.log('PASS:', name);}
    catch (error) {failures++; console.error('FAIL:', name, '\n', error.message.slice(0, 1000));}
  }
  assert.equal(failures, 0, `${failures} round 23 UI checks failed`);
})().catch(error => {console.error(error.message); process.exitCode = 1;});
