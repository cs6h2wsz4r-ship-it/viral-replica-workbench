/* V6: real delegated UI, synthetic project and fake transports only. */
const assert=require('node:assert/strict');
const {fixture:base,button,click,until}=require('./test_v5_ui');
function fixture(){
 const t=base(),normal=t.context.mockApi;
 t.run(`P.runs.d01=[{rev:'r1',kind:'director',state:'completed',result:'result.mp4',billing_seconds:12,billing_note:'输出 6s + 参考视频 6s',review_inputs:{segment:{source_start:0,source_end:5.4,duration:6}},review:{status:'completed',request_id:'check-1',verdict:'fix',summary:'两处问题',issues:[{start:1,end:2,level:'high',what:'目标人物未完整入镜',cell:'c02',fix:'redraw_cell',how:'旧操作说明'},{start:3,end:4,level:'low',what:'切点略早',fix:'accept',how:'旧操作说明'}]}}];P.director.segments[0].selected_run='r1';renderSteps()`);
 t.context.mockApi=async(method,url,body)=>{
  const p=JSON.parse(t.run('JSON.stringify(P)')),run=p.runs.d01[0];
  if(/\/(fix-plan|apply|accept|check|review)$/.test(url)){
   t.requests.push({method,url,body:structuredClone(body)});if(t.beforeV6)await t.beforeV6(method,url,body);
   if(url.endsWith('/fix-plan')){
    if(method==='PUT'){delete run.fix_plan;return p;}
    run.fix_plan={id:'plan-1',status:'ready',issues:body.issues,note:body.note,base_board:p.segment_boards.segments.d01.current,expected_board:p.segment_boards.segments.d01.current,base_prompt:p.director.segments[0].prompt,prompt:'目标人物完整入镜。保留机位。',redraw:t.noRedraw?[]:[{cell:'c02',why:'这一镜缺少目标人物',request:'让目标人物完整入镜'}],why:'让目标外观明确入镜',prompt_changes:[{before:'人物局部入镜。',after:'目标人物完整入镜。'}],video_seconds:12,completed_cells:[]};
   }else if(url.endsWith('/fix-plan/apply')){run.fix_plan.status=run.fix_plan.redraw.length?'redrawing':'ready_video';p.director.segments[0].prompt=run.fix_plan.prompt;p.director.segments[0].prompt_source='fix_plan';}
   else if(url.endsWith('/review/accept')){if(body.accepted)run.review.accepted={at:'now'};else delete run.review.accepted;}
   else if(url.endsWith('/final-preview/check/accept')){if(body.accepted)p.final_preview.check.accepted={at:'now'};else delete p.final_preview.check.accepted;}
   else if(url.endsWith('/review')){run.review={status:'completed',request_id:'check-2',verdict:'pass',issues:[],summary:'通过'};delete run.fix_plan;}
   else if(url.endsWith('/final-preview/check'))p.final_preview.check={status:'completed',verdict:'fix',issues:[{start:5,end:6,level:'high',segment:'d01',what:'接缝处商品状态不一致'}]};
   return p;
  }
  return normal(method,url,body);
 };
 t.run('api=mockApi');return t;
}
async function cards(){
 const t=fixture();
 assert.ok(button(t,'检查视频'),'completed video exposes the manual 检查视频 action');
 assert.doesNotMatch(t.$('#step-4').textContent,/自动验收|重新检查|重新验收|旧操作说明/);
 const checks=t.$$('[data-review-issue]');assert.equal(checks.length,2);assert.equal(checks[0].checked,true);assert.equal(checks[1].checked,false);
 assert.equal(t.$$('[data-review-mark]').length,2);assert.ok(t.$('[data-review-warning="d01"]'));
 const gen=t.$('[data-video-side="target"] video'),src=t.$('[data-video-side="source"] video');let pauses=0;gen.pause=src.pause=()=>pauses++;
 await t.click(t.$('[data-review-seek]'));assert.equal(gen.currentTime,1);assert.equal(src.currentTime,.9);assert.equal(pauses,2);
 checks[0].checked=false;await t.event('change',checks[0]);assert.equal(t.$('[data-fix-plan]').disabled,true);
 const note=t.$('[data-review-note]');note.value='增加完整外观';await t.event('input',note);assert.equal(t.$('[data-fix-plan]').disabled,false);
 await t.click(t.$('[data-fix-plan]'));assert.deepEqual(t.requests.find(r=>r.url.endsWith('/fix-plan')).body,{issues:[],note:'增加完整外观'});
 assert.match(t.$('[data-review-card]').textContent,/先重画第 2 镜.*改提示词.*重新生成这一段视频/s);
 assert.equal(t.$$('[data-prompt-change]').length,1);assert.equal(t.$('[data-prompt-change] del').textContent,'人物局部入镜。');
 assert.match(t.$('[data-review-card]').textContent,/重画 1 张图 \+ 视频约 12 秒/);
 assert.doesNotMatch(t.$('[data-review-card]').textContent,/保留机位/,'unchanged sentence stays out of diff');
 note.value='新的意见';await t.event('input',note);assert.equal(t.$('[data-fix-apply]'),null,'note edit invalidates displayed plan immediately');
 await until(()=>t.requests.some(r=>r.method==='PUT'&&r.url.endsWith('/fix-plan')));
 console.log('PASS V6 cards, defaults, seek, marks, note-only plan, sentence diff and invalidation');
}
async function flow(){
 const t=fixture();await t.click(t.$('[data-fix-plan]'));await click(t,'按方案修改');
 assert.match(t.$('#segmentBar').textContent,/按方案重画第 2 镜：1 张图，官方，按 OpenAI 计费/);assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')));
 await t.click(t.$('[data-fix-confirm]'));await until(()=>t.requests.some(r=>r.url.includes('/images/')&&r.url.endsWith('/confirm')));
 assert.match(t.$('#segmentBar').textContent,/正在重画第 2 镜…/);assert.match(t.$('[data-compare-side="target"] [data-compare-cell="c02"]').textContent,/生成中…/);
 const req=t.requests.find(r=>r.url.includes('/storyboard-edits/')&&r.url.endsWith('/preview'));assert.equal(req.body.request,'让目标人物完整入镜');assert.deepEqual(req.body.fix_plan,{rev:'r1',id:'plan-1'});
 t.run(`P.images['image-d01'].state='completed';P.runs.d01[0].fix_plan.status='ready_video';P.runs.d01[0].fix_plan.completed_cells=['c02'];renderSteps()`);
 await until(()=>!!button(t,'按方案生成视频'));assert.equal(t.$('#comparisonBoards').hidden,false);assert.match(t.$('[data-review-card]').textContent,/第 2 镜已重画/);
 await click(t,'按方案生成视频');assert.match(t.$('#segmentBar').textContent,/按方案重新生成这一段：计费约 12 秒/);
 assert.ok(!t.requests.some(r=>r.url.endsWith('/director')&&r.body.action==='rewrite'),'plan prompt never auto rewritten');
 const n=fixture();n.noRedraw=true;await n.click(n.$('[data-fix-plan]'));await click(n,'按方案修改');
 assert.match(n.$('#segmentBar').textContent,/按方案重新生成这一段：计费约 12 秒/);assert.ok(n.requests.every(r=>!r.url.includes('/images/')&&!r.url.includes('/storyboard-edits/')));
 console.log('PASS V6 redraw payment, busy overlay, stop for review, video payment and prompt-only path');
}
async function acceptanceAndFinal(){
 const t=fixture();await click(t,'不改，接受这一版');assert.ok(t.run('P.runs.d01[0].review.accepted'));assert.match(t.$('[data-review-card]').textContent,/你已接受/);assert.doesNotMatch(t.$('#step-5').textContent,/目标人物未完整入镜/);
 await click(t,'撤销');assert.equal(t.run('P.runs.d01[0].review.accepted'),undefined);
 await click(t,'不改，接受这一版');await click(t,'检查视频');assert.equal(t.run('P.runs.d01[0].review.accepted'),undefined);
 t.run(`P.final_preview={file:'final.mp4',duration:12,segments:[{id:'d01',rev:'r1'}]};renderSteps()`);
 await t.click(t.$('[data-final-check]'));assert.match(t.$('[data-final-check-card]').textContent,/整片检查.*发现 1 个问题/s);
 const video=t.$('#finalPreviewView video');video.pause=()=>{};await t.click(t.$('[data-final-seek]'));assert.equal(video.currentTime,5);
 await t.click(t.$('[data-final-issue-segment]'));assert.equal(t.run('comparisonSelection().sid'),'d01');assert.equal(t.run("v5View('d01').view"),'video');assert.match(t.$('[data-review-note]').value,/接缝处商品状态不一致/);
 assert.ok(t.requests.every(r=>!r.url.endsWith('/fix-plan')),'handoff never generates a plan');
 await t.click(t.$('[data-final-accept]'));assert.ok(t.run('P.final_preview.check.accepted'));await t.click(t.$('[data-final-accept]'));assert.equal(t.run('P.final_preview.check.accepted'),undefined);
 for(const b of t.$$('button'))if(/检查视频|出修改方案|改图方案|重写/.test(b.textContent))assert.doesNotMatch(b.textContent,/免费/);
 console.log('PASS V6 acceptance/undo/recheck, final seek and note handoff, no free model buttons');
}
async function boundaries(){
 const t=fixture();await t.click(t.$('[data-fix-plan]'));await click(t,'按方案修改');
 assert.equal(t.$('[data-director-review]').disabled,true,'a pending paid plan confirmation locks checking');
 await t.click(t.$('[data-fix-confirm]'));
 const count=t.requests.length;await t.run("applyFixPlan('d01','r1',true)");assert.equal(t.requests.length,count,'double confirmation cannot restart paid flow');
 t.run(`P.images['image-d01'].state='failed';P.images['image-d01'].error='网络超时';renderSteps()`);
 await until(()=>!!button(t,'继续按方案重画'));assert.match(t.$('#segmentBar').textContent,/网络超时/);
 await t.click(t.$('[data-fix-cancel]'));assert.deepEqual(t.requests.at(-1).body,{action:'cancel',plan_id:'plan-1'});
 const a=fixture();await click(a,'不改，接受这一版');
 a.run(`P.final_preview={file:'final.mp4',check:{status:'completed',verdict:'fix',issues:[{segment:'d01',what:'接缝人物不一致',level:'high',start:0,end:1}]}};renderSteps()`);
 await a.click(a.$('[data-final-issue-segment]'));assert.ok(a.$('[data-review-note]'),'final issue note remains visible on an accepted version');assert.match(a.$('[data-review-note]').value,/接缝人物不一致/);
 const seek=fixture();await click(seek,'分镜对照');await seek.click(seek.$('[data-review-seek]'));assert.equal(seek.run("v5View('d01').view"),'video','issue time reveals the video when clicked from storyboard comparison');
 const f=fixture();f.run(`P.final_preview={file:'old-final.mp4'};renderSteps()`);f.beforeV6=async()=>{throw Error('旧文件请求失败');};await f.click(f.$('[data-final-check]'));assert.match(f.$('[data-final-check-card]').textContent,/旧文件请求失败/);
 f.run(`P.final_preview={file:'new-final.mp4'};renderSteps()`);assert.match(f.$('[data-final-check-card]').textContent,/还没检查/);assert.doesNotMatch(f.$('[data-final-check-card]').textContent,/旧文件请求失败/);
 f.run(`P.jobs={'final-check':{state:'running'}};renderSteps()`);assert.match(f.$('[data-final-check-card]').textContent,/还没检查/,'an old worker is not a check of the newly assembled file');
 const failed=fixture();failed.run(`P.runs.d01[0].review={status:'failed',accepted:{at:'earlier'},error:'自动验收未完成，请重新验收（免费）'};renderSteps()`);
 assert.doesNotMatch(failed.$('[data-review-card]').textContent,/自动验收|重新验收|免费/,'legacy failures use current action names');assert.ok(button(failed,'撤销'),'failed recheck still allows undoing previous acceptance');
 console.log('PASS V6 confirmation lock, double-submit guard, failed redraw cancellation and accepted-note handoff');
}
async function multipleCells(){
 const t=fixture();t.run("P.runs.d01.push({...clone(P.runs.d01[0]),rev:'r2',result:'other.mp4'});renderSteps()");await t.click(t.$('[data-compare-version="r1"]'));
 await t.click(t.$('[data-fix-plan]'));t.run("P.runs.d01[0].fix_plan.redraw.push({cell:'c03',why:'补充动作',request:'保留连续动作'});renderSteps()");
 await click(t,'按方案修改');await t.click(t.$('[data-fix-confirm]'));
 const previews=()=>t.requests.filter(r=>r.url.includes('/storyboard-edits/')&&r.url.endsWith('/preview'));
 const confirms=()=>t.requests.filter(r=>r.url.includes('/images/')&&r.url.endsWith('/confirm'));
 await until(()=>confirms().length===1);assert.equal(previews().length,1,'second cell must wait for first result');
 await t.click(t.$('[data-compare-version="r2"]'));
 t.run("P.images['image-d01'].state='completed';P.runs.d01[0].fix_plan.completed_cells=['c02'];P.segment_boards.segments.d01.current='v-after-first';P.segment_boards.segments.d01.versions.push({...P.segment_boards.segments.d01.versions[0],id:'v-after-first'});P.runs.d01[0].fix_plan.expected_board='v-after-first';renderSteps()");
 await until(()=>previews().length===2);assert.deepEqual(previews().map(x=>x.body.cell_id),['c02','c03']);await until(()=>confirms().length===2);
 assert.equal(t.run("v5View('d01').rev"),'r2','queue does not change the viewed historical version');
 await t.click(t.$('[data-compare-version="r1"]'));
 t.run("P.images['image-d01-2'].state='failed';P.images['image-d01-2'].error='模拟断网';P.runs.d01[0].fix_plan.status='failed';P.runs.d01[0].fix_plan.error_reason='模拟断网';renderSteps()");
 await until(()=>t.run("fixFlows.get(directorReviewKey('d01','r1')).status")==='failed');
 await click(t,'继续按方案重画');await t.click(t.$('[data-fix-confirm]'));await until(()=>previews().length===3);
 assert.deepEqual(previews().map(x=>x.body.cell_id),['c02','c03','c03'],'retry preserves completed cells');await until(()=>confirms().length===3);
 t.run("P.images['image-d01-3'].state='completed';P.runs.d01[0].fix_plan.completed_cells=['c02','c03'];P.runs.d01[0].fix_plan.status='ready_video';renderSteps()");await until(()=>!!t.$('[data-fix-video]'));
 assert.equal(t.requests.filter(r=>r.url.includes('/tasks/')&&r.url.endsWith('/confirm')).length,0);
 console.log('PASS V6 multi-cell sequencing across version switches, partial retry and stop before video');
}
async function stalePlanPayment(){
 for(const during of [false,true]){
  const t=fixture();t.noRedraw=true;await t.click(t.$('[data-fix-plan]'));
  let release,started=false;const held=new Promise(r=>release=r);
  t.beforeRequest=async(method,url)=>{if(during&&url.includes('/tasks/')&&url.endsWith('/preview')){started=true;await held;}};
  const pending=click(t,'按方案修改');if(during)await until(()=>started);else await pending;
  const note=t.$('[data-review-note]');note.value='改变修改意见';await t.event('input',note);if(during){release();await pending;}
  assert.equal(t.$('[data-fix-plan-view]'),null);assert.equal(t.$('[data-v5-confirm]'),null,'changed plan cannot leave or reinstall its payment confirmation');
  await t.run("confirmSegmentAction('d01')");assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')));
 }
 const fs=require('node:fs'),css=fs.readFileSync('web/style.css','utf8');assert.doesNotMatch(css,/(?:^|\})\s*\.fix-step\s*\{[^}]*grid-template-columns:\s*24px/,'new plan layout must not style old fix wizard');
 console.log('PASS V6 invalidated plan blocks prepared/late video confirmation; CSS scoped to plan');
}
async function frozenModelAcrossCells(){
 // V9c: one confirmation covers the plan, with a total estimate; a mid-flow settings change stops before paying.
 const t=fixture(),inner=t.context.mockApi;let model='gpt-image-2.6-sunburst';
 t.context.mockApi=async(method,url,body)=>{const r=await inner(method,url,body);if(url.includes('/storyboard-edits/')&&url.endsWith('/preview'))r.preview={...r.preview,channel:'official',model,quality:'high'};return r;};t.run('api=mockApi');
 await t.click(t.$('[data-fix-plan]'));t.run("P.runs.d01[0].fix_plan.redraw.push({cell:'c03',why:'补充动作',request:'保留连续动作'});renderSteps()");
 await click(t,'按方案修改');await until(()=>!!t.$('[data-fix-confirm]'));
 assert.match(t.$('#segmentBar').textContent,/按方案重画第 2、3 镜：2 张图，2 张官方 · gpt-image-2\.6 sunburst · 高画质，约 \$0\.16/);
 const previews=()=>t.requests.filter(r=>r.url.includes('/storyboard-edits/')&&r.url.endsWith('/preview'));
 const confirms=()=>t.requests.filter(r=>r.url.includes('/images/')&&r.url.endsWith('/confirm'));
 assert.equal(confirms().length,0);await t.click(t.$('[data-fix-confirm]'));await until(()=>confirms().length===1);
 model='gpt-image-2';
 t.run("P.images['image-d01'].state='completed';P.runs.d01[0].fix_plan.completed_cells=['c02'];P.segment_boards.segments.d01.current='v-after-first';P.segment_boards.segments.d01.versions.push({...P.segment_boards.segments.d01.versions[0],id:'v-after-first'});P.runs.d01[0].fix_plan.expected_board='v-after-first';renderSteps()");
 await until(()=>t.run("fixFlows.get(directorReviewKey('d01','r1'))?.status")==='failed');
 assert.equal(previews().length,2);assert.equal(confirms().length,1,'changed model must not be paid without a new confirmation');
 assert.match(t.$('#segmentBar').textContent,/生图设置改了/);
 await click(t,'继续按方案重画');await until(()=>!!t.$('[data-fix-confirm]'));assert.match(t.$('#segmentBar').textContent,/1 张图，官方 · gpt-image-2 · 高画质，约 \$0\.20/);
 await t.click(t.$('[data-fix-confirm]'));await until(()=>confirms().length===2);
 console.log('PASS V6/V9c redraw plan: one confirmation with total estimate; mid-flow model change stops before paying');
}
async function main(){for(const test of [cards,flow,acceptanceAndFinal,boundaries,multipleCells,frozenModelAcrossCells,stalePlanPayment])await test();console.log('PASS all V6 UI groups');}
module.exports={fixture,main};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
