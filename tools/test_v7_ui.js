/* V7 speech tables and advisory notices: actual renderers, no network. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {fixture,until}=require('./test_v5_ui');

async function main(){
 const t=fixture();
 const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
 t.run(source.slice(source.indexOf('// ---------- settings ----------'),source.indexOf('// ---------- events ----------')));
 assert.match(t.run('settingsRoutes({})'),/台词断句/);
 assert.match(t.run('settingsRoutes({})'),/name="ROUTE_SPEECH_LINES"[^]*value="follow:analysis" selected/);
 t.run(`P.breakdown.dialogue=[{start:0,end:2,speaker:'C1',text:'old first'},{start:2,end:4,speaker:'C1',text:'old second'}];
 P.breakdown.speech_lines=[{start:0,end:4,speaker:'C1',text:'Complete natural sentence.'}];renderCards()`);
 const card=t.$('[data-card="dialogue"]');
 assert.match(card.textContent,/Complete natural sentence/);
 const details=card.querySelector('details');assert.ok(details);
 assert.equal(details.hasAttribute('open'),false);
 assert.match(details.textContent,/old first.*old second/s);
 assert.equal(card.querySelector('[data-editcard]'),null);
 assert.equal(card.querySelector('textarea'),null);
 t.run(`P.breakdown.speech_lines_state={status:'fallback',warning:'台词自动断句没成功，暂用转写的原句。'};renderCards()`);
 assert.match(t.$('[data-card="dialogue"]').textContent,/台词自动断句没成功，暂用转写的原句。/);
 const warning='第 1 段和第 2 段的分界落在一句台词中间，拼接处台词可能不连贯；可以点“重新生成分段方案”再试。';
 t.run(`P.segments_plan_status={speech_warnings:[${JSON.stringify(warning)}]};P.director.segments[0].speech_warning='一句台词未连续完整写入提示词';renderSteps()`);
 assert.ok(t.$('#segmentPlanView').textContent.includes(warning));
 assert.match(t.$('#directorView').textContent,/一句台词未连续完整写入提示词/);
 assert.equal(t.$('[data-director-rewrite="d01"]').disabled,false);
 t.run('P.segments_plan_status.speech_warnings=[];renderSteps()');
 assert.ok(!t.$('#segmentPlanView').textContent.includes(warning));
 let release,started=false;const gate=new Promise(resolve=>release=resolve),base=t.context.mockApi;
 t.context.mockApi=async(method,url,body)=>{
  if(url.endsWith('/segment-plan')){started=true;await gate;}
  return base(method,url,body);
 };
 t.run('api=mockApi');const work=t.run("segmentPlanAction('generate')");await until(()=>started);
 assert.match(t.$('[data-segment-generate]').textContent,/正在重新分段…/);
 assert.equal(t.$('[data-segment-generate]').disabled,true);
 release();await work;
 assert.equal(t.$('[data-segment-generate]').disabled,false);
 console.log('PASS V7: natural speech table, collapsed raw lines, no editor, fallback and boundary notices, advisory prompt warning, shared button progress');
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={main};
