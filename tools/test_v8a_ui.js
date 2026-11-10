/* V8a: recover local pending confirmations using synthetic projects and DOM events only. */
const assert=require('node:assert/strict');
const {fixture}=require('./test_v8_ui');

const action=t=>t.run("v5Actions.get(editSheetKey('d01'))");
const confirm=t=>t.$('[data-v5-confirm="d01"]');
const select=(t,cell,side='target')=>t.click(t.$(`[data-compare-side="${side}"] [data-compare-cell="${cell}"]`));
const tab=(t,sid)=>t.click(t.$(`[data-compare-segment-tab="${sid}"]`));
const redraw=t=>t.click(t.$('#segmentBar [data-edit-preview="d01"]'));
const requests=(t,suffix)=>t.requests.filter(r=>r.url.endsWith(suffix));
function seedPreview(t,{sid='d01',cell=null,id='saved-preview',cache=false}={}){
 t.context.previewSeed={sid,cell,id,cache};
 t.run(`{
  const {sid,cell,id,cache}=previewSeed,board=editSheetState(sid),version=board.versions.find(v=>v.id===board.current);
  const revision={changes:'左手自然放在扶手上，袖子改成浅米色。',keep:'保留机位、背景与商品外形。',why:'手和袖子按你的要求调整。',sig:'saved-revision-'+cell,revised_by_request:'手放扶手，浅色袖子',base_plan_sig:board.plan.sig,storyboard_sig:board.storyboard_sig,settings_sig:board.settings_sig};
  if(cell){board.cell_revisions ||= {};board.cell_revisions[cell]=revision;}
  const rec={id,state:'pending_confirmation',channel:'official',preview:{prompt:'已有的完整生图提示词'},storyboard_context:{sheet_id:sid,plan_sig:board.plan.sig,storyboard_sig:board.storyboard_sig,...(cell?{cell_id:cell,base_current:version.id,base_resliced_at:version.resliced_at??null,cell_revision_sig:revision.sig,cell_revision:revision}:{})}};
  P.images[id]=rec;if(cache)newStoryboardPreviews.set(editSheetKey(sid),rec);
 }`);
}
function clearUi(t){t.run('v5Actions.clear();newStoryboardPreviews.clear();newStoryboardDrafts.clear();comparisonViews.clear()');}
function readOnlyRender(t,render='renderSteps()'){
 const project=t.run('JSON.stringify(P)'),count=t.requests.length;
 t.run(render);
 assert.equal(t.run('JSON.stringify(P)'),project,'render recovery cannot mutate project data');
 assert.equal(t.requests.length,count,'render recovery cannot send requests');
}

async function navigationRetainsConfirmation(){
 for(const kind of ['board','video','cell']){
  const t=fixture();
  if(kind==='cell'){
   await select(t,'c02');const input=t.$('[data-edit-cell-request="d01"]');input.value='改成浅色袖子';await t.event('input',input);
   await t.click(t.$('[data-edit-cell-preview="d01"]'));
  }else if(kind==='video')await t.click(t.$('#segmentBar [data-director-preview="d01"]'));
  else await redraw(t);
  const original=action(t),count=t.requests.length;assert.equal(original.status,'confirming');
  await select(t,'c05');assert.equal(t.run('comparisonSelection().sid'),'d02');
  assert.equal(action(t),original,kind+' confirmation survives cross-segment board click');
  await select(t,'c01','source');assert.equal(action(t),original);
  if(kind!=='cell')assert.ok(confirm(t));
  await tab(t,'d02');await tab(t,'d01');assert.equal(action(t),original,kind+' survives tab navigation');
  assert.equal(t.requests.length,count);
 }
 const t=fixture();await redraw(t);const original=action(t);
 await select(t,'c05');await t.click(t.$('#segmentBar [data-edit-preview="d02"]'));
 assert.equal(t.run("v5Actions.get(editSheetKey('d02')).status"),'confirming');
 await select(t,'c01','source');assert.equal(action(t),original);assert.ok(confirm(t));
 console.log('PASS V8a whole/cell/video confirmations retain identity across board and tab navigation');
}

async function localPreviewRecovery(){
 for(const cache of [false,true]){
  const t=fixture();seedPreview(t,{cache});clearUi(t);if(cache)t.run("newStoryboardPreviews.set(editSheetKey('d01'),P.images['saved-preview']);delete P.images['saved-preview']");
  readOnlyRender(t);assert.ok(confirm(t),'existing '+(cache?'cached':'project')+' preview restores confirmation without preparation');
  assert.equal(action(t).kind,'board');assert.equal(action(t).status,'confirming');assert.equal(action(t).rec.id,'saved-preview');
  assert.equal(action(t).binding,t.run("v5Binding('board','d01',null)"));
  assert.match(t.$('#segmentBar').textContent,/1 张图，官方，按 OpenAI 计费/);
  const restored=action(t);readOnlyRender(t);assert.equal(action(t),restored,'re-render never replaces an existing action');
 }
 console.log('PASS V8a refresh simulation restores local/cache previews with binding and no I/O or project mutation');
}

async function invalidPreviewsStayOnGeneration(){
 const cases=[
  ['plan mismatch',"P.images['saved-preview'].storyboard_context.plan_sig='old'"],
  ['storyboard mismatch',"P.images['saved-preview'].storyboard_context.storyboard_sig='old'"],
  ['invalid mark',"newStoryboardInvalidPreviews.add(editSheetKey('d01'))"],
  ['dirty casting','dirty.rep=true'],
  ...['casting_stale','plan_stale','source_stale'].map(flag=>[flag,`P.segment_board_status.d01={${flag}:true}`]),
  ['invalid fix binding',"P.images['saved-preview'].fix_plan_binding={rev:'missing',id:'missing'}"],
 ];
 for(const [name,change] of cases){
  const t=fixture();seedPreview(t);t.run(change);readOnlyRender(t);
  assert.equal(confirm(t),null,name+' cannot restore');assert.equal(action(t),undefined,name+' leaves action absent');
  assert.ok(t.$('#segmentBar [data-edit-preview="d01"]'),name+' keeps the existing generation action');
 }
 for(const flag of ['plan_stale','casting_stale','source_stale']){
  const t=fixture();seedPreview(t);t.run(`P.segment_board_status.d01={${flag}:true};renderSteps()`);await redraw(t);
  assert.ok(requests(t,'/plan').length,flag+' explicitly prepares the plan on click');assert.ok(confirm(t));
 }
 console.log('PASS V8a stale/mismatched/invalid/dirty previews retain generation path and prepare only on click');
}

async function busyAndExistingActionsWin(){
 const cases=[
  ['batch',"comparisonBatches.set(P.id,{busy:true,errors:{}})"],
  ['director','directorBusy.add(P.id)'],['segment plan','segmentPlanBusy.set(P.id,true)'],
  ['board',"newStoryboardBusy.add(editSheetKey('d01'))"],
  ...['running','submitting','query_pending'].map(state=>['image '+state,`P.images.active={id:'active',state:'${state}',storyboard_context:{sheet_id:'d01'}}`]),
  ...['running','submitting','query_pending'].map(state=>['video '+state,`P.runs.d01=[{rev:'active',state:'${state}'},{rev:'old',state:'completed',result:'old.mp4'}];v5View('d01').rev='old'`]),
  ...['preparing','running','confirming'].map(status=>['fix '+status,`fixFlows.set(directorReviewKey('d01','unselected-run'),{status:'${status}'})`]),
  ['fix plan',"fixPlanBusy.add(directorReviewKey('d01','unselected-run'))"],
 ];
 for(const [name,change] of cases){
  const t=fixture();seedPreview(t);t.run(change);readOnlyRender(t);
  assert.equal(confirm(t),null,name+' busy flow blocks restoration');assert.equal(action(t),undefined);
 }
 for(const [kind,status] of [['board','failed'],['board','preparing'],['board','running'],['video','confirming'],['video','failed'],['cell','failed']]){
  const t=fixture();seedPreview(t);t.run(`v5Actions.set(editSheetKey('d01'),{kind:'${kind}',status:'${status}',error:'保留原错误',rec:{id:'active',rev:'active',billing_seconds:6},cell:${kind==='cell'?"'c02'":'null'}})`);
  const original=action(t);readOnlyRender(t);assert.equal(action(t),original,kind+' '+status+' action cannot be overwritten');
 }
 console.log('PASS V8a active image/video/fix work and all existing action states take priority over recovery');
}

async function cancelSuppressesOnlyItsScope(){
 const t=fixture();seedPreview(t);readOnlyRender(t);assert.ok(confirm(t));const project=t.run('JSON.stringify(P)');
 await t.click(t.$('[data-v5-cancel="d01"]'));assert.equal(confirm(t),null);assert.equal(action(t),undefined);
 clearUi(t);readOnlyRender(t);await tab(t,'d02');await tab(t,'d01');assert.equal(confirm(t),null,'cancelled ID stays suppressed across re-renders and navigation');
 assert.equal(t.run('JSON.stringify(P)'),project,'cancel never deletes or edits server preview');assert.equal(t.requests.length,0);
 seedPreview(t,{sid:'d02',id:'saved-preview-d02'});await tab(t,'d02');assert.ok(t.$('[data-v5-confirm="d02"]'),'another segment restores');
 await tab(t,'d01');assert.equal(confirm(t),null);await redraw(t);assert.ok(confirm(t),'explicit redraw clears cancellation for this scope');
 assert.equal(action(t).rec.id,'saved-preview');clearUi(t);readOnlyRender(t);assert.ok(confirm(t));
 await t.click(t.$('[data-v5-cancel="d01"]'));
 t.context.savedProject=JSON.parse(t.run('JSON.stringify(P)'));
 t.run("P={...clone(savedProject),id:'different-project'};renderSteps()");assert.ok(confirm(t),'the same preview ID in another project is independent');
 t.run('P=clone(savedProject);renderSteps()');assert.equal(confirm(t),null);
 seedPreview(t,{id:'newer-preview'});readOnlyRender(t);assert.equal(action(t).rec.id,'newer-preview','a later preview is not cancelled by an older ID');
 console.log('PASS V8a explicit cancel retains previews and suppresses only its project/segment/preview until redraw');
}

async function cellRecoveryAndCancellation(){
 const t=fixture();await select(t,'c02');seedPreview(t,{cell:'c02'});clearUi(t);readOnlyRender(t);
 assert.ok(confirm(t),'valid cell preview restores directly');assert.equal(action(t).kind,'cell');assert.equal(action(t).cell,'c02');
 assert.equal(t.$('[data-edit-cell-request="d01"]').value,'手放扶手，浅色袖子','recovered request remains editable after cancellation');
 assert.equal(action(t).binding,t.run("v5Binding('cell','d01','c02')"));
 const row=t.$('#segmentCellEditor [aria-label="付费确认"]');assert.match(row.textContent,/按你的要求：手和袖子按你的要求调整/);
 const details=row.querySelector('details');assert.equal(details.querySelector('summary').textContent,'展开完整改法');assert.match(details.textContent,/浅米色.*保留机位/s);
 await t.click(t.$('[data-v5-cancel="d01"]'));clearUi(t);readOnlyRender(t);assert.equal(confirm(t),null);
 seedPreview(t,{cell:'c03',id:'other-cell'});await select(t,'c03');assert.equal(action(t).rec.id,'other-cell','cell cancellation cannot suppress another cell');
 await select(t,'c02');assert.equal(confirm(t),null,'cancelled cell stays suppressed when selected again');
 seedPreview(t,{id:'whole-preview'});await t.click(t.$('[data-compare-cancel-cell]'));assert.equal(action(t).rec.id,'whole-preview','cell cancellation cannot suppress whole preview');
 await t.click(t.$('[data-v5-cancel="d01"]'));await select(t,'c02');assert.equal(confirm(t),null);
 const input=t.$('[data-edit-cell-request="d01"]');input.value='重新修改袖子';await t.event('input',input);
 await t.click(t.$('[data-edit-cell-preview="d01"]'));assert.ok(confirm(t));assert.equal(requests(t,'/preview').length,1);
 clearUi(t);readOnlyRender(t);assert.ok(confirm(t),'cell redraw clears only matching cancellation');
 await t.click(t.$('[data-compare-cancel-cell]'));assert.equal(confirm(t),null,'whole cancellation survives cell redraw');
 const draft=fixture();await select(draft,'c02');seedPreview(draft,{cell:'c02'});
 draft.run("newStoryboardCellRequests.set(editSheetKey('d01'),'本地尚未预览的新要求');newStoryboardInvalidPreviews.add(editSheetKey('d01'))");readOnlyRender(draft);
 assert.equal(confirm(draft),null,'dirty request cannot restore an older preview');
 assert.equal(draft.$('[data-edit-cell-request="d01"]').value,'本地尚未预览的新要求','invalidated same-cell request remains intact');
 console.log('PASS V8a cell recovery retains why/details, cell cancellation isolation and explicit redraw retry');
}

async function cellRecoveryKeepsItsOwnRequest(){
 for(const operation of ['confirm','edit']){
  const t=fixture(),normal=t.context.mockApi;
  t.context.mockApi=async(method,url,body)=>{
   const rec=await normal(method,url,body),revision=rec.storyboard_context?.cell_revision;
   if(revision){revision.why='只针对本镜要求：'+body.request;revision.changes='本镜具体改法：'+body.request;}
   return rec;
  };t.run('api=mockApi');
  const prepare=async(cell,request)=>{
   await select(t,cell);const input=t.$('[data-edit-cell-request="d01"]');input.value=request;await t.event('input',input);
   await t.click(t.$('[data-edit-cell-preview="d01"]'));
  };
  await prepare('c02','A：第二镜改为浅色袖子');const firstId=action(t).rec.id;
  await prepare('c03','B：第三镜手放在桌上');assert.equal(action(t).request,'B：第三镜手放在桌上');
  await select(t,'c02');assert.equal(action(t).rec.id,firstId);
  assert.equal(t.$('[data-edit-cell-request="d01"]').value,'A：第二镜改为浅色袖子','recovered cell must not inherit another cell request');
  assert.equal(action(t).request,'A：第二镜改为浅色袖子');
  assert.equal(action(t).rec.storyboard_context.cell_revision.revised_by_request,action(t).request);
  assert.equal(action(t).binding,t.run("v5Binding('cell','d01','c02')"));
  assert.match(t.$('#segmentCellEditor [aria-label="付费确认"]').textContent,/只针对本镜要求：A：第二镜改为浅色袖子/);
  assert.doesNotMatch(t.$('#segmentCellEditor').textContent,/B：第三镜手放在桌上/);
  if(operation==='confirm'){
   await t.click(confirm(t));assert.deepEqual(requests(t,'/confirm').map(r=>r.url),['projects/sb1-offline/images/'+firstId+'/confirm']);
  }else{
   await t.click(t.$('[data-v5-cancel="d01"]'));
   const input=t.$('[data-edit-cell-request="d01"]');input.value='C：第二镜改为深色袖子';await t.event('input',input);
   readOnlyRender(t);assert.equal(confirm(t),null);assert.equal(action(t),undefined);
   assert.equal(t.run("newStoryboardInvalidPreviews.has(editSheetKey('d01'))"),true);
   assert.equal(t.$('[data-edit-cell-request="d01"]').value,'C：第二镜改为深色袖子','genuine same-cell edits survive render');
   await t.click(t.$('[data-edit-cell-preview="d01"]'));
   assert.equal(requests(t,'/preview').at(-1).body.request,'C：第二镜改为深色袖子');
   assert.notEqual(action(t).rec.id,firstId);assert.equal(requests(t,'/confirm').length,0);
   assert.equal(action(t).rec.storyboard_context.cell_revision.revised_by_request,'C：第二镜改为深色袖子');
  }
 }
 const legacy=fixture();await select(legacy,'c02');seedPreview(legacy,{cell:'c02'});
 legacy.run("delete P.images['saved-preview'].storyboard_context.cell_revision;delete P.images['saved-preview'].storyboard_context.cell_revision_sig;delete editSheetState('d01').cell_revisions.c02;newStoryboardCellRequests.set(editSheetKey('d01'),'另一镜留下的要求')");
 readOnlyRender(legacy);assert.ok(confirm(legacy));assert.equal(action(legacy).request,'');
 assert.equal(legacy.$('[data-edit-cell-request="d01"]').value,'','legacy preview cannot claim unrelated segment text as its request');
 assert.doesNotMatch(legacy.$('#segmentCellEditor [aria-label="付费确认"]').textContent,/按你写的要求|另一镜留下的要求/);
 console.log('PASS V8a recovered cells align request/revision/binding across cells, preserve real edits and avoid legacy request claims');
}

async function cellRecoveryValidatesBase(){
 const cases=[
  ['base version',"P.images['saved-preview'].storyboard_context.base_current='old'"],
  ['reslice time',"P.images['saved-preview'].storyboard_context.base_resliced_at='old'"],
  ['slicer',"editSheetState('d01').versions[0].slicer=1"],
  ['revision signature',"P.images['saved-preview'].storyboard_context.cell_revision_sig='old'"],
 ];
 for(const [name,change] of cases){
  const t=fixture();await select(t,'c02');seedPreview(t,{cell:'c02'});t.run(change);readOnlyRender(t);
  assert.equal(confirm(t),null,name+' mismatch cannot restore cell confirmation');assert.ok(t.$('[data-edit-cell-preview="d01"]'));
 }
 console.log('PASS V8a cell recovery respects base version, reslice, slicer and revision signatures');
}

async function sameSegmentSelectionOnlyClearsCell(){
 const t=fixture();await select(t,'c02');seedPreview(t,{cell:'c02'});readOnlyRender(t);assert.ok(confirm(t));
 await select(t,'c03');assert.equal(action(t),undefined,'switching cells invalidates the old cell confirmation');
 await select(t,'c02');assert.ok(confirm(t));await select(t,'c02');assert.equal(action(t),undefined,'deselecting cell invalidates cell confirmation');
 for(const kind of ['board','video']){
  const whole=fixture();if(kind==='board')await redraw(whole);else await whole.click(whole.$('#segmentBar [data-director-preview="d01"]'));
  const original=action(whole);await select(whole,'c02');assert.equal(action(whole),original,kind+' confirmation survives selecting a cell');
  await select(whole,'c03');assert.equal(action(whole),original);await select(whole,'c03');assert.equal(action(whole),original);
  // A delegated event from a previously rendered cancel-selection button must preserve whole/video state too.
  whole.run("const cancelSelection=document.createElement('button');cancelSelection.setAttribute('data-compare-cancel-cell','');document.body.appendChild(cancelSelection)");
  await whole.click(whole.$('[data-compare-cancel-cell]'));assert.equal(action(whole),original);
 }
 console.log('PASS V8a same-segment cell switch/deselect clear cell confirmation while whole/video survive');
}

async function recoveredConfirmKeepsEndpointAndBinding(){
 const t=fixture();seedPreview(t);readOnlyRender(t);assert.ok(confirm(t));await t.click(confirm(t));
 assert.deepEqual(requests(t,'/confirm').map(r=>({url:r.url,body:r.body})),[{url:'projects/sb1-offline/images/saved-preview/confirm',body:{}}]);
 const changed=fixture();seedPreview(changed);readOnlyRender(changed);assert.ok(confirm(changed));
 changed.run("editSheetState('d01').plan={...editSheetState('d01').plan,sig:'updated'};newStoryboardDrafts.clear()");
 await changed.click(confirm(changed));assert.equal(requests(changed,'/confirm').length,0,'binding change never pays old preview');
 assert.equal(requests(changed,'/preview').length,1,'binding mismatch re-enters existing preparation');assert.ok(confirm(changed));
 const fixed=fixture();seedPreview(fixed);
 fixed.run("P.images['saved-preview'].fix_plan_binding={rev:'r1',id:'fix1'};P.runs.d01=[{rev:'r1',state:'completed',result:'old.mp4',review:{status:'completed',verdict:'pass',issues:[]},fix_plan:{id:'fix1',status:'ready_video',redraw:[],completed_cells:[],issues:[],note:'',base_board:'v1'}}]");
 assert.equal(fixed.run("validFixVideo('d01',{rec:P.images['saved-preview']})"),true);readOnlyRender(fixed);assert.ok(confirm(fixed),'valid fix plan binding can restore');
 fixed.run("P.runs.d01[0].fix_plan.status='cancelled'");await fixed.click(confirm(fixed));assert.equal(requests(fixed,'/confirm').length,0);
 console.log('PASS V8a recovered preview uses original confirm route, re-prepares on mismatch and revalidates fix binding');
}

async function main(){
 const failures=[];
 for(const test of [navigationRetainsConfirmation,localPreviewRecovery,invalidPreviewsStayOnGeneration,busyAndExistingActionsWin,cancelSuppressesOnlyItsScope,cellRecoveryAndCancellation,cellRecoveryKeepsItsOwnRequest,cellRecoveryValidatesBase,sameSegmentSelectionOnlyClearsCell,recoveredConfirmKeepsEndpointAndBinding]){
  try{await test();}catch(error){failures.push(test.name);console.error('FAIL '+test.name+'\n'+error.stack);}
 }
 assert.deepEqual(failures,[],'all V8a behavior groups pass');
}
module.exports={main};if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
