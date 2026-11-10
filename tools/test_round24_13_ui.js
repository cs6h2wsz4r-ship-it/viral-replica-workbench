/* 24.13: actual renderers/events; no server, network or project writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
for(const property of ['hidden','disabled'])Object.defineProperty(Node.prototype,property,{
  get(){return this.hasAttribute(property);},set(v){v?this.setAttribute(property,''):this.removeAttribute(property);},configurable:true,
});
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const page=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const overlap='参考帧的字幕区域与人物/商品重叠，已模糊处理，生成后请检查这一块';
function setup(){
  const document=createDocument(),events={},requests=[],copies=[];
  document.body.innerHTML=page.replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const line={start:0,end:2,speaker:'C1',original:'It is flowy.',proposed:'It is knit.',reason:'替换材质'};
  const task={id:'seg-01',title:'第一段',kind:'speaker',mode:'video',language:'en',source_start:0,source_end:8,duration:8,ratio:'9:16',
    image_ids:['camera'],dialogue_choice:'adopt',dialogue:'It is knit.',dialogue_lines:[{start:0,end:2,speaker:'C1',text:'It is knit.'}],
    dialogue_proposal:{lines:[line]},footage_reason:'跟随原片动作',split_reason:'场景切换'};
  const project={id:'offline-24-13',name:'离线测试',source:{},log:[],jobs:{},
    analysis:{frames:[{at:5.03,file:'original.jpg'},{at:6,file:'six.jpg'}],passages:[{id:'seg-01',start:0,end:8,every:.2,summary:'人物展示商品',grids:[]}]},
    breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'客厅',timeRanges:[[0,8]]}]},
    assets:[{id:'person',type:'image',file:'person.jpg',roles:['person_identity']},
      {id:'original',type:'image',internal:true,file:'original.jpg',frame_at:5.03,frame_reason_code:'manual'},
      {id:'clean',type:'image',internal:true,file:'clean.jpg',derived_from:'original',caption_overlap_warning:overlap},
      {id:'camera',type:'image',file:'camera.jpg',frame_at:5.03,roles:['target_state'],camera_character:'C1',camera_scene:'S1'}],
    replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person',camera_assets:{S1:'camera'}}],product:{action:'keep'}},
    images:{image:{id:'image',state:'completed',for_task:'camera:C1:S1',asset_id:'camera',frame_asset_id:'original',clean_frame_asset_id:'clean',image_ids:['person','clean'],channel:'official',frame_reason_code:'manual'}},
    script:{tasks:[task]},runs:{'seg-01':[{rev:'r2',state:'completed',result:'r2.mp4',remote_status:'completed',remote_task_id:'task_offline_24_13'}]},
    replacement_checks:{'seg-01':{errors:[],issues:[],warnings:[],dialogue_conflicts:[],action_comparison:{status:'missing'}}}};
  let hold;
  const context=vm.createContext({document,console,project,structuredClone,confirm:()=>true,
    navigator:{clipboard:{writeText:async value=>copies.push(value)}},setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  run(source.slice(0,source.indexOf('// ---------- settings ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  context.mockApi=async(method,url,body)=>{
    requests.push({method,url,body:structuredClone(body)});
    if(url.endsWith('/camera-image/draft')){if(hold)await hold;return {prompt:'offline prompt',frame_at:6,frame_asset_id:'original',image_ids:['person','clean'],for_task:'camera:C1:S1'};}
    if(url.endsWith('/images/preview')){project.images.preview={id:'preview',state:'pending_confirmation',channel:'official',for_task:body.for_task,image_ids:body.image_ids,preview:{prompt:body.prompt}};return {};}
    if(url.endsWith('/replacement-check'))return structuredClone(project.replacement_checks);
    if(method==='GET')return structuredClone(project);
    throw Error('Unexpected offline API '+url);
  };
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={default_image_channel:"official",providers:{}};renderAll()');
  const click=async el=>{assert.ok(el,'click target exists');for(const fn of events.click || [])await fn({target:el,preventDefault(){}});};
  return {run,$,$$,click,requests,copies,hold:promise=>{hold=promise;}};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
test('全页台词区只一份；05 已采用/保持/待处理状态，链接精确跳转高亮04',async()=>{
  const t=setup();assert.equal(t.$$('[data-dialogue-box]').length,1);assert.ok(t.$('#scriptView [data-dialogue-box]'));
  assert.equal(t.$('#taskCards [data-dialogue-line]'),null);const status=()=>t.$('[data-dialogue-status="seg-01"]');
  assert.match(status().textContent,/台词：已采用改写版.*去第 04 步查看/);
  let scrolled=false;const target=t.$('#scriptView [data-dialogue-box]');target.scrollIntoView=()=>{scrolled=true;};
  await t.click(status().querySelector('button'));assert.ok(scrolled);assert.match(target.getAttribute('class'),/fix-highlight/);
  t.run('S.tasks[0].dialogue_choice="keep";renderTasks(true)');assert.match(status().textContent,/台词：保持原台词.*去第 04 步查看/);
  t.run('delete S.tasks[0].dialogue_choice;P.replacement_checks["seg-01"].dialogue_conflicts=[{title:"旧服装",matched_terms:["flowy"],details:[]}];renderTasks(true)');
  assert.match(status().textContent,/台词：有 1 处提到旧外观，还没处理.*去第 04 步处理/);
  await t.click(t.$('[data-todo-target="dialogue-proposal"]'));assert.match(target.getAttribute('class'),/fix-highlight/);assert.equal(t.requests.length,0);
});
test('检查后才发现台词冲突时，04补出唯一可用入口并保留未保存的脚本字段',()=>{
  const t=setup();t.run('delete P.script.tasks[0].dialogue_proposal;delete P.script.tasks[0].dialogue_choice;renderScript(true)');
  assert.equal(t.$('[data-dialogue-box]'),null);const title=t.$('#scriptView [data-k="title"]');title.value='未保存标题';
  t.run('dirty.script=true;applyReplacementChecks({"seg-01":{errors:[],issues:[],warnings:[],dialogue_conflicts:[{title:"旧服装",details:[]}]}})');
  assert.equal(t.$$('[data-dialogue-box]').length,1);assert.ok(t.$('#scriptView [data-dialogue-propose]'));assert.equal(title.value,'未保存标题');
});
test('预览或修复失败返回更完整的台词冲突时，立即补出04入口，无需轮询',()=>{
  const t=setup();t.run('delete P.script.tasks[0].dialogue_proposal;delete P.script.tasks[0].dialogue_choice;renderScript(true)');
  assert.equal(t.$('[data-dialogue-box]'),null);
  t.run('showPreviewBlocked("seg-01",{errors:["检查未通过"],issues:[],warnings:[],dialogue_conflicts:[{title:"旧服装",terms:["flowy","back"],details:[]}]})');
  assert.equal(t.$$('[data-dialogue-box]').length,1);assert.ok(t.$('#scriptView [data-dialogue-propose]'));
  assert.match(t.$('[data-dialogue-status]').textContent,/有 2 处提到旧外观，还没处理/);
});
test('完成视频不挂动作未完成黄框，无完成版本仍提示，预览内同样适用',()=>{
  const t=setup();assert.doesNotMatch(t.$('#taskCards').textContent,/动作对比：未完成/);
  const hint=t.$('#taskCards').querySelectorAll('p').find(p=>p.textContent==='下次生成新预览时会重新做动作对比');assert.ok(hint);assert.match(hint.getAttribute('class'),/hint/);
  t.run('P.runs["seg-01"].push({rev:"r3",state:"pending_confirmation",model:"mock",billing_seconds:8,job_sha256:"abc",preview:{tasks:[{duration:8,ratio:"9:16",prompt:"mock",images:[],reference_videos:[]}]}});renderTasks(true)');
  assert.doesNotMatch(t.$('#taskCards').textContent,/动作对比：未完成/);
  t.run('P.runs={};renderTasks(true)');assert.match(t.$('#taskCards').textContent,/动作对比：未完成/);
  t.run('delete P.replacement_checks;P.runs=clone(project.runs);renderTasks(true)');assert.doesNotMatch(t.$('#taskCards').textContent,/动作对比：未完成/);
});
test('表内所有文案与折叠：标题、分析提示、分镜、表演、分段原因、补图',()=>{
  const t=setup();assert.match(page,/每段先生成预览，确认后才付费生成视频。哪段不满意只重做哪段。/);
  assert.equal(t.$('#sendVideoHint').textContent,'当前模型会把视频切成多张截图来分析（只有三方渠道 Gemini 能直接看整段视频）');
  assert.match(t.$('#scriptView').textContent,/这一段的动作和镜头都跟着原片走，这里只需要写语气、表情和重音/);
  assert.doesNotMatch(t.$('#framesStrip').textContent,/每 0\.2s 一帧/);
  const seg=t.$('#scriptView [data-seg-key]'),details=seg.querySelectorAll('details').find(d=>d.textContent.includes('原片片段用途：'));
  assert.ok(details);assert.equal(details.open,false);assert.equal(details.querySelector('summary').textContent,'详细信息');assert.match(details.textContent,/分段依据：/);
  assert.equal(t.$('[data-imgform]').textContent,'另外生成一张参考图');assert.match(t.$('#taskCards').textContent,/需要额外的人物 \/ 商品状态图时用，通常不需要/);
  assert.equal(/整段视频输入仅支持三方渠道 Gemini；当前模型使用帧网格分析。|视频参考模式下，动作和景别完全跟原片片段|补一张目标状态图|链路五预检/.test(page+source),false);
  assert.equal(/精确任务单|帧网格|链路五/.test(page),false);
  assert.equal(/Hypit|@image1/.test(t.$('#step-3 header').textContent),false);
  assert.equal(t.$('[data-motion-timeline] summary').textContent,'原片动作时间轴（只读）');
  assert.equal(t.run('ROLE_LABELS.target_state'),'补充参考图');
  assert.equal(t.run('publicText("链路五 · 精确任务单 · 帧网格")'),'生成流程 · 生成任务详情 · 分镜截图');
  const backend=fs.readFileSync(path.join(root,'console/jimmy_bridge.py'),'utf8');assert.match(backend,/生成前的检查没通过：/);assert.doesNotMatch(backend,/raise BridgeError\(f"链路五预检没通过/);
});
test('远端状态中文、未知值保留、任务号在默认折叠详情内且能复制',async()=>{
  const t=setup();
  for(const [status,label] of [...Object.entries({completed:'已完成',in_progress:'生成中',failed:'失败',queued:'排队中',future_status:'future_status'}),['toString','toString'],['__proto__','__proto__']]){
    t.run(`P.runs["seg-01"][0].remote_status=${JSON.stringify(status)};renderTasks(true)`);
    const card=t.$('[data-run-key="r2"]');assert.match(card.textContent,new RegExp('远端状态 '+label));
    const button=card.querySelector('[data-copy-task]');assert.ok(button);const details=button.closest('details');assert.ok(details);assert.equal(details.open,false);
    assert.equal(details.querySelector('summary').textContent,'详细信息');assert.match(details.textContent,/task_offline_24_13/);
    await t.click(button);assert.equal(t.copies.at(-1),'task_offline_24_13');
  }
  t.run('delete P.runs["seg-01"][0].remote_status;renderTasks(true)');assert.ok(t.$('[data-copy-task]'),'没有状态也可查任务号');
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}}assert.equal(failed,0,`${failed} round24.13 scenarios failed`);console.log(`Round 24.13 UI: ${tests.length} scenarios passed`);})().catch(e=>{console.error(e);process.exitCode=1;});
