/* Round 24.2: real renderer/events with JSON-only mock transport. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createDocument} = require('./ui_test_dom');
const root = path.resolve(__dirname, '..'), source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
function setup() {
  const document = createDocument(), events = {}, requests = [];
  document.body.innerHTML = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8')
    .replace(/<!doctype[^>]*>/gi, '').replace(/<script[\s\S]*?<\/script>/g, '');
  document.addEventListener = (name, fn) => (events[name] ||= []).push(fn);
  const camera = (id, at) => ({id, type:'image', label:id === 'old' ? '背影图' : '合成图', file:`${id}.png`,
    roles:['target_state'], camera_character:'C1', camera_scene:'S1', frame_at:at});
  const project = {id:'round24-2', source:{}, analysis:{},
    assets:[{id:'person',type:'image',label:'人物',file:'person.png',roles:['person_identity']},camera('old',.5),camera('new',3.36)],
    breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'客厅',timeRanges:[[0,8]]}]},
    replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person',description:'原说明',camera_assets:{S1:'old'}}],product:{action:'keep'},scene:'原布景'},
    // Deliberately insert out of time order; current selection is the older image.
    images:{new:{id:'new',asset_id:'new',state:'completed',for_task:'camera:C1:S1',started_at:1793262000},
      old:{id:'old',asset_id:'old',state:'completed',for_task:'camera:C1:S1',started_at:1793252000}},
    script:{model:'mock',tasks:[{id:'seg-01',kind:'speaker',mode:'video',dialogue:'Hello',duration:4,
      ratio:'9:16',source_start:0,source_end:4,image_ids:['old','person'],action:'Smile.'}]},runs:{},log:[]};
  const state = {fail:false, beforeResponse:async()=>{}};
  const context = vm.createContext({document,console,project,structuredClone,confirm:()=>true,
    setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
  const run = code => vm.runInContext(code, context);
  run(source.slice(0, source.indexOf('// ---------- settings ----------')));
  const start = source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start, source.indexOf('\n$("#newProjectBtn").onclick', start)));
  context.mockApi = async (method,url,body) => {
    const wire = body === undefined ? undefined : JSON.parse(JSON.stringify(body));
    requests.push({method,url,body:wire});
    if (url.endsWith('/camera-image/use')) {
      if (state.fail) throw Error('mock 切换失败');
      project.replacements.characters[0].camera_assets.S1 = wire.asset_id;
      const synced = project.script?.tasks.filter(t=>t.image_ids[0] === 'old').map(t=>t.id) || [];
      for (const task of project.script?.tasks || []) if (synced.includes(task.id)) task.image_ids[0] = wire.asset_id;
      project.camera_notice = {};
      project.camera_selection = {asset_id:wire.asset_id,synced_tasks:synced,
        message:`已改用“合成图”${synced.length ? '，'+synced.join('、')+' 的图1 已同步' : ''}`};
      await state.beforeResponse(); return structuredClone(project);
    }
    if (url.endsWith('/script/camera-repair')) {
      project.script.tasks.find(t=>t.id === wire.task_id).image_ids[0] = project.replacements.characters[0].camera_assets.S1;
      return structuredClone(project);
    }
    if (method === 'GET') return structuredClone(project);
    throw Error(`Unexpected API ${method} ${url}`);
  };
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={default_image_channel:"official",providers:{}};renderReplacements(true);renderScript(true)');
  const $ = sel => document.querySelector(sel), $$ = sel => [...document.querySelectorAll(sel)];
  const fire = async (name,el) => {assert.ok(el,`${name} target exists`); for (const fn of events[name] || []) await fn({target:el,preventDefault(){}});};
  return {run,$,$$,project,state,requests,fire,click:el=>fire('click',el),card:()=>$('.scene-camera')};
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
