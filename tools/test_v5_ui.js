/* V5 UI contract: synthetic projects, real renderers/delegated events, no network. */
const assert=require('node:assert/strict');
const {fixture:baseFixture}=require('./test_v4_layout_ui');
function fixture(){
 const t=baseFixture();
 t.run(`P.source.name='reference.mp4';P.source.probe.duration=9;
 P.segment_board_status={};P.director_status={ready:true,segments:[]};
 P.segments_plan.segments.forEach(s=>{const b=P.segment_boards.segments[s.id];b.request='';b.materials={};b.plan={sig:'current',summary:'替换外观，保留动作',cells:s.cells.map(cell=>({cell,changes:'替换选定的人物和商品，保留机位、动作和场景。',keep:'机位与动作'})),questions:[]};b.versions[0].plan_sig='current';});
 newStoryboardDrafts.clear();directorDrafts.clear();renderSteps()`);
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body:structuredClone(body)});
  if(t.beforeRequest)await t.beforeRequest(method,url,body);
  const p=JSON.parse(t.run('JSON.stringify(P)')),parts=url.split('/');
  const sid=parts[parts.indexOf('storyboard-edits')+1];
  if(method==='GET')return t.remote || p;
  if(url.endsWith('/replacements')){
   p.replacements=structuredClone(body);
   p.segment_board_status=Object.fromEntries(p.segments_plan.segments.map(s=>[s.id,{plan_stale:true,warnings:['选角或商品改过，改图方案可能要重新生成']}]));
   return p;
  }
  if(url.includes('/storyboard-edits/') && url.endsWith('/plan')){
   const b=p.segment_boards.segments[sid];b.plan={...b.plan,sig:'fresh',cells:p.segments_plan.segments.find(s=>s.id===sid).cells.map(cell=>({cell,changes:'按选角更新',keep:'动作'}))};
   p.segment_board_status[sid]={};return p;
  }
  if(url.includes('/storyboard-edits/') && method==='PUT'){
   Object.assign(p.segment_boards.segments[sid],structuredClone(body));return p;
  }
  if(url.includes('/storyboard-edits/') && url.endsWith('/preview')){
   if(body.cell_id && !(body.request || '').trim())throw Error('先写一下这一格要怎么改');
   const b=p.segment_boards.segments[sid],v=b.versions.find(v=>v.id===b.current);
   const count=t.requests.filter(r=>r.url===url).length;
   return {id:'image-'+sid+(count>1?'-'+count:''),state:'pending_confirmation',channel:'official',preview:{prompt:'完整生图提示词'},storyboard_context:{sheet_id:sid,plan_sig:b.plan.sig,storyboard_sig:b.storyboard_sig,...(body.cell_id?{cell_id:body.cell_id,base_current:v.id,base_resliced_at:v.resliced_at??null}:{})}};
  }
  if(url.includes('/images/') && url.endsWith('/confirm')){
   p.images[parts.at(-2)].state='running';t.remote=p;return {ok:true};
  }
  if(url.endsWith('/director')){
   if(body.action==='save'){p.director=structuredClone(body.director);return p;}
   if(body.action==='select-run'){p.director.segments.find(s=>s.id===body.segment_id).selected_run=body.rev;return p;}
   if(body.action==='generate'){
    p.director={format_version:3,segments:p.segments_plan.segments.map(s=>({...s,prompt:'图1 新画面',check:[]}))};p.director_status={ready:true,segments:[]};return p;
   }
   assert.equal(body.action,'rewrite','automatic prompt preparation uses single-segment rewrite');
   const s=p.segments_plan.segments.find(s=>s.id===body.segment_id);
   p.director ||= {format_version:3,segments:[]};p.director.format_version=3;
   const d=p.director.segments.find(s=>s.id===body.segment_id);
   if(d){d.prompt='图1 新画面 图2 人物照 视频1 动作';d.needs_rewrite=false;}else p.director.segments.push({...s,prompt:'图1 新画面',check:[]});
   p.director_status={ready:true,segments:[]};return p;
  }
  if(url.includes('/tasks/') && url.endsWith('/preview')){
   const id=parts.at(-2),s=p.segments_plan.segments.find(s=>s.id===id),video=s.mode==='video';
   return {rev:'preview-'+id,state:'pending_confirmation',kind:'director',billing_seconds:video?12:7,billing_note:video?'输出 6s + 参考视频 6s；单价以渠道账户为准':'输出 7s；单价以渠道账户为准',job_sha256:'preview-sha',...(video?{clip:'source-clip.mp4'}:{}),preview:{tasks:[{duration:video?6:7,prompt:'实际视频提示词'}]}};
  }
  if(url.includes('/tasks/') && url.endsWith('/confirm')){
   p.runs[parts.at(-2)].find(r=>r.rev===body.rev).state='running';t.remote=p;return {ok:true};
  }
  if(url.includes('/tasks/') && url.endsWith('/use')){
   p.director.segments.find(s=>s.id===parts.at(-2)).selected_run=body.rev;t.remote=p;return p;
  }
  return p;
 };
 t.run('api=mockApi');return t;
}
function button(t,text,root='#step-4'){return t.$$(root+' button').find(b=>b.textContent.trim()===text);}
async function click(t,text,root){return t.click(button(t,text,root));}
async function until(check){for(let i=0;i<120;i++){if(check())return;await new Promise(r=>setImmediate(r));}assert.fail('UI did not reach expected async state');}
async function layoutTests(){
 const t=fixture();
 for(let n=1;n<=3;n++){
  const b=button(t,'展开','#step-'+n);assert.ok(b,'completed step '+n+' has an expand control');
  assert.match(t.$('#step-'+n).textContent,/✓ 已完成/);await t.click(b);
  assert.ok(button(t,'收起','#step-'+n),'completed step expands to original contents');
 }
 assert.match(t.$('#step-1').textContent,/reference.mp4.*9.*90.*160/s);
 assert.match(t.$('#step-2').textContent,/9 镜/);
 assert.ok(button(t,'整段重画'));assert.ok(button(t,'生成视频'));
 assert.equal(button(t,'全部写提示词'),undefined);
 assert.ok(button(t,'全部生成新分镜'));assert.ok(button(t,'全部生成视频'));
 const adjust=button(t,'调整这一段');assert.ok(adjust);await t.click(adjust);
 for(const s of ['[data-edit-request]','[data-edit-mic]','[data-edit-material]','[data-detail-key="edit-plan:d01"]','[data-director-prompt]','[data-director-save]','[data-edit-use],.edit-versions','[data-segment-mode]'])assert.ok(t.$(s),'advanced function retained '+s);
 assert.match(t.$('#step-4').textContent,/本段脚本/);
 console.log('PASS V5 completed steps, primary actions and advanced controls');
}
async function preparationTests(){
 const t=fixture();
 t.run(`P.segment_board_status.d01={plan_stale:true,warnings:['选角或商品改过，改图方案可能要重新生成']};P.segment_boards.segments.d01.current=null;P.segment_boards.segments.d01.versions=[];renderSteps()`);
 assert.doesNotMatch(t.$('#step-4').textContent,/选角或商品改过，改图方案可能要重新生成|分镜、方案或素材改过，请重新生成免费预览|这一段的说明没写好/);
 await click(t,'生成新分镜');
 const actions=t.requests.filter(r=>/\/(plan|preview)$/.test(r.url));
 assert.deepEqual(actions.map(r=>r.url.split('/').at(-1)),['plan','preview']);
 assert.equal(t.$('#pageConfirmation').hidden,true,'single-segment payment stays inline');
 assert.match(t.$('#step-4').textContent,/生成这一段的新分镜：1 张图，官方，按 OpenAI 计费/);
 assert.ok(button(t,'确认'));assert.ok(button(t,'取消'));
 assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')),'free preparation cannot pay');
 await click(t,'取消');assert.ok(button(t,'生成新分镜'));
 const v=fixture();v.run(`P.director.segments[0].needs_rewrite=true;renderSteps()`);
 await click(v,'生成视频');
 assert.deepEqual(v.requests.filter(r=>r.url.endsWith('/director')).map(r=>r.body),[{action:'rewrite',segment_id:'d01'}]);
 assert.ok(v.requests.findIndex(r=>r.url.endsWith('/director'))<v.requests.findIndex(r=>r.url.endsWith('/preview')));
 assert.match(v.$('#step-4').textContent,/计费约 12 秒（输出 6 秒 \+ 参考视频 6 秒）/);
 await click(v,'确认');
 assert.deepEqual(v.requests.find(r=>r.url.endsWith('/confirm')).body,{rev:'preview-d01',job_sha256:'preview-sha'});
 const image=fixture();await image.click(image.$('[data-compare-side="source"] [data-compare-cell="c05"]'));await click(image,'生成视频');
 assert.match(image.$('#step-4').textContent,/计费约 7 秒（输出 7 秒）/);
 console.log('PASS V5 automatic plan/prompt preparation, preview billing, inline confirmation and cancellation');
}
async function failureTests(){
 const t=fixture();t.beforeRequest=async(method,url)=>{if(url.endsWith('/preview'))throw Error('网络暂时不可用');};
 await click(t,'整段重画');assert.match(t.$('#step-4').textContent,/准备失败：.*网络暂时不可用.*重试/s);
 assert.ok(button(t,'整段重画')||button(t,'生成新分镜'));assert.equal(t.$('#pageConfirmation').hidden,true);
 console.log('PASS V5 preparation failure explains reason and retry');
}
async function cellTests(){
 const t=fixture();await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 assert.match(t.$('#step-4').textContent,/正在改第 2 镜（在下面写要改的地方）/);
 assert.match(t.$('#step-4').textContent,/第 2 镜要改什么/);
 assert.ok(button(t,'取消选镜'));assert.ok(t.$('[data-edit-cell-request="d01"]'));
 assert.match(t.$('#step-4').textContent,/只重画这一镜，其他镜不动/);assert.ok(button(t,'展开完整方案'));
 const input=t.$('[data-edit-cell-request="d01"]');input.value='手放在扶手上，袖子改为浅色';await t.event('input',input);
 await click(t,'重画第 2 镜');
 assert.deepEqual(t.requests.find(r=>r.url.endsWith('/preview')).body,{cell_id:'c02',request:'手放在扶手上，袖子改为浅色'});
 assert.match(t.$('#step-4').textContent,/重画第 2 镜（按你写的要求）：1 张图，官方，按 OpenAI 计费/);
 await click(t,'确认');
 const busy=t.$$('[data-compare-side="target"] [data-compare-cell]').filter(c=>c.textContent.includes('生成中…'));
 assert.deepEqual(busy.map(c=>c.dataset.compareCell),['c02']);
 t.run(`P.images['image-d01'].state='completed';P.segment_boards.segments.d01.versions[0].cells.c02='redrawn-c02.jpg';renderSteps()`);
 assert.match(t.$('[data-compare-side="target"] [data-compare-cell="c02"] img').src,/redrawn-c02/);
 assert.equal(t.$$('[data-compare-side="target"] [data-compare-cell]').filter(c=>c.textContent.includes('生成中…')).length,0);
 console.log('PASS V5 single-cell request, scoped busy overlay and refreshed result');
}
async function videoTests(){
 const t=fixture();t.run(`P.runs.d01=[{rev:'r1',state:'running'}];renderSteps();P.runs.d01[0]={rev:'r1',state:'completed',result:'generated-1.mp4',review:{status:'completed',verdict:'fix',summary:'外观需调整',issues:[{level:'high',start:0,end:1,what:'外观需调整',fix:'rewrite',how:'重写提示词'}]}};renderSteps()`);
 assert.ok(button(t,'分镜对照'));assert.ok(button(t,'视频对照'));
 assert.ok(t.$('#comparisonView video'),'completed result switches to video comparison');
 assert.match(t.$('#comparisonView').innerHTML,/#t=0,5.4/);assert.match(t.$('#comparisonView').innerHTML,/generated-1.mp4/);
 assert.match(t.$('#comparisonView').textContent,/第 1 版.*检查有 1 个问题/s);
 assert.equal(button(t,'按验收意见重写这一段（免费）'),undefined);
 assert.ok(t.$('[data-fix-plan]'));
 await t.click(t.$('[data-fix-plan]'));
 assert.deepEqual(t.requests.find(r=>r.url.endsWith('/fix-plan')).body,{issues:[0],note:''});
 assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')),'planning never pays');
 console.log('PASS V5 automatic video comparison, source range, version/review and rewrite');
}
async function stateTests(){
 const t=fixture();
 assert.equal(t.$('#segmentAdvanced').hidden,true,'advanced panel starts closed');
 await click(t,'调整这一段');assert.equal(t.$('#segmentAdvanced').hidden,false);
 await t.click(t.$('[data-compare-segment-tab="d02"]'));assert.equal(t.$('#segmentAdvanced').hidden,true);
 await t.click(t.$('[data-compare-segment-tab="d01"]'));assert.equal(t.$('#segmentAdvanced').hidden,false);
 t.run('P=clone(P);preserveView(()=>renderSteps())');assert.equal(t.$('#segmentAdvanced').hidden,false);
 t.run(`P={...clone(P),id:'other-project'};renderSteps()`);assert.equal(t.$('#segmentAdvanced').hidden,true,'new project does not inherit expansion');
 const count=fixture();assert.match(count.$('#finalPreviewView').textContent,/已选定 0\/2 段/);assert.equal(count.$('[data-final-preview]').disabled,true);
 count.run(`P.director.segments.forEach(s=>{s.selected_run='r1';P.runs[s.id]=[{rev:'r1',state:'completed',result:'generated.mp4'}]});renderSteps()`);
 assert.match(count.$('#finalPreviewView').textContent,/已选定 2\/2 段/);assert.equal(count.$('[data-final-preview]').disabled,false);
 assert.match(count.$('[data-compare-segment-tab="d01"]').textContent,/✓/);
 const first=fixture();first.run('P.director=null;directorDrafts.clear();renderSteps()');await click(first,'生成视频');
 assert.deepEqual(first.requests.filter(r=>r.url.endsWith('/director')).map(r=>({method:r.method,action:r.body.action})),[{method:'PUT',action:'rewrite'}],'V5a initializes only the selected segment when director does not exist');
 assert.ok(first.requests.some(r=>r.url.endsWith('/preview')));
 const empty=fixture();await empty.click(empty.$('[data-compare-side="target"] [data-compare-cell="c02"]'));await click(empty,'重画第 2 镜');
 assert.match(empty.$('#step-4').textContent,/先写一下这一格要怎么改/);assert.ok(empty.requests.every(r=>!r.url.endsWith('/confirm')),'current backend refuses blank single-cell requests');
 console.log('PASS V5 expansion memory, final gating and existing API constraints');
}
async function concurrencyTests(){
 const t=fixture();let release,started=false;const wait=new Promise(r=>release=r);
 t.beforeRequest=async(method,url)=>{if(url.endsWith('/plan')){started=true;await wait;}};
 t.run(`P.segment_board_status.d01={plan_stale:true};renderSteps()`);
 const pending=click(t,'整段重画');await until(()=>started);
 assert.match(t.$('#segmentBar').textContent,/正在准备…/);
 t.run(`P={...clone(P),id:'other-project'};renderSteps()`);release();await pending;
 assert.ok(t.requests.every(r=>!r.url.includes('other-project')),'late preparation never touches a new project');
 assert.ok(t.requests.every(r=>!r.url.endsWith('/preview')),'late old project plan cannot produce a preview');
 const c=fixture();let resume,saving=false;const saved=new Promise(r=>resume=r);
 c.beforeRequest=async(method,url)=>{if(method==='PUT' && url.endsWith('/storyboard-edits/d01')){saving=true;await saved;}};
 await c.click(c.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 const input=c.$('[data-edit-cell-request]');input.value='只改这一镜';await c.event('input',input);
 const cell=click(c,'重画第 2 镜');await until(()=>saving);
 await c.click(c.$('[data-compare-segment-tab="d02"]'));resume();await cell;
 assert.deepEqual(c.requests.find(r=>r.url.endsWith('/preview')).body,{cell_id:'c02',request:'只改这一镜'});
 assert.equal(c.run('comparisonSelection().sid'),'d02');assert.equal(c.$('#pageConfirmation').hidden,true);
 console.log('PASS V5 preparation race isolation and frozen single-cell request');
}
async function previewIntegrityTests(){
 for(const during of [false,true]){
  const t=fixture();let release,waiting=false;const held=new Promise(r=>release=r);
  t.beforeRequest=async(method,url)=>{if(during && !waiting && url.endsWith('/preview')){waiting=true;await held;}};
  const pending=click(t,'整段重画');if(during)await until(()=>waiting);else await pending;
  const input=t.$('[data-cast-description="C1"]');input.value='预览准备之后修改的新人物外观';await t.event('input',input);
  if(during){release();await pending;}
  if(button(t,'确认'))await click(t,'确认');
  const confirms=t.requests.filter(r=>r.url.endsWith('/confirm'));
  assert.ok(confirms.every(r=>!r.url.endsWith('/images/image-d01/confirm')),'editing casting '+(during?'during':'after')+' preparation never pays the old preview');
  if(confirms.length){assert.ok(t.requests.filter(r=>r.url.endsWith('/preview')).length>=2,'only automatically refreshed preview may be confirmed');continue;}
  if(!button(t,'确认'))await click(t,'整段重画');
  assert.ok(t.requests.every(r=>!r.url.endsWith('/replacements')),'V5a never saves casting during preview preparation');
  assert.match(t.$('#segmentBar').textContent,/选角或商品改了还没保存/);
  await t.run('saveReplacements()');await click(t,'整段重画');
  assert.ok(t.requests.filter(r=>r.url.endsWith('/preview')).length>=2,'changed casting requires a new free preview');
 }
 const v=fixture();await click(v,'生成视频');
 assert.match(v.$('#segmentAdvanced').textContent,/实际视频提示词/,'actual packaged preview remains inspectable in advanced details');
 const b=fixture();b.run(`P.runs.d01=[{rev:'r1',state:'completed',result:'old.mp4'}];P.director.segments[0].needs_rewrite=true;renderSteps()`);
 assert.equal(b.$('#comparisonBoards').hidden,true);await click(b,'整段重画');
 assert.equal(b.$('#comparisonBoards').hidden,false,'drawing from video view reveals the grid in place');
 const recovery=fixture();recovery.run(`P.images.waiting={id:'waiting',state:'query_pending',storyboard_context:{sheet_id:'d01'}};P.runs.d01=[{rev:'r1',state:'completed',result:'old.mp4'},{rev:'r2',state:'failed',remote_task_id:'remote',error:'网络暂时不可用'}];comparisonViews.clear();renderSteps()`);
 assert.ok(recovery.$('[data-edit-resume="waiting"]'),'query_pending image can be recovered');
 assert.ok(recovery.$('[data-director-resume="d01"][data-rev="r2"]'),'newest failed video remains recoverable after reload');
 assert.ok(recovery.$('[data-compare-version="r1"]'),'old completed version remains viewable');
 const scope=fixture();await scope.click(scope.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 const request=scope.$('[data-edit-cell-request]');request.value='只改这一格';await scope.event('input',request);await click(scope,'重画第 2 镜');
 assert.equal(scope.$('[data-comparison-batch="boards"]').disabled,true,'batch cannot replace a pending single-cell confirmation');
 scope.run(`P.images.whole={id:'whole',state:'pending_confirmation',storyboard_context:{sheet_id:'d01'}};newStoryboardPreviews.set(editSheetKey('d01'),P.images.whole)`);
 await click(scope,'确认');assert.ok(scope.requests.some(r=>r.url.endsWith('/images/image-d01/confirm')));assert.ok(scope.requests.every(r=>!r.url.endsWith('/images/whole/confirm')),'frozen single-cell id is authoritative');
 const settings=fixture();let release,waiting=false;const held=new Promise(r=>release=r);
 settings.beforeRequest=async(method,url)=>{if(url.includes('/tasks/') && url.endsWith('/preview')){waiting=true;await held;}};
 const pending=click(settings,'生成视频');await until(()=>waiting);
 const mode=settings.$('[data-segment-mode="d01"]');assert.equal(mode.disabled,true,'mode cannot change while a video preview is being prepared');
 assert.ok(settings.$$('[data-edit-material]').length>0);assert.ok(settings.$$('[data-edit-material]').every(el=>el.disabled),'V5a photo switches are frozen during preparation');
 const requests=settings.requests.length;mode.value='image';await settings.event('change',mode);
 assert.equal(settings.requests.length,requests,'mutation handler also rejects a stale mode event during preparation');
 release();await pending;assert.equal(settings.run('P.segments_plan.segments[0].mode'),'video');
 const shot=fixture();let finish,previewing=false;const wait=new Promise(r=>finish=r);
 shot.beforeRequest=async(method,url)=>{if(!previewing && url.includes('/storyboard-edits/') && url.endsWith('/preview')){previewing=true;await wait;}};
 await shot.click(shot.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 const oldRequest=shot.$('[data-edit-cell-request]');oldRequest.value='浅色袖子';await shot.event('input',oldRequest);
 const drawing=click(shot,'重画第 2 镜');await until(()=>previewing);
 await shot.click(shot.$('[data-compare-side="target"] [data-compare-cell="c03"]'));
 const another=shot.$('[data-edit-cell-request]');assert.equal(another.disabled,true,'same-segment cell editor stays locked for an outstanding cell preview');
 another.value='深色袖子';await shot.event('input',another);
 await shot.click(shot.$('[data-compare-side="target"] [data-compare-cell="c02"]'));finish();await drawing;
 assert.equal(shot.$('[data-edit-cell-request]').value,'浅色袖子','an event from a locked cell cannot change the submitted request');
 assert.deepEqual(shot.requests.find(r=>r.url.endsWith('/preview')).body,{cell_id:'c02',request:'浅色袖子'});
 console.log('PASS V5 stale payment protection, inspectable preview and recovery');
}
async function main(){
 const failures=[];
 for(const test of [layoutTests,preparationTests,failureTests,cellTests,videoTests,stateTests,concurrencyTests,previewIntegrityTests]){
  try{await test();}catch(error){failures.push(test.name);console.error('FAIL '+test.name+'\n'+error.stack);}
 }
 assert.deepEqual(failures,[],'all V5 behavior groups pass');
}
module.exports={fixture,button,click,until,main};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
