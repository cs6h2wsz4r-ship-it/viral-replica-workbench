/* 24.12: actual renderers and delegated events, with offline saves only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const person='a1028222447-602d',product='a1028222505-83b0',current='img1029174954-7db9';
const old='img1029085721-2d86',composite='img1029155737-f654';
// Read-only reconstruction of p1028222359-9b21's 14 assets; no live project dependency.
const image=(id,label,extra={})=>({id,type:'image',label,file:`assets/${id}.png`,...extra});
const assets=[
  image(person,'人物照',{roles:['person_identity']}),image(product,'商品图',{roles:['product_truth']}),
  image('a1029085450-dc73','原片参考帧 穿裙女子 0.5s',{roles:['scene'],camera_scene:'S1'}),
  image(old,'主镜头图 C1 · S1 停车场与打开的后备箱',{roles:['target_state'],generated:true,camera_character:'C1',camera_scene:'S1'}),
  image('a1029144955-da41','原片参考帧 穿裙女子 3.4s',{roles:['scene'],internal:true,camera_scene:'S1'}),
  image('a1029144955-b77c','去字幕参考帧 3.36s',{roles:['scene'],internal:true,derived_from:'a1029144955-da41',camera_scene:'S1'}),
  image('a1029145117-bd57','原片参考帧 穿裙女子 0.1s',{roles:['scene'],internal:true,camera_scene:'S1'}),
  image('a1029145117-2db9','去字幕参考帧 0.15s',{roles:['scene'],internal:true,derived_from:'a1029145117-bd57',camera_scene:'S1'}),
  image(composite,'合成图',{roles:['target_state'],generated:true,camera_character:'C1',camera_scene:'S1'}),
  image('a1029171723-d255','原片参考帧 穿裙女子 0.0s',{roles:['scene'],internal:true,camera_scene:'S1'}),
  image('a1029171723-a156','去字幕参考帧 0.00s',{roles:['scene'],internal:true,derived_from:'a1029171723-d255',camera_scene:'S1'}),
  image('a1029174942-007c','原片参考帧 穿裙女子 5.0s',{roles:['scene'],internal:true,camera_scene:'S1'}),
  image('a1029174942-d592','去字幕参考帧 5.03s',{roles:['scene'],internal:true,derived_from:'a1029174942-007c',camera_scene:'S1'}),
  image(current,'主镜头图 C1 · S1 停车场与打开的后备箱',{roles:['target_state'],generated:true,camera_character:'C1',camera_scene:'S1'}),
];
const frames=assets.filter(a=>a.internal || a.label.startsWith('原片参考帧')).map(a=>a.id);
function setup(change=()=>{}){
  const project={id:'offline-24-12',assets:structuredClone(assets),runs:{},images:{},replacement_checks:{},
    breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'停车场',timeRanges:[[0,10]]}]},
    replacements:{characters:[{source_id:'C1',action:'replace',asset_id:person,camera_assets:{S1:current}}],product:{action:'replace',asset_id:product}},
    script:{tasks:[{id:'seg-01',kind:'speaker',mode:'video',title:'第一段',source_start:0,source_end:10,duration:10,ratio:'9:16',
      action:'Use @image1 with @image2 and @image3.',image_ids:[current,person,product]}]}};
  change(project);
  const document=createDocument(),events={},requests=[];
  Object.defineProperty(Node.prototype,'outerHTML',{configurable:true,set(html){const template=document.createElement('template');template.innerHTML=html;this.replaceWith(template.firstChild);}});
  document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const context=vm.createContext({document,console,project,structuredClone,setTimeout:()=>1,clearTimeout(){},
    localStorage:{getItem(){},setItem(){}},fetch(){throw Error('禁止网络');}});
  const run=code=>vm.runInContext(code,context),$=selector=>document.querySelector(selector);
  run(source.slice(0,source.indexOf('// ---------- settings ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  context.mockApi=async(method,url,body)=>{
    assert.equal(method,'PUT');assert.equal(url,`projects/${project.id}/script`);
    requests.push(structuredClone(body));project.script=structuredClone(body.script);return structuredClone(project);
  };
  run('api=mockApi;P=clone(project);STATUS={};renderScript(true)');
  const click=async(target)=>{assert.ok(target,'click target exists');for(const fn of events.click || [])await fn({target,preventDefault(){}});};
  const ids=node=>[...node.querySelectorAll('[data-chip]')].map(el=>el.dataset.chip);
  return {run,$,click,requests,project,card:()=>$('[data-task="seg-01"]'),fold:()=>$('[data-task="seg-01"] [data-detail-key="other-camera-images"]'),
    defaults:()=>ids($('[data-task="seg-01"] .chips')),ids,
    selected:()=>JSON.parse(run('JSON.stringify(S.tasks[0].image_ids)'))};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
test('14 张素材：默认仅三张已选，九张内部帧完全排除，两张旧版本默认折叠',async()=>{
  assert.equal(assets.length,14);assert.equal(frames.length,9);
  const t=setup();assert.deepEqual(t.defaults(),[current,person,product]);
  assert.ok(t.fold());assert.equal(t.fold().open,false);assert.equal(t.fold().querySelector('summary').textContent,'显示其他版本的主镜头图（2）');
  assert.deepEqual(t.ids(t.fold()),[old,composite]);
  for(const id of frames)assert.equal(t.card().querySelector(`[data-chip="${id}"]`),null);
  assert.doesNotMatch(t.card().textContent,/原片参考帧|去字幕参考帧/);
  assert.deepEqual([...t.card().querySelectorAll('.n')].map(n=>n.textContent),['图1','图2','图3']);
  assert.match(t.card().textContent,/按点击顺序编号为图1、图2…，最多 4 张/);
  assert.match(t.card().textContent,/只列出本段可能用到的图；内部截帧不会出现在这里/);
  const selected=t.selected();t.fold().open=true;await t.click(t.fold().querySelector('summary'));t.fold().open=false;
  assert.deepEqual(t.selected(),selected);assert.equal(t.run('dirty.script'),false,'native disclosure never edits script');
  await t.click(t.$('#saveScript'));
  assert.deepEqual(t.requests[0].script.tasks[0].image_ids,[current,person,product]);
  assert.equal(t.requests[0].script.tasks[0].action,'Use @image1 with @image2 and @image3.');
  assert.equal(Object.hasOwn(t.requests[0],'reference_orders'),false,'existing save payload is unchanged');
});
test('候选顺序：已选、每个替换人物本场景在用图、人物/商品、上传图；别的场景和旧记录折叠',()=>{
  const t=setup(p=>{
    p.script.tasks[0].image_ids=['upload'];
    p.assets.unshift(image('upload','用户上传'),image('extra','其他上传图'),image('person2','人物二',{roles:['person_identity']}),
      image('camera2','人物二在用图',{generated:true,camera_character:'C2',camera_scene:'S1'}),
      image('other-scene','另一场景在用图',{generated:true,camera_character:'C1',camera_scene:'S2'}),
      image('record-only','旧主镜头（无素材机位字段）',{generated:true,roles:['target_state']}),
      image('derived-only','去字幕参考帧（无 internal 标记）',{derived_from:person}),
      {id:'audio',type:'audio',label:'音频',file:'a.wav'});
    p.images.legacy={state:'completed',asset_id:'record-only',for_task:'camera:C1:S1'};
    p.camera_current={C1:{S1:current,S2:'other-scene'},C2:{S1:'camera2'}};
    p.replacements.characters.push({source_id:'C2',action:'replace',asset_id:'person2',camera_assets:{S1:'camera2'}});
  });
  assert.deepEqual(t.defaults(),['upload',current,'camera2',person,'person2',product,'extra']);
  assert.deepEqual(t.ids(t.fold()),['other-scene','record-only',old,composite]);
  assert.match(t.fold().querySelector('summary').textContent,/（4）/);
  assert.equal(t.card().querySelector('[data-chip="derived-only"]'),null);
  assert.equal(t.card().querySelector('[data-chip="audio"]'),null);
  t.fold().open=true;t.run('renderTasks(true)');assert.equal(t.fold().open,true,'keyed rerender preserves disclosure state');
});
test('已选内部/衍生/旧版/任意类型仍按图号显示，未选内部候选永不泄漏',async()=>{
  const internal=frames[0],derived=frames[2];
  const t=setup(p=>{
    p.script.tasks[0].image_ids=[internal,derived,old,'audio'];
    p.assets.push({id:'audio',type:'audio',label:'已存的特殊参考',file:'a.wav'});
    // Even a bad replacement/current binding must never expose an unselected internal frame.
    p.camera_current={C1:{S1:frames[1]}};
    p.replacements.product.asset_id=frames[3];
  });
  assert.deepEqual(t.defaults().slice(0,4),[internal,derived,old,'audio']);
  assert.deepEqual([...t.card().querySelectorAll('.n')].map(n=>n.textContent),['图1','图2','图3','图4']);
  assert.deepEqual(t.ids(t.fold()),[composite,current]);assert.match(t.fold().textContent,/（2）/);
  for(const id of frames.filter(id=>![internal,derived].includes(id)))assert.equal(t.card().querySelector(`[data-chip="${id}"]`),null);
  await t.click(t.$('#saveScript'));assert.deepEqual(t.requests[0].script.tasks[0].image_ids,[internal,derived,old,'audio']);
  await t.click(t.card().querySelector(`[data-chip="${internal}"] img`));
  assert.equal(t.card().querySelector(`[data-chip="${internal}"]`),null,'deselected internal image vanishes');
  assert.deepEqual(t.selected(),[derived,old,'audio']);
});
test('展开点选旧图加入末尾、最多四张、取消后计数和图号正确，折叠状态不进保存字段',async()=>{
  const t=setup();t.fold().open=true;
  await t.click(t.fold().querySelector(`[data-chip="${old}"] img`));
  assert.deepEqual(t.selected(),[current,person,product,old]);
  assert.deepEqual(t.defaults(),[current,person,product,old]);
  assert.deepEqual(t.ids(t.fold()),[composite]);assert.match(t.fold().textContent,/（1）/);
  t.fold().open=true;await t.click(t.fold().querySelector(`[data-chip="${composite}"]`));
  assert.deepEqual(t.selected(),[current,person,product,old],'fifth reference is still refused');
  t.fold().open=false;await t.click(t.$('#saveScript'));
  assert.deepEqual(t.requests[0].script.tasks[0],t.project.script.tasks[0],'save contains script data only');
  assert.deepEqual(t.requests[0].script.tasks[0].image_ids,[current,person,product,old]);
  await t.click(t.card().querySelector(`[data-chip="${old}"]`));
  assert.deepEqual(t.selected(),[current,person,product]);assert.match(t.fold().textContent,/（2）/);
  assert.deepEqual([...t.card().querySelectorAll('.n')].map(n=>n.textContent),['图1','图2','图3']);
});
test('没有其他版本时不显示空折叠，失效引用仍可移除，缺字段旧项目可渲染',async()=>{
  const t=setup(p=>{p.assets=p.assets.filter(a=>![old,composite].includes(a.id));p.script.tasks[0].image_ids=['missing',current,person];});
  assert.equal(t.fold(),null);assert.deepEqual(t.defaults(),[current,person,product]);
  assert.deepEqual([...t.card().querySelectorAll('.n')].map(n=>n.textContent),['图2','图3']);
  await t.click(t.card().querySelector('[data-chip="missing"]'));assert.deepEqual(t.selected(),[current,person]);
  const empty=setup(p=>{delete p.assets;delete p.replacements;delete p.images;delete p.breakdown;p.script.tasks[0].image_ids=[];});
  assert.deepEqual(empty.defaults(),[]);assert.equal(empty.fold(),null);assert.match(empty.card().textContent,/素材库里还没有图片/);
});
(async()=>{for(const [name,fn] of tests){await fn();console.log('PASS: '+name);}console.log(`PASS: 24.12 ${tests.length} offline UI scenarios`);})().catch(error=>{console.error(error);process.exitCode=1;});
