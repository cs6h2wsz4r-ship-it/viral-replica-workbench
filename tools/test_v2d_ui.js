/* V2d actual renderers and delegated events; synthetic state and offline HTTP only. */
const assert=require('node:assert/strict');
const {setup}=require('./v2_ui_fixture');
const {seed}=require('./test_v2_ui');

function reviewedProject(){
 const t=setup();seed(t);
 t.run(`P.director.segments.forEach(s=>{s.selected_run='r1';P.runs[s.id]=[{rev:'r1',state:'completed',result:s.id+'.mp4'}]});renderSteps()`);
 return t;
}
function review(t,value){t.context.reviewValue=value;t.run('P.runs.d01[0].review=clone(reviewValue);renderSteps()');}
function card(t){return t.$('[data-review-card]');}
function video(t){return t.$('[data-director-run="d01:r1"] video');}
function button(t){return t.$('[data-director-review="d01"][data-rev="r1"]');}
const high={start:1.2,end:2.5,level:'high',what:'原片文字漏出',fix:'image_mode',cell:'',how:'回第 04 步改成只用图片，再重新生成这一段'};
const low={start:3,end:3.5,level:'low',what:'短暂的轻微瑕疵',fix:'accept',cell:'',how:'可以接受这一处'};
const tests=[
 ['pass and one review button per completed version',async()=>{
  const t=reviewedProject();review(t,{status:'completed',verdict:'pass',summary:'检查通过',issues:[]});
  t.run(`P.runs.d01.push({rev:'r2',state:'failed',error:'生成失败'},{rev:'r3',state:'completed',result:'d01-r3.mp4'});renderSteps()`);
  await t.click(t.$('[data-compare-version="r1"]'));
  assert.match(card(t).textContent,/视频检查通过/);
  assert.equal(button(t).textContent,'检查视频');
  assert.equal(t.$$('[data-director-review]').length,1,'review belongs to the viewed version');
  await t.click(t.$('[data-compare-version="r3"]'));assert.ok(t.$('[data-director-review][data-rev="r3"]'));
  await t.click(t.$('[data-compare-version="r1"]'));
  await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c04"]'));assert.equal(t.$$('[data-director-review]').length,1);
  await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));
  assert.equal(t.$('[data-director-run="d01:r2"] [data-director-review]'),null);
  assert.match(t.$('[data-director-use="d01"]').textContent,/已选用这一版/);assert.equal(button(t).textContent,'检查视频');
 }],
 ['fix issues have time ranges and actionable text',async()=>{
  const t=reviewedProject();review(t,{status:'completed',verdict:'fix',summary:'发现 2 个问题',issues:[high,low]});
  const text=card(t).textContent;
  assert.match(text,/发现 2 个问题/);
  assert.ok(text.includes('1.2–2.5 秒要改原片文字漏出'));
  assert.ok(text.includes('3.0–3.5 秒轻微短暂的轻微瑕疵'));
  assert.equal(card(t).querySelectorAll('li').length,2);
 }],
 ['failed review preserves the video and provides manual checking',async()=>{
  const t=reviewedProject();review(t,{status:'failed',error:'验收请求超时'});
  assert.match(card(t).textContent,/没检查完.*验收请求超时/);
  assert.match(video(t).src,/d01\.mp4/);
  assert.equal(button(t).textContent,'检查视频');
  assert.match(video(t).src,/d01\.mp4/);
  assert.equal(t.run('P.runs.d01[0].state'),'completed');
  assert.equal(t.$('[data-director-use="d01"]').disabled,false);
  assert.equal(button(t).disabled,false);
 }],
 ['running review preserves selection and explains what is happening',async()=>{
  const t=reviewedProject();t.run("P.jobs={'review:d01:r1':{state:'running'}}");review(t,{status:'running'});
  assert.match(card(t).textContent,/检查中.*正在逐秒对照/);
  assert.match(video(t).src,/d01\.mp4/);
  assert.equal(button(t).disabled,true);
  assert.equal(t.$('[data-director-use="d01"]').disabled,false);
  assert.equal(t.$('[data-final-preview]').disabled,false);
 }],
 ['review button posts the run endpoint, merges the project and starts polling',async()=>{
  const t=reviewedProject();let finish;
  t.context.mockApi=async(method,url,body)=>{
   t.requests.push({method,url,body:structuredClone(body)});
   await new Promise(resolve=>finish=resolve);
   const p=JSON.parse(t.run('JSON.stringify(P)'));
   p.runs.d01[0].review={status:'running'};p.jobs={'review:d01:r1':{state:'running'}};
   return p;
  };t.run('api=mockApi');
  assert.ok(button(t),'completed version has a review button');
  const pending=t.click(button(t));await Promise.resolve();
  assert.equal(t.requests.length,1);
  assert.deepEqual(t.requests[0],{method:'POST',url:'projects/sb1-offline/runs/d01/r1/review',body:{}});
  assert.equal(button(t).disabled,true);
  await t.click(button(t));assert.equal(t.requests.length,1);
  finish();await pending;
  assert.equal(t.run('P.runs.d01[0].review.status'),'running');
  assert.equal(t.run('P.runs.d01[0].result'),'d01.mp4');
  assert.equal(t.run('busy()'),true);
  assert.notEqual(t.run('pollTimer'),null);
 }],
 ['request failure uses the same retry button without changing the result',async()=>{
  const t=reviewedProject();
  t.context.mockApi=async(method,url,body)=>{t.requests.push({method,url,body});throw Error('验收服务暂时不可用');};t.run('api=mockApi');
  await t.click(button(t));
  assert.match(card(t).textContent,/验收服务暂时不可用/);
  assert.match(video(t).src,/d01\.mp4/);assert.equal(button(t).textContent,'检查视频');
  assert.equal(t.run('P.runs.d01[0].state'),'completed');
  assert.equal(button(t).disabled,false);
  assert.equal(card(t).querySelectorAll('button').length,1);
  t.context.mockApi=async()=>{const p=JSON.parse(t.run('JSON.stringify(P)'));p.runs.d01[0].review={status:'completed',verdict:'pass',summary:'通过',issues:[]};return p;};t.run('api=mockApi');
  await t.click(button(t));
  assert.match(card(t).textContent,/视频检查通过/);
  assert.doesNotMatch(card(t).textContent,/验收服务暂时不可用/);
 }],
 ...['pass','fix'].map(verdict=>[`polling replaces a dropped-response error with the new ${verdict} review`,async()=>{
  const t=reviewedProject();
  const previous={status:'completed',verdict:'pass',summary:'检查通过',issues:[],request_id:'review-before'};
  review(t,previous);
  const server=JSON.parse(t.run('JSON.stringify(P)'));
  t.context.mockApi=async(method)=>{if(method==='POST')throw Error('验收响应连接断开');return structuredClone(server);};t.run('api=mockApi');
  await t.click(button(t));
  assert.match(card(t).textContent,/验收响应连接断开/);
  server.runs.d01[0].review=verdict==='pass'?{...previous,request_id:'review-after'}:{status:'completed',verdict:'fix',summary:'发现问题',issues:[high],request_id:'review-after'};
  await t.run('refresh()');
  assert.doesNotMatch(card(t).textContent,/验收响应连接断开|没检查完/);
  assert.match(card(t).textContent,verdict==='pass'?/视频检查通过/:/1.2–2.5 秒要改原片文字漏出/);
 }]),
 ['unchanged completed review keeps the failed retry visible after polling',async()=>{
  const t=reviewedProject();review(t,{status:'completed',verdict:'pass',summary:'检查通过',issues:[],request_id:'review-before'});
  const server=JSON.parse(t.run('JSON.stringify(P)'));
  server.runs.d01[0].review=Object.fromEntries(Object.entries(server.runs.d01[0].review).reverse());
  t.context.mockApi=async(method)=>{if(method==='POST')throw Error('本次验收请求未受理');return structuredClone(server);};t.run('api=mockApi');
  await t.click(button(t));await t.run('refresh()');
  assert.match(card(t).textContent,/本次验收请求未受理/);
  assert.equal(button(t).textContent,'检查视频');
  assert.doesNotMatch(card(t).textContent,/视频检查通过/);
 }],
 ['polling during an outstanding request cannot attach its later error to the new review',async()=>{
  const t=reviewedProject();review(t,{status:'completed',verdict:'pass',summary:'检查通过',issues:[],request_id:'review-before'});
  const server=JSON.parse(t.run('JSON.stringify(P)'));let rejectRequest;
  server.runs.d01[0].review={status:'completed',verdict:'fix',summary:'发现问题',issues:[high],request_id:'review-after'};
  t.context.mockApi=async(method)=>method==='POST'?new Promise((resolve,reject)=>rejectRequest=reject):structuredClone(server);t.run('api=mockApi');
  const pending=t.click(button(t));await Promise.resolve();
  await t.run('refresh()');
  rejectRequest(Error('迟到的验收响应断开'));await pending;
  assert.doesNotMatch(card(t).textContent,/迟到的验收响应断开|没检查完/);
  assert.match(card(t).textContent,/1.2–2.5 秒要改原片文字漏出/);
 }],
 ['returning to a project shows its new review instead of a stale-project response error',async()=>{
  const t=reviewedProject();review(t,{status:'completed',verdict:'pass',summary:'检查通过',issues:[],request_id:'review-before'});
  t.context.returnedProject=JSON.parse(t.run('JSON.stringify(P)'));let rejectRequest;
  t.context.mockApi=async()=>new Promise((resolve,reject)=>rejectRequest=reject);t.run('api=mockApi');
  const pending=t.click(button(t));await Promise.resolve();
  t.run('P={...P,id:"another-project"};projectGeneration++');
  const error=Error('项目已切换或正在删除，请稍候');error.staleProject=true;rejectRequest(error);await pending;
  t.context.returnedProject.runs.d01[0].review={status:'completed',verdict:'fix',summary:'发现问题',issues:[high],request_id:'review-after'};
  t.run('P=clone(returnedProject);renderSteps()');
  assert.doesNotMatch(card(t).textContent,/项目已切换|没检查完/);
  assert.match(card(t).textContent,/1.2–2.5 秒要改原片文字漏出/);
 }],
 ['fallback high warning only considers selected versions and allows assembly',async()=>{
  const t=reviewedProject();review(t,{status:'completed',verdict:'fix',summary:'请检查',issues:[high,low]});
  t.context.lowReview={status:'completed',verdict:'fix',issues:[low]};
  t.run(`P.runs.d02[0].review=clone(lowReview);P.runs.d02.push({rev:'r2',state:'completed',result:'unused.mp4',review:clone(P.runs.d01[0].review)});renderSteps()`);
  const text=t.$('#finalPreviewView').textContent;
  assert.ok(text.includes('第 1 段选用的版本检查有问题：原片文字漏出，在第 04 步选中这一段，在“视频”里处理，或确认接受后再拼接。'));
  assert.doesNotMatch(text,/第 2 段选用的版本检查有问题|短暂的轻微瑕疵/);
  assert.equal(t.$('[data-final-preview]').disabled,false);
  assert.equal(t.$$('#finalPreviewView [data-final-preview]').length,1);
  assert.equal(t.$$('#finalPreviewView [data-fission-open]').length,1);
  assert.equal(t.$$('#finalPreviewView input').length,0);
  t.context.mockApi=async(method,url,body)=>{
   t.requests.push({method,url,body});
   return {...JSON.parse(t.run('JSON.stringify(P)')),final_preview:{file:'final/reviewed.mp4',duration:13}};
  };t.run('api=mockApi');
  await t.click(t.$('[data-final-preview]'));
  assert.equal(t.requests.at(-1).url,'projects/sb1-offline/final-preview');
  assert.match(t.$('#finalPreviewView video').src,/reviewed\.mp4/);
 }],
 ['server review warnings are shown without blocking a ready final preview',async()=>{
  const t=reviewedProject();
  t.run(`P.final_preview_status={ready:true,warnings:[],review_warnings:['第 2 段选用的版本检查有问题：人物身份变化，在第 04 步选中这一段，在“视频”里处理，或确认接受后再拼接。']};renderSteps()`);
  assert.match(t.$('#finalPreviewView').textContent,/第 2 段选用的版本检查有问题：人物身份变化/);
  assert.equal(t.$('[data-final-preview]').disabled,false);
  assert.equal(t.$$('#finalPreviewView [data-final-preview]').length,1);
  assert.equal(t.$$('#finalPreviewView [data-fission-open]').length,1);
 }],
];
async function main(){
 let failures=0;
 for(const [name,test] of tests){try{await test();console.log('PASS V2d UI: '+name);}catch(error){failures++;console.error('FAIL V2d UI: '+name);console.error(error);}}
 assert.equal(failures,0,`${failures} V2d UI checks failed`);
 console.log(`PASS V2d UI: ${tests.length} scenarios completed`);
}
module.exports={main};if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
