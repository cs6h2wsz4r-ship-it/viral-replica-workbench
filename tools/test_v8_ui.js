/* V8: single-cell revision reasoning, real DOM/events and synthetic transports only. */
const assert=require('node:assert/strict');
const {fixture:base,until}=require('./test_v5_ui');

function fixture(){
 const t=base(),normal=t.context.mockApi;
 t.run("Object.values(P.segment_boards.segments).forEach(board=>board.settings_sig='settings-current')");
 t.context.mockApi=async(method,url,body)=>{
  const rec=await normal(method,url,body);
  if(url.includes('/storyboard-edits/') && url.endsWith('/preview') && body.cell_id && !body.fix_plan){
   const original=JSON.parse(t.run(`JSON.stringify(editSheetState('d01').plan.cells.find(c=>c.cell===${JSON.stringify(body.cell_id)}))`));
   const count=t.requests.filter(r=>r.url===url).length;
   rec.storyboard_context.settings_sig=t.run("editSheetState('d01').settings_sig");
   rec.storyboard_context.cell_revision={changes:'让人物左手自然放在扶手上，袖子改成浅米色。<img src=x onerror=bad>',keep:'保留机位、背景与商品外形。',why:'把手和袖子改成你指定的样子。<script>bad</script>',sig:'cell-revision-'+count,revised_by_request:body.request,original_plan:original,base_plan_sig:rec.storyboard_context.plan_sig,storyboard_sig:rec.storyboard_context.storyboard_sig,settings_sig:rec.storyboard_context.settings_sig};
   rec.storyboard_context.cell_revision_sig=rec.storyboard_context.cell_revision.sig;
  }
  return rec;
 };
 t.run('api=mockApi');return t;
}
async function selectCell(t){
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 const input=t.$('[data-edit-cell-request="d01"]');input.value='手放在扶手上，袖子改为浅色';await t.event('input',input);
}
const redraw=t=>t.click(t.$('[data-edit-cell-preview="d01"]'));
const requests=(t,suffix)=>t.requests.filter(r=>r.url.endsWith(suffix));

async function preparation(){
 const t=fixture();await selectCell(t);let entered=false,release;
 const gate=new Promise(resolve=>release=resolve);
 t.beforeRequest=async(method,url)=>{if(url.endsWith('/preview')){entered=true;await gate;}};
 const work=redraw(t);await until(()=>entered);
 assert.equal(t.$('#segmentCellEditor .action-progress').textContent,'正在整理这一镜的改法…');
 assert.equal(t.$('[data-edit-cell-request]').disabled,true);
 assert.equal(t.$('[data-v5-confirm]'),null);assert.equal(requests(t,'/confirm').length,0);
 assert.doesNotMatch(t.$('#segmentCellEditor').textContent,/免费/);
 await t.run("prepareSegmentAction('d01','cell')");assert.equal(requests(t,'/preview').length,1);
 release();await work;
 assert.deepEqual(t.requests.map(r=>[r.method,r.url.split('/').slice(2).join('/')]),[['PUT','storyboard-edits/d01'],['POST','storyboard-edits/d01/preview']]);
 assert.deepEqual(requests(t,'/preview')[0].body,{cell_id:'c02',request:'手放在扶手上，袖子改为浅色'});
 console.log('PASS V8 cell reasoning progress, input lock and existing preview route only');
}

async function confirmation(){
 const t=fixture();await selectCell(t);
 const plan=t.run("JSON.stringify(editSheetState('d01').plan)");await redraw(t);
 const row=t.$('#segmentCellEditor [aria-label="付费确认"]');assert.ok(row);
 assert.match(row.textContent,/按你的要求：把手和袖子改成你指定的样子/);
 assert.match(row.textContent,/1 张图，官方，按 OpenAI 计费/);assert.doesNotMatch(row.textContent,/免费/);
 const details=[...row.querySelectorAll('details')].find(d=>d.querySelector('summary')?.textContent==='展开完整改法');assert.ok(details);
 assert.equal(details.open,false);assert.match(details.textContent,/让人物左手自然放在扶手上/);assert.match(details.textContent,/保留机位、背景与商品外形/);
 assert.equal(row.querySelector('script'),null);assert.equal(row.querySelector('img'),null);
 const current=t.$$('#segmentCellEditor p').find(p=>p.textContent.startsWith('这一镜当前的改图方案：'));assert.ok(current);
 assert.match(current.textContent,/这一镜当前的改图方案：让人物左手自然放在扶手上/);
 assert.equal(t.run("JSON.stringify(editSheetState('d01').plan)"),plan,'a cell revision never mutates the full board plan');
 assert.equal(t.run("editSheetState('d01').cell_revisions.c02.sig"),'cell-revision-1');
 assert.equal(t.run("editSheetState('d01').cell_revisions.c03"),undefined);
 assert.equal(t.run("v5BoardPreview('d01','c02')?.id"),'image-d01','the received revision is immediately available to preview validation');
 assert.equal(requests(t,'/confirm').length,0);
 await t.click(t.$('[data-v5-confirm="d01"]'));
 assert.deepEqual(requests(t,'/confirm').map(r=>r.url.split('/').slice(2).join('/')),['images/image-d01/confirm']);
 console.log('PASS V8 escaped why/details, current proposal, isolated revision and explicit image payment');
}

async function failureAndCancel(){
 const t=fixture();await selectCell(t);
 t.beforeRequest=async(method,url)=>{if(url.endsWith('/preview'))throw Error('这一镜的改法整理失败：推理服务暂时不可用 <img src=x onerror=bad>');};
 await redraw(t);
 const action=t.$('#segmentCellEditor .cell-action');assert.ok(action.querySelector('[data-edit-cell-preview]'));
 assert.match(action.querySelector('[role="alert"]').textContent,/推理服务暂时不可用/);
 assert.equal(action.querySelector('img'),null);assert.equal(action.querySelector('[data-v5-confirm]'),null);
 assert.equal(t.$('[data-v5-confirm]'),null);assert.equal(requests(t,'/confirm').length,0);
 assert.doesNotMatch(action.textContent,/免费/);
 t.beforeRequest=null;await redraw(t);assert.ok(t.$('[data-v5-confirm]'));
 await t.click(t.$('[data-v5-cancel]'));assert.equal(t.$('[data-v5-confirm]'),null);assert.equal(requests(t,'/confirm').length,0);
 assert.ok(t.$('[data-edit-cell-preview]'));
 console.log('PASS V8 escaped inline failure reason, retry and cancellation without payment');
}

async function revisionBinding(){
 const t=fixture();await selectCell(t);await redraw(t);
 assert.ok(t.run("editSheetState('d01').cell_revisions?.c02"),'preview response installs the cell revision');
 t.run("editSheetState('d01').cell_revisions.c02={...editSheetState('d01').cell_revisions.c02,sig:'changed-elsewhere'}");
 assert.equal(t.run("v5BoardPreview('d01','c02')"),undefined,'a superseded cell revision cannot supply a reusable preview');
 await t.click(t.$('[data-v5-confirm]'));
 assert.equal(requests(t,'/confirm').length,0,'a changed revision must be prepared again before payment');
 assert.equal(requests(t,'/preview').length,2);assert.equal(t.run("editSheetState('d01').cell_revisions.c02.sig"),'cell-revision-2');
 assert.ok(t.$('[data-v5-confirm]'));
 console.log('PASS V8 changed cell revision invalidates preview and payment binding');
}

async function staleResponse(){
 const t=fixture();await selectCell(t);let entered=false,release;
 const gate=new Promise(resolve=>release=resolve),normal=t.context.mockApi;
 t.context.mockApi=async(method,url,body)=>{const result=await normal(method,url,body);if(url.endsWith('/preview')){entered=true;await gate;}return result;};t.run('api=mockApi');
 const work=redraw(t);await until(()=>entered);
 t.run("P={...clone(P),id:'another-project'};renderSteps()");release();await work;
 assert.equal(t.run('P.id'),'another-project');assert.equal(t.run("editSheetState('d01').cell_revisions"),undefined);
 assert.equal(t.$('[data-v5-confirm]'),null);assert.equal(requests(t,'/confirm').length,0);
 console.log('PASS V8 late revision response cannot mutate another project or install confirmation');
}

async function staleRevisionDisplay(){
 const cases=[['plan',"editSheetState('d01').plan={...editSheetState('d01').plan,sig:'new-full-plan',cells:editSheetState('d01').plan.cells.map(c=>({...c,changes:'整段新方案里的改法'}))};newStoryboardDrafts.clear()"],['storyboard',"editSheetState('d01').storyboard_sig='updated-storyboard'"],['settings',"editSheetState('d01').settings_sig='updated-settings'"],['legacy',"delete editSheetState('d01').cell_revisions.c02.base_plan_sig"],...['plan_stale','casting_stale','source_stale'].map(status=>[status,`P.segment_board_status.d01={${status}:true}`])];
 for(const [name,mutate] of cases){
  const t=fixture();await selectCell(t);await redraw(t);await t.click(t.$('[data-v5-cancel]'));
  assert.match(t.$('#segmentCellEditor').textContent,/这一镜当前的改图方案：让人物左手自然放在扶手上/);
  t.run(mutate);const saved=t.run("JSON.stringify(editSheetState('d01').cell_revisions.c02)");t.run('renderSteps()');
  assert.doesNotMatch(t.$('#segmentCellEditor').textContent,/让人物左手自然放在扶手上/,name+' invalidation must not display historical revision as current');
  if(name==='plan')assert.match(t.$('#segmentCellEditor').textContent,/这一镜当前的改图方案：整段新方案里的改法/);
  assert.equal(t.run("JSON.stringify(editSheetState('d01').cell_revisions.c02)"),saved,'history is retained after '+name+' invalidation');
  assert.equal(requests(t,'/confirm').length,0);
 }
 console.log('PASS V8 historical revision hidden after full-plan, storyboard, settings and stale-status changes');
}

async function main(){const errors=[];for(const test of [preparation,confirmation,failureAndCancel,revisionBinding,staleResponse,staleRevisionDisplay]){try{await test();}catch(error){console.error(test.name+': '+error.message);errors.push(error);}}if(errors.length)throw new AggregateError(errors,'V8 UI assertions failed');}
module.exports={fixture,main};if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
