/* Step05 reuse note uses the actual renderer and mocked Step04 response. */
const assert=require('node:assert/strict');
const {setup}=require('./test_storyboard_sb1_ui');
const {seed}=require('./test_v2_ui');
async function main(){
 const t=setup();seed(t);
 const note='第 04 步重新生成后，这一段内容没变，沿用了已有的新分镜。';
 const controls=t.$$('#newStoryboards button').length;
 assert.ok(!t.$('#newStoryboards').textContent.includes(note));
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body});
  const p=JSON.parse(t.run('JSON.stringify(P)'));
  p.segment_boards.segments.d01.reused_after_replan={from_id:'old_alias',generated_at:'new'};
  p.segment_boards.segments.d01.request='沿用的补充要求';
  return p;
 };t.run('api=mockApi');
 await t.click(t.$('[data-segment-generate]'));
 assert.match(t.requests.at(-1).url,/segment-plan$/);
 assert.ok(t.$('[data-edit-sheet="d01"]').textContent.includes(note));
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c04"]'));
 assert.ok(!t.$('[data-edit-sheet="d02"]').textContent.includes(note));
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));
 assert.equal(t.$('[data-edit-request="d01"]').value,'沿用的补充要求');
 assert.equal(t.$$('#newStoryboards button').length,controls);
 t.run('renderNewStoryboards()');
 assert.ok(t.$('[data-edit-sheet="d01"]').textContent.includes(note));
 t.run('delete P.segment_boards.segments.d01.reused_after_replan;renderNewStoryboards()');
 assert.ok(!t.$('#newStoryboards').textContent.includes(note));
 console.log('PASS V2e: reused note on matching segment after Step04 regeneration, preserved draft, no new controls');
}
module.exports={main};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
