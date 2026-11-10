/* Round 24.3: real render/events, temporary project copy, deterministic mock I/O. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,installPageConfirmationHarness}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,process.env.ROUND24_3_SOURCE || 'web/app.js'),'utf8');
const copied=path.join(root,'runtime/temp/round24_3-ui-project.json');
fs.copyFileSync(path.join(root,'tools/fixtures/round24/p1028222359-9b21/project.json'),copied);
const issue=(code,step,extra={})=>({code,step,title:code,fix:code,details:[],...extra});
const stale=issue('camera_image_stale','03',{character_id:'C1',scene_id:'S1',asset_id:'old',reference_index:1,details:[
 'seg-01 的图1 指向一张已删除或过期的主镜头图','seg-01 的图1 指向一张已删除或过期的主镜头图；主镜头图的替换依据已变化',
 '主镜头图的替换依据已变化，请按当前人物和商品重新生成主镜头图，再重新生成新脚本。',
 'seg-01 在场景 S1（停车场与打开的后备箱），还缺 C1 在这个场景的主镜头图','seg-01','替换设定在生成脚本之后改过，请重新生成新脚本']});
const check=issues=>({errors:issues.map(i=>i.title),issues,warnings:[]});
const A=check([stale,issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1'}),issue('invalid_camera_image','03',{character_id:'C1',scene_id:'S1'}),issue('script_stale','04')]);
const B=check([issue('camera_image_stale','03',{character_id:'C1',scene_id:'S1',details:stale.details}),issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1',details:['seg-01 在场景 S1（停车场与打开的后备箱），还缺 C1 在这个场景的主镜头图','seg-01']})]);
const completedCamera={old:{id:'old',asset_id:'old',state:'completed',for_task:'camera:C1:S1'}};
function setup(initial=A, overrides={}) {
 const document=createDocument(),events={},timers=new Map(),requests=[];let n=0;
 document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
 document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
 const p=JSON.parse(fs.readFileSync(copied));
 Object.assign(p,{id:'round24-3',source:{},analysis:{},jobs:{},runs:{},log:[],images:{},camera_stale_assets:['old'],
 assets:[{id:'person',type:'image',roles:['person_identity'],label:'新人物',file:'person.png'},
 {id:'product',type:'image',roles:['product_truth'],label:'ChatGPT 图像 新商品',file:'product.png'},
 {id:'old',type:'image',roles:['target_state'],label:'旧主镜头图',file:'old.png',camera_character:'C1',camera_scene:'S1'}],
 breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'停车场与打开的后备箱',timeRanges:[[0,8]]}],shots:[{start:0,end:4,characters:['C1']}]},
 replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person',description:''}],product:{action:'keep',asset_id:'product',description:''},scene:''},
 script:{model:'mock',tasks:[{id:'seg-01',kind:'speaker',mode:'video',source_start:0,source_end:4,duration:4,ratio:'9:16',image_ids:['old','product'],dialogue:'Hello',action:'Smile'}]},replacement_checks:{'seg-01':initial}});
 Object.assign(p,structuredClone(overrides));
 const state={result:initial,hold:null};
 const context=vm.createContext({document,console,structuredClone,project:p,confirm:()=>true,
 setTimeout:(fn,ms)=>{timers.set(++n,{fn,ms});return n;},clearTimeout:id=>timers.delete(id),localStorage:{getItem(){},setItem(){}}});
 const run=s=>vm.runInContext(s,context);
 run(source.slice(0,source.indexOf('// ---------- settings ----------')));
 const start=source.indexOf('document.addEventListener("click", async (e) => {');
 run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
 context.mockApi=async(method,url,body)=>{
  requests.push({method,url,body:structuredClone(body)});
  if(url.endsWith('/replacement-check')) {if(state.hold)await state.hold;return Object.fromEntries((body?.tasks || p.script.tasks).map(t=>[t.id,structuredClone(state.result)]));}
  if(url.endsWith('/replacements')) {p.replacements=structuredClone(body);return structuredClone(p);}
  if(url.endsWith('/camera-image/use')) {p.assets.push({id:'new',type:'image',roles:['target_state'],file:'new.png',camera_character:'C1',camera_scene:'S1'});p.replacements.characters[0].camera_assets={S1:'new'};p.script.tasks[0].image_ids[0]='new';p.camera_stale_assets=[];p.camera_selection={message:'已改用'};return structuredClone(p);}
  if(method==='DELETE' && url.includes('/assets/')) {p.assets=p.assets.filter(a=>a.id!==url.split('/').at(-1));return structuredClone(p);}
  if(url.endsWith('/script') && method==='POST') {p.script.replacements_fingerprint='new script';return structuredClone(p);}
  if(url.endsWith('/analyze') && method==='POST') {p.analysis={status:'done',revision:2};return structuredClone(p);}
  if(url.endsWith('/script') && method==='PUT') {p.script=structuredClone(body.script);return structuredClone(p);}
  if(method==='GET')return structuredClone(p);
  throw Error(`Unexpected mock request ${method} ${url}`);
 };
 run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={providers:{}};renderAll()');
 const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
 const fire=async(name,el)=>{assert.ok(el);for(const f of events[name] || [])await f({target:el,preventDefault(){}});};
 installPageConfirmationHarness({run,context,document,dispatch:el=>fire('click',el)});
 const flush=async()=>{for(const [id,t] of [...timers])if(t.ms<=250){timers.delete(id);await t.fn();}};
 return {run,$,$$,state,p,requests,flush,fire,click:el=>fire('click',el),guide:()=>$('[data-replacement-check="seg-01"]'),timers};
}
const tests=[
 ...['missing_camera_image','invalid_camera_image'].map(code=>[`${code}：已失效的历史主镜头图与身体动作各说真实原因`,async()=>{
  const t=setup(check([issue(code,'03',{character_id:'C1',scene_id:'S1'}),issue('performance_has_motion','04',{details:['脚本里有 1 处身体动作描述']})]),{images:completedCamera});
  t.run('P.replacements.product.action="replace";renderTasks(true)');
  const rows=t.$$('.fix-step');assert.equal(rows.length,2);
  assert.match(rows[0].querySelector('.fix-heading').textContent,/重新生成 S1 的主镜头图/);
  assert.equal(rows[0].querySelector('.hint').textContent,'为什么：之前的主镜头图是按旧的人物或商品设定生成的，现在已失效');
  assert.equal(rows[1].querySelector('.hint').textContent,'为什么：脚本重复写了原片的身体动作，需要按当前规则重新生成');
  assert.equal(rows[0].querySelector('button').dataset.todoTarget,'camera');
  assert.equal(rows[1].querySelector('button').dataset.todoTarget,'script');
  assert.doesNotMatch(t.guide().textContent,/先完成前面的设定，再按新设定生成脚本和参考图/);
 }]),
 ['从没生成过当前人物×场景时明确提示生成',async()=>{
  for(const code of ['missing_camera_image','invalid_camera_image']) {
   const t=setup(check([issue(code,'03',{character_id:'C1',scene_id:'S1'})]),{images:{
    otherScene:{id:'otherScene',for_task:'camera:C1:S2',state:'completed'},
    otherPerson:{id:'otherPerson',for_task:'camera:C2:S1',state:'completed'},
    failed:{id:'failed',for_task:'camera:C1:S1',state:'failed'}
   }});
   const rows=t.$$('.fix-step');assert.equal(rows.length,1);
   assert.equal(rows[0].querySelector('.fix-heading').textContent,'1. 现在做 · 第 03 步：生成 S1 的主镜头图');
   assert.equal(rows[0].querySelector('.hint').textContent,'为什么：这一段要换人，这个场景还没有主镜头图');
  }
 }],
 ['历史图被删除与文件缺失分别说明，过期证据优先',async()=>{
  for(const code of ['missing_camera_image','invalid_camera_image']) {
   const data=check([issue(code,'03',{character_id:'C1',scene_id:'S1'})]);
   const deleted=setup(data,{images:completedCamera,camera_stale_assets:[]});
   deleted.run('P.assets=P.assets.filter(a=>a.id!=="old");renderTasks(true)');
   assert.equal(deleted.$('.fix-step .hint').textContent,'为什么：之前的主镜头图已从素材库删除');
   assert.match(deleted.$('.fix-heading').textContent,/重新生成 S1/);
   const missing=setup(data,{images:completedCamera,camera_stale_assets:[],missing_asset_files:['old']});
   assert.equal(missing.$('.fix-step .hint').textContent,'为什么：之前的主镜头图是按旧的人物或商品设定生成的，现在已失效');
   const mixed=setup(data,{images:completedCamera});
   mixed.run('P.images.deleted={id:"deleted",asset_id:"deleted",for_task:"camera:C1:S1",state:"completed"};renderTasks(true)');
   assert.equal(mixed.$('.fix-step .hint').textContent,'为什么：之前的主镜头图是按旧的人物或商品设定生成的，现在已失效');
  }
 }],
 ['合并脚本原因保留、去重并限制两条，参考图原因只作兜底',async()=>{
  const root=issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1'});
  for(const codes of [[],['performance_has_motion'],['performance_has_motion','anchor_order'],['performance_has_motion','performance_has_motion','anchor_order','motion_additions']]) {
   const t=setup(check([root,issue('invalid_image','05'),...codes.map(code=>issue(code,'04'))]));
   const reasons=[...new Set(codes)].filter(code=>code!=='motion_additions').map(code=>t.run(`ISSUE_TODOS[${JSON.stringify(code)}].reason`));
   const expected=reasons.length ? reasons.slice(0,2).join('；')+(reasons.length>2?'等':'') : '主镜头图更新后，重新生成脚本会自动带上新的参考图';
   assert.equal(t.$$('.fix-step').length,2);
   assert.equal(t.$$('.fix-step')[1].querySelector('.hint').textContent,`为什么：${expected}`);
  }
 }],
 ['主镜头根因合并脚本过期原因：独立 code 或技术明细均保留',async()=>{
  for(const detailOnly of [false,true]) {
   const root=issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1',details:detailOnly?['替换设定在生成脚本之后改过，请重新生成新脚本']:[]});
   const t=setup(check([root,issue('performance_has_motion','04'),...(!detailOnly?[issue('script_stale','04')]:[])]));
   const reasons=t.$$('.fix-step')[1].querySelector('.hint').textContent.replace('为什么：','').split('；');
   assert.equal(reasons.length,2);
   assert.ok(reasons.includes('替换设定在生成脚本之后改过'));
   assert.ok(reasons.includes('脚本重复写了原片的身体动作，需要按当前规则重新生成'));
  }
  const t=setup(check([issue('camera_image_stale','03',{character_id:'C1',scene_id:'S1'})]));
  assert.equal(t.$$('.fix-step').length,2);
  assert.equal(t.$$('.fix-step')[1].querySelector('.hint').textContent,'为什么：主镜头图更新后，重新生成脚本会自动带上新的参考图');
 }],
 ['保存保持→替换不用刷新（相同表单 key 的缓存复现）',async()=>{
  const t=setup(check([issue('product_mismatch','03')]));await t.flush();
  const input=t.$('[data-cast-action="product"]');input.value='replace';await t.fire('input',input);
  // Draft and saved request inputs can be identical; a successful save must still recheck persisted facts.
  await t.flush();const before=t.requests.filter(r=>r.url.endsWith('/replacement-check')).length;
  t.state.result=check([]);await t.run('saveReplacements()');await t.flush();
  assert.equal(t.requests.filter(r=>r.url.endsWith('/replacement-check')).length,before+1,'successful save must invalidate same-input cached checks');
  assert.doesNotMatch(t.guide().textContent,/把第 03 步的商品处理/);
  assert.match(t.$('#toast').textContent,/已完成：/);
 }],
 ...[['情形 A',A],['情形 B',B]].map(([name,data])=>[name,async()=>{
  const t=setup(data),rows=t.$$('.fix-step');
  assert.equal(rows.length,2);assert.match(t.guide().textContent,/seg-01 预览前还要做 2 件事/);
  assert.match(rows[0].textContent,/1\. 现在做.*第 03 步.*重新生成 S1 的主镜头图/s);
  assert.match(rows[1].textContent,/2\. 之后做.*第 04 步.*重新生成新脚本/s);
  assert.equal(rows[0].querySelector('button').dataset.todoTarget,'camera');
  assert.equal(rows[0].querySelector('button').dataset.fixCharacter,'C1');
  assert.equal(rows[0].querySelector('button').dataset.fixScene,'S1');
  assert.equal(rows[1].querySelector('button').dataset.todoTarget,'script');
  const detail=t.guide().querySelector('details');assert.equal(detail.open,false);assert.match(detail.textContent,/技术明细/);
  assert.equal(t.$$('li').filter(el=>el.textContent.trim()==='seg-01').length,0);
  assert.equal(t.$$('.fix-step button').length,2);
 }]),
 ['SB1 顶部五步不再导航到主镜头图',async()=>{const t=setup();assert.equal(t.$('[data-camdraft]'),null);assert.equal(t.$$('.workflow-progress a').length,5);} ],
 ['保留旧内容变灰、完成消息、改用/生成完成重查',async()=>{
  const t=setup();await t.flush();t.state.result=check([]);
  let release;t.state.hold=new Promise(r=>release=r);
  await t.run('useSceneCamera("C1","S1","new")');
  assert.match(t.guide().textContent,/正在检查…/);assert.match(t.guide().textContent,/重新生成 S1/);
  assert.equal(t.guide().getAttribute('aria-busy'),'true');
  const done=t.flush();release();await done;t.state.hold=null;
  assert.match(t.guide().textContent,/检查通过/);assert.equal(t.guide().getAttribute('aria-busy'),'false');
  t.p.images.generated={id:'generated',asset_id:'new',state:'completed',for_task:'camera:C1:S1'};
  const count=t.requests.length;await t.run('refresh()');await t.flush();assert.ok(t.requests.length>count+1);
 }],
 ['多人物场景根因独立、同一人物场景去重',async()=>{
  const t=setup();
  const issues=[issue('camera_image_stale','03',{character_id:'C1',scene_id:'S1'}),issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1'}),issue('missing_camera_image','03',{character_id:'C2',scene_id:'S2'}),issue('anchor_order','04'),issue('script_stale','04')];
  const todos=t.run(`fixGroups(${JSON.stringify(check(issues))},S.tasks[0])`);
  assert.deepEqual(Array.from(todos,x=>x.key),['camera:C1:S1','camera:C2:S2','script']);
  const missing=check([issue('person_replace_missing_image','05',{affected:[{character_id:'C1',asset_id:'person'},{character_id:'C2',asset_id:'person2'}]})]);
  const people=t.run(`fixGroups(${JSON.stringify(missing)},S.tasks[0])`);assert.equal(people.length,2);assert.match(people[1].action,/C2/);
 }],
 ['已有主镜头图只需换图，不误判为生成脚本',async()=>{
  const t=setup(check([issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1',expected_asset_id:'current'})]));
  const rows=t.$$('.fix-step');assert.equal(rows.length,1);assert.match(rows[0].textContent,/换成 S1 当前的主镜头图/);
  assert.equal(rows[0].querySelector('button').dataset.todoTarget,'references');
  assert.equal(t.$$('.workflow-progress a').length,5);
 }],
 ['空参考图只列具体选图动作，单次选主镜头图的提示不重复',async()=>{
  const t=setup(check([issue('no_images','05'),issue('person_replace_missing_image','05',{character_id:'C1'}),issue('missing_camera_image','03',{character_id:'C1',scene_id:'S1',expected_asset_id:'current'})]));
  assert.equal(t.$$('.fix-step').length,1);assert.match(t.$('.fix-step').textContent,/S1 当前的主镜头图/);
 }],
 ['SB1 顶栏不会自动确认旧生图',async()=>{const t=setup();Object.getPrototypeOf(t.$('#workflowNext')).scrollIntoView=function(){};await t.click(t.$('#workflowNext'));assert.equal(t.requests.filter(r=>r.url.endsWith('/confirm')).length,0);} ],
 ['保存、删除、脚本、参考图、拆解完成后重查所有分段',async()=>{
  for(const action of ['保存脚本','生成脚本','删除素材','修改参考图','重新拆解','上传素材']) {
   const t=setup();t.run('P.script.tasks.push({...clone(P.script.tasks[0]),id:"seg-02"});project.script=clone(P.script);renderScript(true)');
   await t.flush();const before=t.requests.filter(r=>r.url.endsWith('/replacement-check')).length;
   t.state.result=check([]);
   if(action==='保存脚本')await t.run('saveScript(true)');
   if(action==='生成脚本')await t.run('submitScript()');
   if(action==='删除素材')await t.click(t.$('[data-delasset="old"]'));
   if(action==='修改参考图')await t.click(t.$('[data-task="seg-01"] [data-chip="person"]'));
   if(action==='重新拆解')await t.run('submitAnalyze()');
   if(action==='上传素材') {
    // Execute the actual upload callback with a frozen transport, no browser or socket.
    const start=source.indexOf('bindDrop($("#assetInput").closest("label")');
    const end=source.indexOf('bindDrop($("#audioInput").closest("label")',start);
    t.run('bindDrop=(label,input,handler)=>{globalThis.uploadHandler=handler};upload=async()=>{project.assets.push({id:"uploaded",type:"image",file:"uploaded.png",roles:["product_truth"]})}');
    t.run(source.slice(start,end));await t.run('uploadHandler([{name:"product-new.png"}])');
    await Promise.resolve();await Promise.resolve();
   }
   await t.flush();
   const req=t.requests.filter(r=>r.url.endsWith('/replacement-check'));assert.ok(req.length>before,action);
   assert.deepEqual(req.at(-1).body.tasks.map(t=>t.id),['seg-01','seg-02'],action);
   assert.ok(t.$$('[data-replacement-check]').every(el=>/检查通过/.test(el.textContent)),action);
  }
 }],
 ['失效素材与图1顺序有可操作控件',async()=>{
  const t=setup(check([issue('invalid_image','05')]));
  t.run('S.tasks[0].image_ids=["deleted","product","person"];P.script=clone(S);P.replacement_checks={"seg-01":{errors:["失效图片","图1错误"],issues:[{code:"invalid_image",step:"05"},{code:"image1_not_person",step:"05"}]}};renderTasks(true)');
  const removal=t.$('[data-task="seg-01"] [data-chip="deleted"]');assert.ok(removal,'deleted selected reference must be removable');
  await t.click(removal);assert.equal(t.run('S.tasks[0].image_ids.includes("deleted")'),false);
  const reorder=t.$('[data-fix-image1="seg-01"]');assert.ok(reorder,'image1 reorder control must remain usable after navigating');
  await t.click(reorder);assert.equal(t.run('S.tasks[0].image_ids[0]'),'person');
 }],
 ['检查结果相同也会结束加载、失败保留旧内容',async()=>{
  const t=setup();await t.flush();t.run('scheduleReplacementCheck(true)');await t.flush();
  assert.doesNotMatch(t.guide().textContent,/正在检查…/);assert.equal(t.guide().getAttribute('aria-busy'),'false');
  t.run('api=async()=>{throw Error("mock 检查失败")};scheduleReplacementCheck(true)');await t.flush();
  assert.match(t.guide().textContent,/mock 检查失败/);assert.match(t.guide().textContent,/重新生成 S1/);
  assert.equal(t.guide().getAttribute('aria-busy'),'false');
 }],
 ['无旧按钮名、每个 code 有动作与目标；真实二选一说明',async()=>{
  const t=setup();assert.doesNotMatch(t.guide().textContent,/起草主镜头图|准备主镜头图/);
  for(const code of ['analysis_no_caption_bbox','person_keep_with_image','person_image_mismatch','product_mismatch','camera_image_stale','missing_camera_image','script_stale','performance_has_motion','anchor_order','motion_additions','appearance_conflict','reference_mapping','missing_dialogue','no_images','invalid_image','product_missing_image','person_replace_missing_image','image1_not_person','video_reference_required','invalid_camera_image','camera_scene_mismatch','dialogue_conflict']) {
   t.run(`renderedHtml.delete(document.querySelector('[data-replacement-check]'));updateHtml(document.querySelector('[data-replacement-check]'),replacementCheckHtml(${JSON.stringify(check([issue(code,'05',{details:[code]})]))},S.tasks[0]))`);
   if(code==='motion_additions'){assert.equal(t.$('.fix-step'),null);assert.ok(t.$('[data-motion-edit]'));assert.ok(t.$('[data-motion-dismiss]'));continue;}
   const row=t.$('.fix-step');assert.ok(row,code);assert.ok(row.querySelector('button').dataset.todoTarget,code);assert.match(row.textContent,/为什么：/);
  }
  const p=setup(check([issue('product_mismatch','03')]));assert.match(p.$('.fix-step').textContent,/ChatGPT 图像 新商品/);assert.equal(p.$$('.fix-step button').length,2);assert.match(p.$('.fix-step').textContent,/保留原片商品/);
 }],
];
let completed=false;process.once('beforeExit',()=>{if(!completed){console.error('FAIL: Round 24.3 did not finish all scenarios');process.exitCode=1;}});
(async()=>{let failed=0;for(const [name,fn]of tests){try{await fn();console.log('PASS:',name);}catch(e){failed++;console.error('FAIL:',name,e.message);}}completed=true;if(failed)process.exitCode=1;else console.log(`Round 24.3 UI: ${tests.length} scenarios passed`);})().catch(e=>{console.error(e);process.exitCode=1;});
