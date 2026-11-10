/* V8c: real renderers/events, synthetic projects and transports only. */
const assert=require('node:assert/strict');
const {fixture}=require('./test_v8b_ui');
const {until}=require('./test_v5_ui');
const choices=t=>t.$$('#segmentBar [data-segment-mode-choice]');
const choose=(t,mode)=>t.click(t.$(`#segmentBar [data-segment-mode-choice="${mode}"]`));

async function recommendations(){
 const t=fixture();
 assert.ok(choices(t)[0].querySelector('[data-mode-recommended]'),'legacy project recommends its current mode');
 assert.equal(choices(t)[1].querySelector('[data-mode-recommended]'),null);
 assert.equal(t.$('[data-segment-mode-warning]'),null);
 assert.equal(t.$('[data-segment-mode-suggestion]'),null);
 assert.equal(t.$('[data-segment-mode-help]'),null);
 assert.doesNotMatch(t.$('#segmentBar').textContent,/你改过|取舍与计费|建议：/);
 t.context.reason='完整的推荐理由。'.repeat(20)+'<img src=x onerror=bad>';
 t.run("P.segments_plan.segments[0].mode_suggested='image';P.segments_plan.segments[0].mode_reason=reason;renderSteps()");
 assert.equal(choices(t)[0].querySelector('[data-mode-recommended]'),null);
 assert.equal(choices(t)[1].querySelector('[data-mode-recommended]').textContent,'推荐');
 assert.ok(choices(t)[1].getAttribute('title').includes(t.context.reason));
 assert.ok(!choices(t)[0].getAttribute('title').includes(t.context.reason));
 assert.equal(t.$('#segmentBar img'),null);
 assert.equal(t.$('[data-segment-mode-warning]').textContent,'原片里旧人或旧商品很显眼，可能漏回来；生成后点“检查视频”看一下。');
 assert.match(choices(t)[0].getAttribute('title'),/约多 6 计费秒/);
 assert.match(choices(t)[1].getAttribute('title'),/换人换货更干净/);
 await choose(t,'image');assert.equal(t.$('[data-segment-mode-warning]'),null);
 assert.match(t.$('[data-segment-mode-feedback]').textContent,/已改成不带原片视频/);
 t.run("P.segments_plan.segments[0].mode_suggested='video';renderSteps()");
 assert.equal(t.$('[data-segment-mode-warning]').textContent,'动作和特效只靠分镜还原，可能偏弱。');
 console.log('PASS V8c recommendation badge, full escaped tooltip, concise mismatch-only guidance');
}

async function segmentLocks(){
 const changes=[
  ...['preparing','confirming','running'].flatMap(status=>[
   `v5Actions.set(editSheetKey(SID),{kind:'video',status:'${status}',rec:{rev:'busy',billing_seconds:12}})`,
   `v5Actions.set(editSheetKey(SID),{kind:'board',status:'${status}',rec:{id:'busy',billing_seconds:12}})`,
   `fixFlows.set(directorReviewKey(SID,'r1'),{status:'${status}'})`,
  ]),
  ...['pending_confirmation','running','submitting','query_pending'].flatMap(state=>[
   `P.images.busy={id:'busy',state:'${state}',storyboard_context:{sheet_id:SID}}`,
   `P.runs[SID]=[{rev:'busy',state:'${state}'}]`,
  ]),
  'newStoryboardBusy.add(editSheetKey(SID))',
  "fixPlanBusy.add(directorReviewKey(SID,'r1'))",
 ];
 for(const change of changes){
  for(const sid of ['d01','d02']){
   const t=fixture();t.context.SID=sid;t.run(change+';renderSteps()');
   assert.ok(choices(t).every(b=>b.disabled===(sid==='d01')),sid+' '+change);
   assert.equal(t.$('[data-segment-mode="d01"]').disabled,sid==='d01');
   if(sid==='d01'){
    assert.ok(t.$('[data-segment-mode-disabled]'));
    await t.run("segmentPlanAction('save','d01',{mode:'image'})");assert.equal(t.requests.length,0);
   }else{
    assert.equal(t.$('[data-segment-mode-disabled]'),null);
    await choose(t,'image');assert.equal(t.requests.length,1,'other segment must not block the save path: '+change);
    assert.equal(t.run('P.segments_plan.segments[0].mode'),'image');
   }
  }
 }
 for(const change of ["segmentPlanBusy.set(P.id,'generate')","segmentPlanBusy.set(P.id,'save')","comparisonBatches.set(P.id,{busy:true})",'dirty.rep=true','directorBusy.add(P.id)']){
  const t=fixture();t.run(change+';renderSteps()');assert.ok(choices(t).every(b=>b.disabled));
 }
 console.log('PASS V8c segment-local preparations, confirmations, images, videos and fix flows; global project locks');
}

async function directorLocks(){
 const t=fixture();let started=false,release;
 const hold=new Promise(resolve=>release=resolve);
 t.beforeRequest=async(method,url)=>{if(url.endsWith('/director')){started=true;await hold;}};
 const pending=t.run("directorAction('rewrite',{segment_id:'d02'})");await until(()=>started);
 assert.ok(choices(t).every(b=>b.disabled));
 assert.match(t.$('[data-segment-mode-disabled]').textContent,/第 2 段.*提示词/,'global rewrite CAS must name the blocking segment');
 const count=t.requests.length;await choose(t,'image');assert.equal(t.requests.length,count);
 release();await pending;assert.ok(choices(t).every(b=>!b.disabled));
 const video=fixture();let entered=false,resume;
 const gate=new Promise(resolve=>resume=resolve);
 video.beforeRequest=async(method,url)=>{if(url.includes('/tasks/')){entered=true;await gate;}};
 const preparing=video.run("directorVideoAction('preview','d02')");await until(()=>entered);
 assert.ok(choices(video).every(b=>!b.disabled),'other segment video packaging has no global CAS');
 resume();await preparing;
 console.log('PASS V8c global director rewrite names its segment; other-segment video preview stays isolated');
}

async function cancelledConfirmation(){
 for(const kind of ['video','board']){
  const t=fixture();await t.run(`prepareSegmentAction('d01','${kind}')`);
  assert.ok(choices(t).every(b=>b.disabled));await t.click(t.$('[data-v5-cancel="d01"]'));
  assert.ok(choices(t).every(b=>!b.disabled),kind+' cancelled preview must release the switch');
  await choose(t,'image');assert.equal(t.run('P.segments_plan.segments[0].mode'),'image');
 }
 console.log('PASS V8c cancelling a local pending confirmation releases its mode switch');
}

async function pendingRecoveryAndCellExit(){
 for(const kind of ['video','cell']){
  const t=fixture();
  t.run(kind==='video'?"P.runs.d01=[{rev:'waiting',state:'pending_confirmation'}];renderSteps()":"P.images.waiting={id:'waiting',state:'pending_confirmation',storyboard_context:{sheet_id:'d01',cell_id:'c02'}};renderSteps()");
  assert.ok(choices(t).every(b=>b.disabled));
  const before=t.run('JSON.stringify(P)');
  assert.ok(t.$('[data-segment-mode-cancel-pending]'),'a record restored after reload must have a cancellation exit');
  await t.click(t.$('[data-segment-mode-cancel-pending]'));
  assert.ok(choices(t).every(b=>!b.disabled));assert.equal(t.run('JSON.stringify(P)'),before);assert.equal(t.requests.length,0);
 }
 for(const exit of ['button','cell']){
  const t=fixture();await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
  t.run("newStoryboardCellRequests.set(editSheetKey('d01'),'只调整手势')");await t.run("prepareSegmentAction('d01','cell')");
  assert.ok(choices(t).every(b=>b.disabled));
  await t.click(t.$(exit==='button'?'[data-compare-cancel-cell]':'[data-compare-side="target"] [data-compare-cell="c02"]'));
  assert.ok(choices(t).every(b=>b.disabled),'deselecting retains the cell preview for later inspection: '+exit);
  assert.ok(t.$('[data-segment-mode-cancel-pending]'));await t.click(t.$('[data-segment-mode-cancel-pending]'));
  assert.ok(choices(t).every(b=>!b.disabled),'explicitly cancelling the retained preview releases the switch');
 }
 console.log('PASS V8c restored and deselected cell previews offer explicit cancellation without losing V8a recovery');
}

async function concurrentProjectResponses(){
 for(const action of ['plan','save'])for(const delayed of ['board','mode']){
  const t=fixture();let server=JSON.parse(t.run('JSON.stringify(P)')),captured=false,release;
  const gate=new Promise(resolve=>release=resolve);
  t.context.mockApi=async(method,url,body)=>{
   t.requests.push({method,url,body});let kind;
   if(url.endsWith('/storyboard-edits/d02/plan') || url.endsWith('/storyboard-edits/d02')){kind='board';server.segment_boards.segments.d02.plan.sig='new-other-board';}
   else if(url.endsWith('/segment-plan/d01')){kind='mode';const s=server.segments_plan.segments[0];s.mode_suggested ||= s.mode;s.mode=body.mode;server.director.segments[0].inputs_sig=null;}
   else assert.equal(method,'GET');
   const snapshot=structuredClone(server);
   if(kind===delayed){captured=true;await gate;}
   return snapshot;
  };t.run('api=mockApi');
  const board=()=>t.run(`performStoryboardEdit('d02','${action}')`),mode=()=>t.run("segmentPlanAction('save','d01',{mode:'image'})");
  const pending=delayed==='board'?board():mode();await until(()=>captured);
  await (delayed==='board'?mode():board());release();await pending;
  assert.equal(t.run('P.segments_plan.segments[0].mode'),'image',delayed+' response cannot roll back the saved mode');
  assert.equal(t.run('P.segment_boards.segments.d02.plan.sig'),'new-other-board',delayed+' response cannot roll back the other segment result');
  assert.ok(t.$('[data-segment-mode-feedback]'));
  assert.equal(t.requests.filter(r=>r.url.endsWith('/segment-plan/d01')).length,1,'no repeat mode save or model request');
 }
 const switched=fixture();
 switched.context.noteRequest=(method,url,pid)=>switched.requests.push({method,url,pid});
 switched.run(`var savedSnapshot=clone(P);api=(method,url)=>{
  noteRequest(method,url,P.id);
  if(method==='PUT')return new Promise(resolve=>globalThis.releaseSaved=resolve);
  return Promise.resolve({id:'late',state:'pending_confirmation',storyboard_context:{sheet_id:'d02'}});
 }`);
 const preparing=switched.run("performStoryboardEdit('d02','preview')");
 switched.run("releaseSaved(savedSnapshot);Promise.resolve().then(()=>{P={...P,id:'another-project'};})");await preparing;
 assert.deepEqual(switched.requests.map(r=>r.method),['PUT'],'switching project during response merge must prevent the next preview request');
 assert.equal(switched.run('P.id'),'another-project');
 console.log('PASS V8c late full-project responses preserve both saved mode and other-segment results');
}

async function concurrentPreviewResponses(){
 for(const kind of ['video','image']){
  const t=fixture();let server=JSON.parse(t.run('JSON.stringify(P)')),previewEntered=false,modeCaptured=false,releasePreview,releaseMode;
  const previewGate=new Promise(resolve=>releasePreview=resolve),modeGate=new Promise(resolve=>releaseMode=resolve);
  t.context.mockApi=async(method,url,body)=>{
   t.requests.push({method,url,body});
   if(url.endsWith('/preview')){
    previewEntered=true;await previewGate;
    const rec=kind==='video'?{rev:'pending-other',state:'pending_confirmation',billing_seconds:7}:{id:'pending-other',state:'pending_confirmation',channel:'official',storyboard_context:{sheet_id:'d02',plan_sig:'current'}};
    if(kind==='video')server.runs.d02=[rec];else server.images[rec.id]=rec;
    return structuredClone(rec);
   }
   if(url.endsWith('/segment-plan/d01')){
    server.segments_plan.segments[0].mode='image';const snapshot=structuredClone(server);modeCaptured=true;await modeGate;return snapshot;
   }
   return structuredClone(server);
  };t.run('api=mockApi');
  const preview=t.run(kind==='video'?"directorVideoAction('preview','d02')":"performStoryboardEdit('d02','preview')");await until(()=>previewEntered);
  const mode=t.run("segmentPlanAction('save','d01',{mode:'image'})");await until(()=>modeCaptured);
  releasePreview();await preview;releaseMode();await mode;
  assert.equal(t.run('P.segments_plan.segments[0].mode'),'image');
  assert.equal(t.run(kind==='video'?"P.runs.d02?.[0]?.rev":"P.images['pending-other']?.id"),'pending-other',kind+' partial preview must survive the late full-project response');
  assert.equal(t.requests.filter(r=>r.url.endsWith('/preview')).length,1);
 }
 console.log('PASS V8c partial image/video previews survive a concurrent mode-save response');
}

async function main(){
 const failures=[];
 for(const test of [recommendations,segmentLocks,directorLocks,cancelledConfirmation,pendingRecoveryAndCellExit,concurrentProjectResponses,concurrentPreviewResponses]){
  try{await test();}catch(error){failures.push(test.name);console.error('FAIL '+test.name+'\n'+error.stack);}
 }
 assert.deepEqual(failures,[]);
 await require('./v8c_sync_cases').runSyncTests();
}
module.exports={main,recommendations,segmentLocks,directorLocks,cancelledConfirmation,pendingRecoveryAndCellExit,concurrentProjectResponses,concurrentPreviewResponses};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
