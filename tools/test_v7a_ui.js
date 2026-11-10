/* V7a: real renderers and delegated events, synthetic projects and mocked APIs. */
const assert=require('node:assert/strict');
const {fixture,until}=require('./test_v5_ui');

const header='#comparisonBatchActions';
const open=t=>t.click(t.$(header+' [data-segment-replan]'));
const confirm=t=>t.click(t.$(header+' [data-segment-confirm]'));
const cancel=t=>t.click(t.$(header+' [data-segment-cancel]'));

async function visibilityAndConfirmation(){
 const t=fixture(),before=t.requests.length;
 assert.equal(t.$(header+' button').textContent,'重新生成分段方案');
 assert.match(t.$(header+' button').getAttribute('class'),/\bghost\b/);
 assert.deepEqual(t.$$(header+' button').map(b=>b.textContent),['重新生成分段方案','全部生成新分镜','全部生成视频']);
 assert.ok(t.$('#selectedSegmentHeader [data-segment-generate]'),'original disclosure entry remains');
 await open(t);
 const box=t.$(header+' [data-segment-confirmation]');assert.ok(box);
 assert.match(box.textContent,/推理模型/);assert.match(box.textContent,/未改变的段.*新分镜/);
 assert.match(box.textContent,/提示词.*过期/);assert.match(box.textContent,/历史视频保留/);
 assert.match(box.textContent,/分段范围.*匹配的视频/);assert.match(box.textContent,/确认.*取消/);
 assert.doesNotMatch(box.textContent,/免费|调用一次/);
 assert.equal(t.$(header+' [data-segment-replan]'),null);
 assert.equal(t.$('#pageConfirmation').hidden,true);assert.equal(t.requests.length,before);
 t.run('P=clone(P);renderSteps()');assert.ok(t.$(header+' [data-segment-confirmation]'),'polling preserves the pending confirmation');
 await cancel(t);assert.ok(t.$(header+' [data-segment-replan]'));assert.equal(t.requests.length,before);
 await open(t);await confirm(t);
 assert.deepEqual(t.requests.slice(before),[{method:'POST',url:'projects/'+t.run('P.id')+'/segment-plan',body:{action:'generate'}}]);
 assert.ok(t.$(header+' [data-segment-replan]'));assert.equal(t.$(header+' [data-segment-confirmation]'),null);
 const empty=fixture();empty.run('P.segments_plan=null;renderSteps()');
 assert.equal(empty.$(header+' [data-segment-replan]'),null);
 assert.equal(empty.$(header+' [data-segment-generate]'),null);
 assert.equal(empty.$$('[data-segment-generate]').length,1);
 assert.match(empty.$('#segmentPlanView [data-segment-generate]').textContent,/^生成分段方案/);
 await empty.click(empty.$('#segmentPlanView [data-segment-generate]'));
 assert.equal(empty.requests.at(-1).url,'projects/'+empty.run('P.id')+'/segment-plan');
 console.log('PASS V7a header visibility/order, inline confirmation/cancel, no modal/request until confirmation, existing first generation');
}

async function sharedProgress(){
 for(const origin of ['header','disclosure']){
  const t=fixture(),base=t.context.mockApi;let started=false,release;
  const gate=new Promise(resolve=>release=resolve);
  t.context.mockApi=async(method,url,body)=>{if(url.endsWith('/segment-plan')){started=true;await gate;}return base(method,url,body);};t.run('api=mockApi');
  if(origin==='header')await open(t);
  const work=origin==='header'?confirm(t):t.click(t.$('#selectedSegmentHeader [data-segment-generate]'));
  await until(()=>started);
  for(const region of [header,'#selectedSegmentHeader']){
   const button=t.$(region+' [data-segment-generate]');assert.ok(button);assert.equal(button.disabled,true);
   assert.equal(button.textContent,'正在重新分段…');await t.click(button);
  }
  assert.equal(t.$(header+' [data-segment-confirmation]'),null);
  assert.ok(t.$$(header+' [data-comparison-batch]').every(b=>b.disabled));
  t.run('P=clone(P);renderSteps()');assert.equal(t.$(header+' [data-segment-generate]').disabled,true);
  release();await work;
  assert.equal(t.requests.filter(r=>r.url.endsWith('/segment-plan')).length,1,'duplicate clicks cannot request twice');
  assert.equal(t.$(header+' [data-segment-replan]').disabled,false);
  assert.equal(t.$('#selectedSegmentHeader [data-segment-generate]').disabled,false);
 }
 console.log('PASS V7a both entry points share pending state and block duplicate requests');
}

async function failureAndRetry(){
 const t=fixture();t.beforeRequest=async(method,url)=>{if(url.endsWith('/segment-plan'))throw Error('服务暂时不可用 <img src=x onerror=bad>');};
 await open(t);await confirm(t);
 const error=t.$(header+' [role="alert"]');assert.ok(error);assert.match(error.textContent,/服务暂时不可用/);
 assert.equal(error.querySelector('img'),null);assert.equal(t.$('#pageConfirmation').hidden,true);
 assert.equal(t.$(header+' [data-segment-replan]').disabled,false);
 t.beforeRequest=null;await open(t);await confirm(t);
 assert.equal(t.$(header+' [role="alert"]'),null);assert.equal(t.requests.filter(r=>r.url.endsWith('/segment-plan')).length,2);
 console.log('PASS V7a escaped inline failure reason and retry');
}

async function speechNotice(){
 for(const [state,lines,expected] of [[null,[],true],[null,[{text:'Legacy sentence'}],true],[{status:'done',fingerprint:'source'},[{text:'Ready sentence'}],false],[{status:'fallback',fingerprint:'source'},[{text:'Original sentence'}],false],[{status:'skipped',fingerprint:'source'},[],false]]){
  const t=fixture();t.run(`P.breakdown.dialogue=[{text:'Original sentence',start:0,end:1}];P.breakdown.speech_lines=${JSON.stringify(lines)};P.breakdown.speech_lines_state=${JSON.stringify(state)};renderSteps()`);
  await open(t);assert.equal(t.$(header+' [data-segment-confirmation]').textContent.includes('会先自动整理一次台词断句。'),expected);
 }
 const silent=fixture();await open(silent);assert.doesNotMatch(silent.$(header).textContent,/会先自动整理一次台词断句/);
 console.log('PASS V7a speech notice only for speech without a completed normalization attempt');
}

async function confirmationScope(){
 const t=fixture();await open(t);const oldConfirm=t.$(header+' [data-segment-confirm]');
 t.run(`P={...clone(P),id:'another-project'};renderSteps()`);
 assert.equal(t.$(header+' [data-segment-confirmation]'),null);await t.click(oldConfirm);assert.equal(t.requests.length,0);
 await open(t);const stale=t.$(header+' [data-segment-confirm]');
 t.run(`P.segments_plan.generation_id='updated-elsewhere';renderSteps()`);
 assert.equal(t.$(header+' [data-segment-confirmation]'),null);await t.click(stale);assert.equal(t.requests.length,0);
 await open(t);t.run('P.segments_plan=null;renderSteps()');assert.equal(t.$(header+' [data-segment-confirmation]'),null);
 const late=fixture(),base=late.context.mockApi;let started=false,release;
 const gate=new Promise(resolve=>release=resolve),original=JSON.parse(late.run('JSON.stringify(P)'));
 late.context.mockApi=async(method,url,body)=>{if(url.endsWith('/segment-plan')){late.requests.push({method,url,body});started=true;await gate;return original;}return base(method,url,body);};late.run('api=mockApi');
 await open(late);const work=confirm(late);await until(()=>started);late.run(`P={...clone(P),id:'another-project'};renderSteps()`);release();await work;
 assert.equal(late.run('P.id'),'another-project');assert.equal(late.$(header+' [data-segment-replan]').disabled,false);
 console.log('PASS V7a project and plan changes discard confirmation; late responses cannot replace another project');
}

async function concurrentWorkAndDrafts(){
 const t=fixture(),base=t.context.mockApi;let started=false,release;
 const gate=new Promise(resolve=>release=resolve);
 t.context.mockApi=async(method,url,body)=>{if(url.endsWith('/segment-plan/d01')){started=true;await gate;}return base(method,url,body);};t.run('api=mockApi');
 await open(t);const mode=t.$('[data-segment-mode="d01"]');mode.value='image';const work=t.event('change',mode);await until(()=>started);
 assert.equal(t.$(header+' [data-segment-confirmation]'),null);
 assert.equal(t.$(header+' [data-segment-replan]').disabled,true);
 assert.equal(t.$('#selectedSegmentHeader [data-segment-generate]').textContent,'正在处理…','saving mode must not claim a new inference request');
 release();await work;
 t.run(`comparisonBatches.set(P.id,{busy:true});renderSteps()`);
 assert.equal(t.$(header+' [data-segment-replan]').disabled,true);
 const count=t.requests.length;await t.run("segmentPlanAction('generate')");assert.equal(t.requests.length,count);
 t.run('comparisonBatches.clear();renderSteps()');await open(t);
 const note=t.$('[data-cast-description="product"]');note.value='尚未保存的商品说明';await t.event('input',note);
 await confirm(t);assert.equal(t.requests.length,count,'existing unsaved casting guard remains in effect');
 console.log('PASS V7a mode-save label, batch lock and unsaved casting guard');
}

async function main(){await visibilityAndConfirmation();await sharedProgress();await failureAndRetry();await speechNotice();await confirmationScope();await concurrentWorkAndDrafts();}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={main};
