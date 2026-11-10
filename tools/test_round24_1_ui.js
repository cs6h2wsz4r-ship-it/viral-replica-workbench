/* Round 24.1: real card renderer and delegated handlers, JSON transport mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createDocument} = require('./ui_test_dom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const page = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');

function setup({pending = false, manual = true, generated = false} = {}) {
  const document = createDocument(), events = {}, requests = [];
  document.body.innerHTML = page.replace(/<!doctype[^>]*>/gi, '').replace(/<script[\s\S]*?<\/script>/g, '');
  document.addEventListener = (name, fn) => (events[name] ||= []).push(fn);
  const project = {id:'offline-round24-1', assets:[
    {id:'person', type:'image', roles:['person_identity'], file:'person.jpg'},
    {id:'frame', type:'image', file:'frame.jpg', frame_at:0.1, frame_reason_code:manual ? 'manual' : 'front_product'}],
    analysis:{frames:[{at:0.1, file:'frame.jpg'}, {at:0, file:'zero.jpg'}, {at:2.4, file:'next.jpg'}, {at:10, file:'other-scene.jpg'}]},
    breakdown:{characters:[{id:'C1', name:'人物'}], scene:[{id:'S1', name:'客厅', timeRanges:[[0,8]]}]},
    replacements:{characters:[{source_id:'C1', action:'replace', asset_id:'person'}], product:{action:'keep'}},
    images:{}, script:{tasks:[]}};
  if (generated) {
    project.assets.push({id:'camera', type:'image', roles:['target_state'], camera_character:'C1', camera_scene:'S1', file:'camera.jpg', label:'版本一'});
    project.replacements.characters[0].camera_assets = {S1:'camera'};
    project.images.completed = {id:'completed', for_task:'camera:C1:S1', state:'completed', asset_id:'camera', frame_asset_id:'frame', channel:'official'};
  }
  if (pending) project.images.old = {id:'old', state:'pending_confirmation', for_task:'camera:C1:S1', channel:'official',
    frame_asset_id:'frame', frame_at:0.1, frame_reason_code:manual ? 'manual' : 'front_product', image_ids:['person','frame'], preview:{prompt:'旧预览'}};
  let serial = 0, confirmations = 0;
  const state = {beforeDraft(){}, beforeConfirm(){}, failDraft:false};
  const context = vm.createContext({document, console, project, structuredClone, setTimeout:()=>1, clearTimeout(){},
    localStorage:{getItem(){}, setItem(){}}, confirm:()=>{confirmations++; return true;}});
  const run = code => vm.runInContext(code, context);
  run(source.slice(0, source.indexOf('// ---------- settings ----------')));
  const start = source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start, source.indexOf('\n$("#newProjectBtn").onclick', start)));
  context.mockApi = async (method, url, body) => {
    // Match api() JSON.stringify: undefined properties never reach the backend.
    const wire = body === undefined ? undefined : JSON.parse(JSON.stringify(body));
    requests.push({method, url, body:wire});
    if (method === 'GET') return structuredClone(project);
    if (url.endsWith('/camera-image/draft')) {
      state.beforeDraft(wire);
      if (state.failDraft) throw Error('mock 起草失败');
      const at = wire.at ?? 3.36, reason = wire.at === undefined ? 'front_product' : 'manual';
      const frame = {id:`frame-${++serial}`, type:'image', file:`frame-${serial}.jpg`, frame_at:at, frame_reason_code:reason};
      project.assets.push(frame);
      return {prompt:'新预览提示词', for_task:'camera:C1:S1', frame_at:at, frame_asset_id:frame.id,
        frame_reason_code:reason, image_ids:['person',frame.id], camera_binding:`binding-${serial}`};
    }
    if (url.endsWith('/images/preview')) {
      const rec = {id:`preview-${++serial}`, state:'pending_confirmation', channel:wire.channel,
        for_task:wire.for_task, frame_asset_id:wire.image_ids.at(-1), image_ids:wire.image_ids,
        frame_reason_code:wire.frame_reason_code, preview:{prompt:wire.prompt}};
      project.images[rec.id] = rec; return structuredClone(rec);
    }
    if (url.endsWith('/confirm')) {await state.beforeConfirm(url); return {};}
    throw Error(`Unexpected offline API: ${method} ${url}`);
  };
  run('api=mockApi; P=clone(project); S=clone(P.script); STATUS={default_image_channel:"official"}; renderReplacements(true)');
  const $ = s => document.querySelector(s), card = () => $('.scene-camera');
  const fire = async (name, el) => {
    assert.ok(el, `${name} target exists`);
    for (const fn of events[name] || []) await fn({target:el, preventDefault(){}});
  };
  const click = el => fire('click', el);
  const input = async value => {const el = $('[data-camera-at]'); el.value = value; await fire('input', el);};
  const draftRequest = () => requests.filter(r => r.url.endsWith('/camera-image/draft')).at(-1)?.body;
  return {run, $, card, click, fire, input, project, requests, state, draftRequest,
    confirmations:()=>confirmations, selection:()=>run('cameraFrameSelections.get(cameraDraftKey("C1","S1"))')};
}

const tests=[];
(() => {
  const t=setup();
  assert.equal(t.$('[data-scene-camera]'),null);
  assert.equal(t.$('[data-camera-key]'),null);
  assert.equal(t.$('[data-camdraft]'),null);
  assert.doesNotMatch(t.$('#step-3').textContent,/分场表|主镜头图/);
  assert.ok(t.$('[data-cast-card]'));
  console.log('PASS SB1: 第03步只做选角和商品设定，旧分场与主镜头图不再出现');
})();
