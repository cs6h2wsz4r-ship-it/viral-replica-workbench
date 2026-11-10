/* SB3b actual renderers/events; synthetic state and mocked HTTP only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {setup}=require('./test_storyboard_sb1_ui');
const {seed}=require('./test_v2_ui');
async function main(){
 const t=setup(),long='x'.repeat(6213),reason='这一段的说明有 6213 字符，超过 5000 上限';
 seed(t);t.run('directorDrafts.clear()');
 t.context.longBrief=long;t.context.reason=reason;
 t.run(`P.director={format_version:3,summary:'保留整份方案',questions:[],segments:[{id:'d01',title:'长说明',cells:['c01'],mode:'image',reason:'完整事件',mode_reason:'画面干净',images:['c01'],gen_images:['c01'],timeline:[],what:'抬腕',check:[],source_start:0,source_end:1,source_seconds:1,target_seconds:1,duration:4,prompt:longBrief,needs_rewrite:true,rewrite_reason:reason,warnings:[]}]};P.director_status={ready:true};renderSteps()`);
 const card=t.$('[data-director-card="d01"]');
 assert.doesNotMatch(card.textContent,/这一段的说明有 6213 字符，超过 5000 上限/);assert.equal(t.run('comparisonNeedsPrompt(P.segments_plan.segments[0])'),true);
 assert.ok(t.$('[data-director-save]'),'manual correction remains available');
 assert.equal(t.$('[data-director-prompt="d01"]').value,long);
 let count=t.$('[data-director-count="d01"]');
 assert.match(count.textContent,/6213 \/ 5000/);assert.ok(count.matches('.director-count-over'));
 assert.match(fs.readFileSync(path.join(__dirname,'../web/style.css'),'utf8'),/\.director-count-over\s*\{[^}]*color:\s*var\(--pay\)/);
 assert.equal(t.$('[data-director-rewrite="d01"]').textContent,'重新写这一段（一次推理）');
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false,'video action automatically repairs prompt');
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body:structuredClone(body)});
  const p=JSON.parse(t.run('JSON.stringify(P)'));
  if(body.action==='save')p.director=structuredClone(body.director);
  const s=p.director.segments[0];
  if(body.action==='rewrite')s.prompt='Rewritten';
  if(s.prompt.trim()&&s.prompt.length<=5000){s.needs_rewrite=false;delete s.rewrite_reason;}
  return p;
 };
 t.run('api=mockApi');
 t.$('[data-detail-key="director-prompt:d01"]').open=true;
 const brief=t.$('[data-director-prompt="d01"]');brief.value='a'.repeat(5000);await t.event('input',brief);
 assert.match(count.textContent,/5000 \/ 5000/);assert.equal(count.matches('.director-count-over'),false);
 brief.value='a'.repeat(5001);await t.event('input',brief);assert.ok(count.matches('.director-count-over'));
 t.run('renderSteps()');assert.ok(t.$('[data-director-count="d01"]').matches('.director-count-over'));
 const edited=t.$('[data-director-prompt="d01"]');edited.value='a'.repeat(5000);await t.event('input',edited);
 await t.click(t.$('[data-director-save]'));
 assert.equal(t.requests.at(-1).body.action,'save');assert.equal(t.requests.at(-1).body.director.segments[0].prompt.length,5000);
 assert.doesNotMatch(t.$('[data-director-card="d01"]').textContent,/超过 5000 上限/);
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 t.run(`directorDrafts.clear();P.director.segments[0].prompt='';P.director.segments[0].needs_rewrite=true;P.director.segments[0].rewrite_reason='模型没有写这一段的导演说明';renderSteps()`);
 assert.doesNotMatch(t.$('[data-director-card="d01"]').textContent,/模型没有写这一段的导演说明/);assert.equal(t.run('comparisonNeedsPrompt(P.segments_plan.segments[0])'),true);
 await t.click(t.$('[data-director-rewrite="d01"]'));assert.equal(t.requests.at(-1).body.action,'rewrite');
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 console.log('PASS SB3b: long brief preserved; red live counts; rewrite reasons/button; manual save and rewrite');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
