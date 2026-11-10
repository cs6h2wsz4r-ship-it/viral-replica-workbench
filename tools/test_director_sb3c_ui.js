/* SB3c: actual renderers/request handlers; all fetches synthetic, no server. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {setup}=require('./test_storyboard_sb1_ui');
const {seed}=require('./test_v2_ui');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../web/style.css'),'utf8');
const offline='连不上工作台：它可能已经停止运行。请在终端重新运行 start.command，然后刷新页面。';

async function rendering(){
 const t=setup();
 t.run(`P.director={format_version:3,summary:'完整故事',split_reason:'只分两段 <safe>',tradeoff:'old',warnings:['模型没有说明分段取舍'],questions:[],segments:['low','medium','high'].map((risk,i)=>({id:'d'+i,title:'段落',cells:['c0'+(i+1)],mode:'image',what:'抬腕 <safe>',check:['手表表盘朝外'],timeline:[],mode_reason:'画面干净',images:['c01'],gen_images:['c01'],source_start:i,source_end:i+1,source_seconds:1,target_seconds:4,duration:4,prompt:'Ready brief',risk,risk_reason:'old',warnings:['不足 600 字符']}))};P.segments_plan={format_version:2,summary:'完整故事',segments:P.director.segments.map(s=>({...s,refs:['C1']}))};P.segment_boards={segments:Object.fromEntries(P.segments_plan.segments.map(s=>[s.id,{source:{...s,file:s.id+'.jpg'},current:'v1',versions:[{id:'v1',from:'full',slicer:2,file:s.id+'-new.jpg',cells:Object.fromEntries(s.cells.map(c=>[c,c+'-new.jpg']))}]}]))};P.director_status={ready:true};renderSteps()`);
 assert.equal(t.$('.director-summary').textContent,'完整故事');
 assert.equal(t.$('.director-split-reason'),null,'split explanation belongs to Step 04');
 assert.equal(t.$('.director-tradeoff'),null);assert.equal(t.$('.director-risk'),null);
 assert.doesNotMatch(t.$('#directorView').textContent,/取舍|风险|600/);
 assert.doesNotMatch(css,/\.director-risk-/);
 for(let i=0;i<3;i++){await t.click(t.$(`[data-compare-side="source"] [data-compare-cell="c0${i+1}"]`));assert.equal(t.$(`[data-director-preview="d${i}"]`).disabled,false);}
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));
 t.run('P.director_status.stale=true;P.director_status.segments=[{id:"d0",stale:true}];renderDirector()');
 assert.doesNotMatch(t.$('#directorView').textContent,/上游内容改过/);assert.equal(t.run('comparisonNeedsPrompt(P.segments_plan.segments[0])'),true);
 t.run(`delete P.director.format_version;directorDrafts.clear();renderSteps()`);
 assert.equal(t.$('[data-director-card]'),null);assert.ok(t.$('[data-director-preview]'),'legacy plan prepares automatically from video action');
 assert.doesNotMatch(t.$('#directorView').textContent,/undefined|null/);
}

function networkSetup(){
 const t=setup();
 // Restore the real API overwritten by the reusable renderer fixture.
 t.run(source.slice(source.indexOf('async function api('),source.indexOf('function toast(')));
 t.run(`fetch=async()=>{throw new TypeError('Load failed')}`);
 return t;
}

async function network(){
 const t=networkSetup();seed(t);
 for(const message of ['Load failed','Failed to fetch']){
  t.context.networkMessage=message;t.run(`fetch=async()=>{throw new TypeError(networkMessage)}`);
  await assert.rejects(t.run(`api('GET','status')`),{message:offline,kind:'transient'});
  await assert.rejects(t.run(`upload('projects/'+P.id+'/assets',{name:'photo.png'})`),{message:offline,kind:'transient'});
 }
 t.run('P.director_status={ready:true};P.director=null;directorDrafts.clear();renderSteps()');
 await t.click(t.$('[data-director-preview]'));
 assert.ok(t.$('#segmentAction').textContent.includes(offline));
 t.$('#framesStrip').innerHTML='<button data-doc="analysis/readme.txt">查看文档</button><pre class="docview" hidden></pre>';
 await assert.rejects(t.click(t.$('[data-doc]')),{message:offline});
 assert.equal(t.$('#toast').textContent,offline,'direct document fetch shows the same plain-language error');
 t.run(`fetch=async()=>({ok:true,text:async()=> 'document contents'})`);
 await t.click(t.$('[data-doc]'));assert.equal(t.$('.docview').textContent,'document contents');assert.equal(t.$('.docview').hidden,false);
 const expected={error:'业务校验未通过',issues:['one'],errors:['two'],error_kind:'validation'};
 t.context.reply=expected;
 t.run(`fetch=async()=>({ok:false,status:422,statusText:'Unprocessable',json:async()=>reply})`);
 await assert.rejects(t.run(`api('POST','status',{})`),e=>e.message===expected.error&&e.kind==='validation'&&e.issues===expected.issues&&e.errors===expected.errors);
 await assert.rejects(t.run(`upload('projects/'+P.id+'/assets',{name:'photo.png'})`),{message:expected.error});
 t.run(`fetch=async()=>({ok:false,status:404,statusText:'Not Found',json:async()=>({})})`);
 await assert.rejects(t.run(`api('GET','status')`),{message:'后台是旧版本工作台，没有这个功能：请重新双击 start.command / start.bat 重启'});
 t.run(`fetch=async()=>({ok:false,status:500,statusText:'Load failed',json:async()=>({error:'Failed to fetch'})})`);
 await assert.rejects(t.run(`api('GET','status')`),{message:'Failed to fetch'});
 t.run(`fetch=async()=>{const e=new Error('cancelled');e.name='AbortError';throw e}`);
 await assert.rejects(t.run(`api('GET','status')`),{name:'AbortError',message:'cancelled'});
 t.run(`fetch=async()=>{throw new Error('another failure')}`);
 await assert.rejects(t.run(`api('GET','status')`),{message:'another failure'});
 t.run(`fetch=async()=>({ok:true,json:async()=>({ready:true})})`);
 assert.equal((await t.run(`api('GET','status')`)).ready,true);
 t.run(`fetch=async()=>({ok:true,json:async()=>{throw new TypeError('response parse failure')}})`);
 assert.equal(JSON.stringify(await t.run(`api('GET','status')`)),'{}','response parsing behavior unchanged');
 assert.equal((source.match(/\bfetch\(/g)||[]).length,1,'all direct fetch paths share network error handling');
}

async function main(){
 // Run both groups even during the initial failing-test pass.
 const results=await Promise.allSettled([rendering(),network()]);
 const errors=results.filter(r=>r.status==='rejected').map(r=>r.reason);
 if(errors.length)throw new AggregateError(errors,'SB3c regression failures');
 console.log('PASS SB3c: removed risk/tradeoff, split reason, legacy gate; API/upload/document network errors and HTTP compatibility');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
