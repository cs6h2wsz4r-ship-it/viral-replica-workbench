/* V6b: real renderers/events, synthetic state and transport; no browser/network. */
const assert=require('node:assert/strict');
const {fixture}=require('./test_v6_ui');
const {button,click}=require('./test_v5_ui');

async function ready(redraw=true){
 const t=fixture();t.noRedraw=!redraw;await t.click(t.$('[data-fix-plan]'));
 t.run(`Object.assign(P.runs.d01[0].fix_plan,{status:'ready_video',completed_cells:['c02']});renderSteps()`);
 return t;
}
function consumed(t,{status=true,state='completed'}={}){
 t.run(`P.runs.d01.push({rev:'unused-preview',state:'superseded'},{rev:'r9',kind:'director',state:${JSON.stringify(state)},result:${state==='completed'?'"new.mp4"':'null'},fix_plan_binding:{id:'plan-1',sid:'d01',rev:'r1'}});
 ${status?"Object.assign(P.runs.d01[0].fix_plan,{status:'video_started',video_rev:'r9'});":''}
 v5View('d01').rev='r1';renderSteps()`);
}

async function consumedCardAndNavigation(){
 for(const status of [true,false]){
  const t=await ready();consumed(t,{status});const before=t.run('JSON.stringify(P)');
  assert.match(t.$('[data-review-card]').textContent,/已按方案生成第 2 版/);
  assert.match(t.$('[data-review-card]').textContent,/目标人物未完整入镜/);
  assert.doesNotMatch(t.$('[data-review-card]').textContent,/修改进度|已重画|按方案修改/);
  assert.equal(t.$('[data-fix-video]'),null);assert.equal(t.$('[data-fix-apply]'),null);
  assert.ok(button(t,'再生成一版'));assert.ok(button(t,'✓ 已选用这一版'));
  assert.equal(t.run('JSON.stringify(P)'),before,'legacy display inference is read only');
  await click(t,'去看第 2 版 →');assert.equal(t.run("v5View('d01').rev"),'r9');
  assert.match(t.$('[data-review-card]').textContent,/还没检查/);assert.ok(t.$('[data-director-review]'));
  assert.match(t.$('[data-compare-version="r9"]').textContent,/第 2 版 · 未检查/);
 }
}

async function directRepeatAndStalePayment(){
 const t=await ready(false);await t.run("prepareFixVideo('d01','r1')");
 assert.ok(t.$('[data-v5-confirm]'),'an unconfirmed preview still permits its initial confirmation');
 consumed(t,{status:false,state:'failed'});
 assert.equal(t.$('[data-v5-confirm]'),null,'existing paid confirmation disappears after another tab consumes plan');
 const n=t.requests.length;
 await t.run("applyFixPlan('d01','r1');prepareFixVideo('d01','r1');confirmSegmentAction('d01')");
 assert.equal(t.requests.length,n);assert.equal(t.$('[aria-label="付费确认"]'),null);
 t.run("P.runs.d01[0].fix_plan.expected_board='outdated';renderSteps()");
 assert.match(t.$('[data-review-card]').textContent,/已按方案生成第 2 版/,'consumption survives later board edits');
}

async function confirmationSwitchesToUnchecked(){
 const t=await ready(false),normal=t.context.mockApi;
 t.context.mockApi=async(method,url,body)=>{
  const result=await normal(method,url,body);
  if(url.includes('/tasks/')&&url.endsWith('/preview'))result.fix_plan_binding={id:'plan-1',sid:'d01',rev:'r1'};
  if(url.includes('/tasks/')&&url.endsWith('/confirm')){
   t.remote.runs.d01[0].fix_plan.status='video_started';t.remote.runs.d01[0].fix_plan.video_rev=body.rev;
   Object.assign(t.remote.runs.d01.find(r=>r.rev===body.rev),{state:'completed',result:'new.mp4'});
  }
  return result;
 };
 t.run('api=mockApi');await t.run("prepareFixVideo('d01','r1')");
 assert.deepEqual(t.requests.find(r=>r.url.endsWith('/preview')).body,{fix_plan:{id:'plan-1',rev:'r1'}});
 await t.click(t.$('[data-v5-confirm]'));
 assert.equal(t.run("v5View('d01').rev"),'preview-d01');
 assert.match(t.$('[data-review-card]').textContent,/还没检查/);assert.ok(t.$('[data-director-review]'));
 await t.click(t.$('[data-compare-version="r1"]'));
 assert.match(t.$('[data-review-card]').textContent,/已按方案生成第 2 版/);
 assert.ok(button(t,'再生成一版'));assert.equal(t.$('[data-fix-video]'),null);
}

async function legacyConfirmationCannotPromptAgain(){
 const t=await ready(false);await t.run("prepareFixVideo('d01','r1')");
 t.run("const action=v5Actions.get(editSheetKey('d01'));action.rec.fix_plan_binding={id:'plan-1',sid:'d01',rev:'r1'};delete action.fixContext;delete action.fixPlan");
 consumed(t,{status:false});
 assert.ok(!t.$('[data-v5-confirm]'),'an old auto-bound preview is also removed');
 const count=t.requests.length;
 t.run("pageConfirm=async()=>{throw Error('consumed plan must never show a paid dialog')}");
 await t.run("directorVideoAction('confirm','d01',{rev:'preview-d01',job_sha256:'preview-sha'})");
 assert.equal(t.requests.length,count);
}

async function regeneratedPlanIsUsable(){
 const t=await ready();consumed(t);await click(t,'重新出方案');
 t.run("P.runs.d01[0].fix_plan.id='plan-2';renderSteps()");
 assert.ok(t.$('[data-fix-apply]'));assert.doesNotMatch(t.$('[data-review-card]').textContent,/已按方案生成/);
 await t.click(t.$('[data-fix-apply]'));assert.ok(t.$('[data-fix-confirm]'));
}

async function technicalFailureDetails(){
 const t=fixture(),detail={http_status:502,error_type:'<img src=x onerror=bad>',message:'<script>bad()</script>',image_count:8,request_bytes:45678};
 t.run(`P.runs.d01[0].review={status:'failed',error_reason:'上游暂不可用',error_detail:${JSON.stringify(detail)}};
 P.final_preview={file:'final.mp4',check:{status:'failed',error_reason:'上游暂不可用',error_detail:${JSON.stringify(detail)}}};renderSteps()`);
 for(const selector of ['[data-review-card]','[data-final-check-card]']){
  const details=t.$(selector+' details');assert.ok(details,'failed card includes technical disclosure');
  assert.equal(details.hasAttribute('open'),false);assert.equal(t.$(selector+' summary').textContent,'技术信息');
  for(const text of ['502','45678','<script>bad()</script>','<img src=x onerror=bad>'])assert.ok(details.textContent.includes(text));
  assert.equal(t.$(selector+' script'),null);assert.equal(t.$(selector+' [onerror]'),null);
 }
 for(const status of ['completed','running']){
  t.run(`P.runs.d01[0].review.status='${status}';P.runs.d01[0].review.verdict='pass';P.final_preview.check.status='${status}';P.final_preview.check.verdict='pass';P.jobs={'review:d01:r1':{state:'running'},'final-check':{state:'running'}};renderSteps()`);
  assert.equal(t.$('[data-review-card] details'),null);assert.equal(t.$('[data-final-check-card] details'),null);
 }
 t.run("P.runs.d01[0].review={status:'failed'};P.final_preview.check={status:'failed'};renderSteps()");
 assert.equal(t.$('[data-review-card] details'),null);assert.equal(t.$('[data-final-check-card] details'),null);
}

async function main(){
 let failures=0;
 for(const test of [consumedCardAndNavigation,directRepeatAndStalePayment,confirmationSwitchesToUnchecked,legacyConfirmationCannotPromptAgain,regeneratedPlanIsUsable,technicalFailureDetails]){
  try{await test();console.log('PASS V6b '+test.name);}catch(error){failures++;console.error('FAIL V6b '+test.name,error);}
 }
 assert.equal(failures,0);
}
module.exports={main};if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
