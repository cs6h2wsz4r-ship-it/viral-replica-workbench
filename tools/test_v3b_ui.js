/* V3b check guidance and recovered previews; real UI with synthetic HTTP. */
const assert=require('node:assert/strict');
const {setup}=require('./v2_ui_fixture');
const {seed}=require('./test_v2_ui');

async function main(){
 const t=setup();seed(t);
 t.run(`P.director.segments[0].check=[];
 P.director.segments[0].needs_rewrite=true;
 P.director.segments[0].rewrite_reason='生成后检查项缺少内容';
 directorDrafts.clear();renderSteps()`);
 assert.equal(t.$('[data-director-card="d01"] .director-rewrite-notice'),null,'missing checks are prepared automatically');
 assert.equal(t.run('comparisonNeedsPrompt(P.segments_plan.segments[0])'),true);
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body:structuredClone(body)});
  const p=JSON.parse(t.run('JSON.stringify(P)'));
  p.director=structuredClone(body.director);
  const row=p.director.segments[0];
  row.check=Array.from({length:6},(_,i)=>`检查内容 ${i+1}`);
  row.needs_rewrite=false;delete row.rewrite_reason;
  return p;
 };t.run('api=mockApi');
 await t.click(t.$('[data-director-save]'));
 assert.equal(t.$$('[data-director-card="d01"] .director-check li').length,6);
 assert.equal(t.$('[data-director-card="d01"] .director-rewrite-notice'),null);
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 assert.match(t.$('#directorSaveStatus').textContent,/校验通过/);
 assert.equal(t.requests.length,1,'saving recovered checks must not trigger inference or paid generation');
 assert.equal(t.requests[0].body.action,'save');
 console.log('PASS V3b UI: nonempty check guidance, six checks, recovered preview, save only');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
