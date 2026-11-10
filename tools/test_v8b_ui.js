/* V8b: real DOM/events, synthetic state and mocked save transport only. */
const assert=require('node:assert/strict');
const {fixture:base}=require('./test_v8_ui');
const {until}=require('./test_v5_ui');
const choices=t=>t.$$('#segmentBar [data-segment-mode-choice]');
const choose=(t,mode)=>t.click(t.$(`#segmentBar [data-segment-mode-choice="${mode}"]`));
function fixture(){
 const t=base(),normal=t.context.mockApi;
 t.run("P.resolved_routes={video:{channel:'third_party',model:'seedance2.0-mini-video'}};renderSteps()");
 t.context.mockApi=async(method,url,body)=>{
  if(!url.includes('/segment-plan/'))return normal(method,url,body);
  t.requests.push({method,url,body:structuredClone(body)});
  if(t.beforeRequest)await t.beforeRequest(method,url,body);
  const p=JSON.parse(t.run('JSON.stringify(P)')),sid=url.split('/').at(-1),s=p.segments_plan.segments.find(s=>s.id===sid);
  s.mode_suggested ||= s.mode;s.mode=body.mode;
  p.director_status={ready:false,segments:[{id:sid,stale:true}]};
  for(const run of p.runs[sid] || [])if(run.state==='pending_confirmation')run.state='superseded';
  return p;
 };t.run('api=mockApi');return t;
}

async function displayAndSuggestions(){
 const t=fixture(),before=t.run('JSON.stringify(P)');
 assert.deepEqual(choices(t).map(b=>b.textContent.replace('推荐','')),['带原片视频','不带原片视频']);
 assert.deepEqual(choices(t).map(b=>b.getAttribute('aria-pressed')),['true','false']);
 assert.match(choices(t)[0].className || choices(t)[0].getAttribute('class'),/\bon\b/);
 assert.ok(choices(t)[0].querySelector('[data-mode-recommended]'));
 assert.match(choices(t)[0].getAttribute('title'),/本段没有商品/);
 const html=t.$('#segmentBar').innerHTML;
 assert.ok(html.indexOf('segment-bar-meta')<html.indexOf('data-segment-mode-choice'));
 assert.ok(html.indexOf('data-segment-mode-choice')<html.indexOf('id="segmentAction"'));
 assert.match(choices(t).map(b=>b.getAttribute('title')).join(' '),/动作、运镜和特效更像原片/);
 assert.match(choices(t).map(b=>b.getAttribute('title')).join(' '),/旧人或旧商品可能漏回来/);
 assert.match(choices(t).map(b=>b.getAttribute('title')).join(' '),/约多 6 计费秒/);
 assert.match(choices(t).map(b=>b.getAttribute('title')).join(' '),/换人换货更干净.*会偏弱/s);
 t.run('renderSteps()');assert.equal(t.run('JSON.stringify(P)'),before);assert.equal(t.requests.length,0);
 t.context.reason='动作与运镜的连续变化需要参考。'.repeat(5)+'<script>bad</script>';
 t.run('P.segments_plan.segments[0].mode_reason=reason;renderSteps()');
 assert.ok(choices(t)[0].getAttribute('title').includes(t.context.reason));
 assert.equal(t.$('#segmentBar script'),null);assert.equal(t.$('[data-segment-mode-suggestion]'),null);
 await choose(t,'image');assert.match(t.$('[data-segment-mode-warning]').textContent,/动作和特效只靠分镜还原，可能偏弱/);
 await choose(t,'video');assert.equal(t.$('[data-segment-mode-warning]'),null);
 console.log('PASS V8b prominent switch, recommended button, escaped full tooltip and read-only rendering');
}

async function savesAndPreserves(){
 const t=fixture();
 t.run(`P.runs.d01=[{rev:'done',state:'completed',result:'done.mp4'}];P.runs.d02=[{rev:'pending',state:'pending_confirmation'}];
 editSheetState('d01').cell_revisions={c02:{sig:'cell',base_plan_sig:'current',changes:'保留单镜改法'}};
 editDraft('d01').request='尚未保存的改图文字';
 newStoryboardCellRequests.set(editSheetKey('d01'),'保留这一镜的草稿');renderSteps()`);
 const board=t.run("JSON.stringify(editSheetState('d01'))");
 await choose(t,'image');
 assert.deepEqual(t.requests,[{method:'PUT',url:'projects/sb1-offline/segment-plan/d01',body:{action:'save',mode:'image'}}]);
 assert.equal(t.$('#pageConfirmation').hidden,true);
 assert.equal(t.run("JSON.stringify(editSheetState('d01'))"),board);
 assert.equal(t.run("editDraft('d01').request"),'尚未保存的改图文字');
 assert.equal(t.run("newStoryboardCellRequests.get(editSheetKey('d01'))"),'保留这一镜的草稿');
 assert.equal(t.run("newStoryboardInvalidPreviews.has(editSheetKey('d01'))"),false);
 assert.equal(t.run('P.runs.d02[0].state'),'pending_confirmation');assert.equal(t.run('P.runs.d01[0].result'),'done.mp4');
 assert.equal(t.$('[data-segment-mode="d01"]').value,'image');
 assert.deepEqual(choices(t).map(b=>b.getAttribute('aria-pressed')),['false','true']);
 const feedback=t.$('[data-segment-mode-feedback]');assert.ok(feedback);
 assert.match(feedback.textContent,/已改成不带原片视频.*已生成的新分镜照用.*生成视频.*重写.*会调用推理.*已生成的视频保留/s);
 assert.match(t.$('#directorView').textContent,/怎么拍：只用目标画面和照片/);
 assert.doesNotMatch(t.$('#directorView').textContent,/怎么拍：原片视频/);
 const count=t.requests.length;await choose(t,'image');assert.equal(t.requests.length,count,'selected option does not resave');
 const select=t.$('[data-segment-mode="d01"]');select.value='video';await t.event('change',select);
 assert.equal(t.requests.at(-1).body.mode,'video');assert.match(t.$('[data-segment-mode-feedback]').textContent,/已改成带原片视频/);
 assert.equal(t.run('P.segments_plan.segments[0].mode_suggested'),'video');
 assert.doesNotMatch(t.$('#segmentBar').textContent,/免费/);
 console.log('PASS V8b one save request, shared dropdown, retained boards/drafts/video and accurate post-save feedback');
}

async function locksAndReasons(){
 const cases=[
  ['dirty.rep=true',/未保存.*选角|选角.*未保存/],
  ["segmentPlanBusy.set(P.id,'save')",/保存/],
  ["segmentPlanBusy.set(P.id,'generate')",/分段/],
  ["comparisonBatches.set(P.id,{busy:true})",/批量/],
  ['directorBusy.add(P.id)',/提示词|视频/],
  ["newStoryboardBusy.add(editSheetKey('d01'))",/分镜|改图/],
  ["fixPlanBusy.add(directorReviewKey('d01','r1'))",/修改方案/],
  ...['preparing','confirming','running'].map(status=>[`fixFlows.set(directorReviewKey('d01','r1'),{status:'${status}'})`,/准备|确认|修改/]),
  ...['board','cell','video'].flatMap(kind=>['preparing','confirming','running'].map(status=>[`v5Actions.set(editSheetKey('d01'),{kind:'${kind}',status:'${status}',rec:{rev:'wait',id:'wait',billing_seconds:12},cell:'c02'})`,/准备|确认|生成/])),
  ...['running','submitting','query_pending'].flatMap(state=>[
   [`P.images.busy={state:'${state}',storyboard_context:{sheet_id:'d01'}}`,/分镜|结果/],
   [`P.runs.d01=[{rev:'busy',state:'${state}'}]`,/视频|结果/],
  ]),
 ];
 for(const [change,reason] of cases){
  const t=fixture();t.run(change+';renderSteps()');
  assert.ok(choices(t).every(b=>b.disabled),change);
  assert.equal(t.$('[data-segment-mode="d01"]').disabled,true,change+' dropdown shares lock');
  assert.match(t.$('[data-segment-mode-disabled]').textContent,reason,change);
  await choose(t,'image');
  await t.run("segmentPlanAction('save','d01',{mode:'image'})");
  assert.equal(t.requests.length,0,change+' blocks delegated and direct calls');
 }
 console.log('PASS V8b all generation/preparation/confirmation/casting locks explain reasons and block save');
}

async function pendingSaveAndFailure(){
 const t=fixture();let entered=false,release;
 const gate=new Promise(resolve=>release=resolve);
 t.beforeRequest=async()=>{entered=true;await gate;};
 const saving=choose(t,'image');await until(()=>entered);
 assert.ok(choices(t).every(b=>b.disabled));assert.match(t.$('[data-segment-mode-disabled]').textContent,/保存/);
 await choose(t,'image');assert.equal(t.requests.length,1);release();await saving;
 const failed=fixture();failed.beforeRequest=async()=>{throw Error('暂时无法保存 <img src=x onerror=bad>');};
 await choose(failed,'image');
 assert.equal(choices(failed)[0].getAttribute('aria-pressed'),'true');
 assert.equal(failed.$('[data-segment-mode-feedback]'),null);
 const error=failed.$('#segmentBar [data-segment-mode-error]');assert.match(error.textContent,/暂时无法保存/);assert.equal(error.querySelector('img'),null);
 assert.equal(choices(failed)[1].disabled,false);failed.beforeRequest=null;await choose(failed,'image');
 assert.equal(failed.$('[data-segment-mode-error]'),null);assert.ok(failed.$('[data-segment-mode-feedback]'));
 const late=fixture();let started=false,resume;const held=new Promise(resolve=>resume=resolve);
 late.beforeRequest=async()=>{started=true;await held;};const work=choose(late,'image');await until(()=>started);
 late.run("P={...clone(P),id:'other'};renderSteps()");resume();await work;
 assert.equal(late.run('P.id'),'other');assert.equal(late.$('[data-segment-mode-feedback]'),null);
 console.log('PASS V8b save progress, duplicate guard, escaped retryable errors and project-scoped responses');
}

async function billingFallback(){
 for(const code of ["P.resolved_routes.video.model='other-model'",'delete P.resolved_routes','P.segments_plan.segments[0].duration=NaN']){
  const t=fixture();t.run(code+';renderSteps()');
  assert.match(choices(t).map(b=>b.getAttribute('title')).join(' '),/计费更高/);
  assert.doesNotMatch(choices(t).map(b=>b.getAttribute('title')).join(' '),/约多 \d/);
 }
 const t=fixture();t.run('P.segments_plan.segments[0].source_end=2.7;renderSteps()');
 assert.match(choices(t).map(b=>b.getAttribute('title')).join(' '),/约多 6 计费秒/,'billing uses stretched target duration');
 console.log('PASS V8b billing uses known effective model and output duration, unknown billing never invents seconds');
}

async function feedbackScope(){
 const t=fixture();t.beforeRequest=async()=>{throw Error('本段保存失败');};await choose(t,'image');
 await t.click(t.$('[data-compare-segment-tab="d02"]'));
 assert.equal(t.$('[data-segment-mode-error]'),null,'mode-save error belongs to its segment');
 const changed=fixture();await choose(changed,'image');assert.ok(changed.$('[data-segment-mode-feedback]'));
 await changed.click(changed.$('#segmentBar [data-director-preview="d01"]'));
 assert.equal(changed.$('[data-segment-mode-feedback]'),null,'after prompt preparation the previous next-generation message is no longer shown');
 console.log('PASS V8b mode feedback/errors remain scoped and stop promising another rewrite after preparation');
}

async function liveCastingLock(){
 const t=fixture(),input=t.$('[data-cast-description="product"]');
 input.value='选角和商品尚未保存的说明';await t.event('input',input);
 assert.ok(choices(t).every(b=>b.disabled));
 const select=t.$('[data-segment-mode="d01"]');assert.equal(select.disabled,true,'a real casting edit immediately locks the retained dropdown');
 assert.equal(select.value,t.run('P.segments_plan.segments[0].mode'));
 assert.match(t.$('[data-segment-mode-disabled]').textContent,/未保存.*选角/);
 assert.equal(t.requests.length,0);
 console.log('PASS V8b actual casting input immediately synchronizes both mode controls');
}

async function completedOtherSegmentUnlocks(){
 for(const kind of ['video','board']){
  const t=fixture();
  t.run(`v5Actions.set(editSheetKey('d02'),{kind:'${kind}',status:'running',rec:{rev:'done',id:'done'}});P.runs.d02=[{rev:'done',state:'completed',result:'done.mp4'}];P.images.done={id:'done',state:'completed',storyboard_context:{sheet_id:'d02'}};renderSteps()`);
  assert.ok(choices(t).every(b=>!b.disabled),'completed '+kind+' in another segment does not keep a stale running lock');
 }
 console.log('PASS V8b completed work in another segment releases the mode lock without navigation');
}

async function main(){
 const failures=[];
 for(const test of [displayAndSuggestions,savesAndPreserves,locksAndReasons,pendingSaveAndFailure,billingFallback,feedbackScope,liveCastingLock,completedOtherSegmentUnlocks]){
  try{await test();}catch(error){failures.push(test.name);console.error('FAIL '+test.name+'\n'+error.stack);}
 }
 assert.deepEqual(failures,[]);
}
module.exports={fixture,main};if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
