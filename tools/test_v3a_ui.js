/* V3a real renderers and click/input events; mocked HTTP only. */
const assert=require('node:assert/strict');
const {setup}=require('./v2_ui_fixture');
const {seed}=require('./test_v2_ui');

async function main(){
 const t=setup();seed(t);
 t.run(`P.director.segments.forEach(s=>{s.prompt='图1 新画面；视频1 动作。';s.needs_rewrite=false;});directorDrafts.clear();renderSteps()`);
 let failure='提示词不能包含内部格号';
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body:structuredClone(body)});
  const p=JSON.parse(t.run('JSON.stringify(P)'));
  p.director=structuredClone(body.director);
  p.director.segments[0].needs_rewrite=!!failure;
  if(failure)p.director.segments[0].rewrite_reason=failure;
  else delete p.director.segments[0].rewrite_reason;
  return p;
 };t.run('api=mockApi');
 t.$('[data-detail-key="director-prompt:d01"]').open=true;
 const edit=async value=>{const input=t.$('[data-director-prompt="d01"]');input.value=value;await t.event('input',input);};
 await edit('图1 新画面；视频1 动作；c01。');
 await t.click(t.$('[data-director-save]'));
 assert.match(t.$('#directorView').textContent,/已保存.*未通过校验/);
 assert.match(t.$('#directorSaveStatus').textContent,/提示词不能包含内部格号/);
 assert.match(t.$('#directorSaveStatus').textContent,/生成视频/);
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false,'invalid saved prompt is repaired on generate');
 assert.equal(t.requests.length,1,'invalid save must not submit or rewrite automatically');
 // A successful save visibly unlocks selected segment video, without discarding the edited text.
 failure='';await edit('图1 新画面；视频1 动作；PERSON C2。');
 await t.click(t.$('[data-director-save]'));
 assert.match(t.$('#directorView').textContent,/已保存.*校验通过/);
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 assert.equal(t.$('[data-director-card="d01"] .director-rewrite-notice'),null);
 assert.match(t.$('[data-director-prompt="d01"]').value,/PERSON C2/);
 // Saving with no dirty input must also show a rejected validation response.
 failure='提示词素材编号不完整或含不存在的编号：图2';
 await t.click(t.$('[data-director-save]'));
 assert.match(t.$('#directorSaveStatus').textContent,/图2/);
 assert.match(t.$('#directorSaveStatus').textContent,/素材.*编号/);
 // Transport/exception path keeps the draft and gives a specific next step.
 await edit('图1 新画面；视频1 动作；修改未丢失。');
 t.context.mockApi=async()=>{throw Error('导演方案不完整：不能修改段落 ID 或顺序');};t.run('api=mockApi');
 await t.click(t.$('[data-director-save]'));
 assert.match(t.$('#directorView [role="alert"]').textContent,/不能修改段落 ID 或顺序/);
 assert.match(t.$('#directorView [role="alert"]').textContent,/保存|刷新/);
 assert.match(t.$('[data-director-prompt="d01"]').value,/修改未丢失/);
 console.log('PASS V3a UI: validation reason, correction guidance, save success, selected segment video unlock, draft preservation');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
