/* Round 24.4: real DOM renderer/events and mocked JSON transport; no live I/O. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createDocument} = require('./ui_test_dom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const page = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const backWarning = '这一帧是背影，看不到脸。请在下面换一帧，或点“自动选帧”，然后重新生成预览';

function setup(change = () => {}) {
  const document = createDocument(), events = {}, requests = [];
  document.body.innerHTML = page.replace(/<!doctype[^>]*>/gi, '').replace(/<script[\s\S]*?<\/script>/g, '');
  document.addEventListener = (name, fn) => (events[name] ||= []).push(fn);
  const project = {id:'offline-round24-4', assets:[
    {id:'person', type:'image', file:'person.jpg'},
    {id:'original', type:'image', internal:true, file:'assets/frame-C1-0.00.jpg', frame_at:0, frame_reason_code:'manual'}],
    analysis:{shots:[{start:0, end:10.052, frame:'analysis/frames/shot-001.jpg'}]},
    breakdown:{characters:[{id:'C1', name:'人物'}], scene:[{id:'S1', name:'室内', timeRanges:[[0,10.052]]}],
      shots:[{start:0, end:1, characters:['C1'], action:'背影'}, {start:1, end:10.052, characters:['C1'], action:'正面露脸'}]},
    replacements:{characters:[{source_id:'C1', action:'replace', asset_id:'person'}], product:{action:'keep'}},
    images:{current:{id:'current', state:'pending_confirmation', channel:'official', for_task:'camera:C1:S1',
      frame_asset_id:'original', frame_at:0, frame_reason_code:'manual', frame_warning:backWarning,
      image_ids:['person','original'], preview:{prompt:'当前预览'}}}, script:{tasks:[]}};
  change(project);
  const context = vm.createContext({document, console, project, structuredClone, setTimeout:()=>1, clearTimeout(){},
    localStorage:{getItem(){}, setItem(){}}, confirm:()=>true});
  const run = code => vm.runInContext(code, context);
  run(source.slice(0, source.indexOf('// ---------- settings ----------')));
  const start = source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start, source.indexOf('\n$("#newProjectBtn").onclick', start)));
  let serial = 0;
  context.mockApi = async (method, url, body) => {
    const wire = body === undefined ? undefined : JSON.parse(JSON.stringify(body));
    requests.push({method, url, body:wire});
    if (method === 'GET') return structuredClone(project);
    if (url.endsWith('/camera-image/draft')) {
      const at = wire.at ?? 3.36;
      const frame = {id:`frame-${++serial}`, type:'image', file:`assets/new-frame-${serial}.jpg`, frame_at:at};
      project.assets.push(frame);
      return {prompt:'新预览', for_task:'camera:C1:S1', frame_at:at, frame_asset_id:frame.id,
        frame_reason_code:'manual', frame_warning:'', image_ids:['person',frame.id]};
    }
    if (url.endsWith('/images/preview')) {
      const frame = project.assets.find(a => a.id === wire.image_ids.at(-1));
      const rec = {id:`preview-${++serial}`, state:'pending_confirmation', channel:wire.channel,
        for_task:wire.for_task, frame_asset_id:frame.id, frame_at:frame.frame_at, frame_warning:'',
        frame_reason_code:wire.frame_reason_code, image_ids:wire.image_ids, preview:{prompt:wire.prompt}};
      project.images[rec.id] = rec;
      return structuredClone(rec);
    }
    throw Error(`Unexpected offline API: ${method} ${url}`);
  };
  run('api=mockApi; P=clone(project); S=clone(P.script); STATUS={default_image_channel:"official"}; renderReplacements(true)');
  const $ = selector => document.querySelector(selector);
  const $$ = selector => document.querySelectorAll(selector);
  const fire = async (name, el) => {
    assert.ok(el, `${name} target exists`);
    for (const fn of events[name] || []) await fn({target:el, preventDefault(){}});
  };
  return {run, $, $$, requests, project, click:el => fire('click',el),
    input:async value => {const el = $('[data-camera-at]'); el.value = value; await fire('input',el);},
    draftRequest:() => requests.filter(r => r.url.endsWith('/camera-image/draft')).at(-1)?.body,
    title:() => $('.camera-pair figcaption').textContent,
    source:() => $('.camera-pair img').src};
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
