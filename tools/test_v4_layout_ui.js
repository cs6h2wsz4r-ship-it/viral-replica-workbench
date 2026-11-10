/* V4: actual renderers/events, synthetic data and mocked HTTP only. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {setup}=require('./test_storyboard_sb1_ui');
const {seed}=require('./test_v2_ui');
function fixture(){
 const t=setup({autoConfirm:false});seed(t);
 t.run(`P.storyboard.cells.push({id:'c09',index:9,start:8,end:9,file:'skip.jpg',characters:[]});P.segments_plan.skip_cells=['c09'];
 P.segments_plan.segments[0].cells=['c01','c02','c03','c04'];P.segments_plan.segments[1].cells=['c05','c06','c07','c08'];
 P.segment_boards.segments.d01.source.cells=P.segments_plan.segments[0].cells;P.segment_boards.segments.d02.source.cells=P.segments_plan.segments[1].cells;
 P.director.segments=P.segments_plan.segments.map(s=>({...s,prompt:'图1 新画面 图2 人物照 视频1 动作',check:['外观正确']}));
 P.segment_boards.segments.d01.versions[0].cells.c04='c04-new.jpg';renderSteps()`);
 return t;
}
async function main(){
 const t=fixture();
 assert.equal(t.$$('[data-flow-index]').length,5,'five workflow steps');
 assert.deepEqual(JSON.parse(t.run('JSON.stringify(workflowState().map(r=>r.label))')),['01 上传对标视频','02 拆解对标视频','03 选角和商品','04 对照和修改','05 成片']);
 const raw=t.$$('[data-compare-side="source"] [data-compare-cell]'),target=t.$$('[data-compare-side="target"] [data-compare-cell]');
 assert.equal(raw.length,9);assert.equal(target.length,9);
 assert.deepEqual(raw.map(x=>x.dataset.compareCell),target.map(x=>x.dataset.compareCell));
 for(const cell of t.$$('[data-compare-cell="c09"]')){assert.equal(cell.disabled,true);assert.match(cell.textContent,/不复刻/);assert.match(cell.getAttribute('class'),/is-skipped/);}
 assert.match(raw[0].getAttribute('aria-label'),/第 1 段.*第 1 镜/);
 assert.equal(t.$$('[data-compare-cell][aria-pressed="true"]').length,8);
 assert.equal(t.$$('.compare-cell.is-muted').length,8);
 const paths=t.$$('[data-segment-outline="d01"]');assert.equal(paths.length,2);
 assert.equal(paths[0].getAttribute('d'),paths[1].getAttribute('d'));
 assert.equal((paths[0].getAttribute('d').match(/M/g)||[]).length,1,'cross-row segment is one continuous outline');assert.match(paths[0].getAttribute('d'),/Z$/);
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 assert.equal(t.run('comparisonSelection().cell'),'c02');assert.ok(t.$('.compare-cell.is-cell'));assert.match(t.$('#segmentBar').textContent,/正在改第 2 镜/);
 assert.ok(t.$('[data-edit-cell-preview="d01"]'));assert.equal(t.$('#segmentAdvanced').hidden,true,'advanced panel hides while editing one cell');
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c02"]'));assert.equal(t.run('comparisonSelection().cell'),null);
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c03"]'));
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c06"]'));
 assert.equal(t.run('comparisonSelection().sid'),'d02');assert.equal(t.run('comparisonSelection().cell'),null);
 t.run('P=clone(P);preserveView(()=>renderSteps())');assert.equal(t.run('comparisonSelection().sid'),'d02');
 for(const selector of ['[data-edit-sheet]','[data-director-card]'])assert.equal(t.$$(selector).length,1);
 assert.ok(t.$('[data-edit-sheet="d02"]'));assert.ok(t.$('[data-director-prompt="d02"]'));assert.ok(t.$('[data-director-save]'));assert.ok(t.$('[data-director-preview="d02"]'));
 assert.equal(t.$('[data-detail-key="director-prompt:d02"]').open,false);
 const css=fs.readFileSync('web/style.css','utf8');assert.match(css,/@media[^}]*max-width:\s*899px/);assert.match(css,/\.comparison-columns\s*\{\s*grid-template-columns:\s*1fr/);
 await concurrencyTests();
 await operationTests();
 await edgeTests();
 await batchTests();
 console.log('PASS V4: five steps, paired grid, disabled skips, selection, continuous outline, single-cell toggle, preserved selection and scoped controls');
}
async function concurrencyTests(){
 const t=batchFixture(),base=t.context.mockApi;let release,started=false;
 const wait=new Promise(resolve=>release=resolve);
 t.context.mockApi=async(method,url,body)=>{
  if(url.endsWith('/plan')){t.requests.push({method,url,body});const original=JSON.parse(t.run('JSON.stringify(P)'));started=true;await wait;return original;}
  return base(method,url,body);
 };
 t.run(`api=mockApi;P.segment_boards.segments.d01.plan=null;newStoryboardDrafts.clear();renderSteps()`);
 const batch=t.click(t.$('[data-comparison-batch="boards"]'));await until(()=>started);
 t.run(`P={...clone(P),id:'another-project'};renderSteps()`);release();await batch;
 assert.ok(t.requests.every(r=>!r.url.includes('another-project')),'a late batch plan must never preview/save against another project');
 const c=batchFixture(),transport=c.context.mockApi;let saved=false,resume;
 const saving=new Promise(resolve=>resume=resolve);
 c.context.mockApi=async(method,url,body)=>{
  if(method==='PUT' && url.endsWith('/storyboard-edits/d01')){c.requests.push({method,url,body});const old=JSON.parse(c.run('JSON.stringify(P)'));saved=true;await saving;return old;}
  return transport(method,url,body);
 };
 c.run('api=mockApi');await c.click(c.$('[data-compare-side="target"] [data-compare-cell="c02"]'));
 const input=c.$('[data-edit-cell-request="d01"]');input.value='只改第2镜';await c.event('input',input);
 const preview=c.click(c.$('[data-edit-cell-preview="d01"]'));await until(()=>saved);
 await c.click(c.$('[data-compare-side="source"] [data-compare-cell="c05"]'));resume();await preview;
 assert.deepEqual(c.requests.find(r=>r.url.endsWith('/preview')).body,{cell_id:'c02',request:'只改第2镜'},'cell request stays bound to the clicked shot across awaits');
 const settings=batchFixture(),normal=settings.context.mockApi;let finish,writing=false;
 const held=new Promise(resolve=>finish=resolve);
 settings.context.mockApi=async(method,url,body)=>{
  if(url.endsWith('/segment-plan/d01')){settings.requests.push({method,url,body});const p=JSON.parse(settings.run('JSON.stringify(P)'));p.segments_plan.segments[0].mode=body.mode;writing=true;await held;return p;}
  return normal(method,url,body);
 };settings.run('api=mockApi');
 const mode=settings.$('[data-segment-mode="d01"]');mode.focus();mode.value='video';const modeSave=settings.event('change',mode);await until(()=>writing);
 assert.equal(settings.$('[data-segment-mode="d01"]').disabled,true,'moved mode control is locked while saving');
 assert.ok(settings.$$('[data-edit-material]').length>0);assert.ok(settings.$$('[data-edit-material]').every(el=>el.disabled),'V5a material controls are locked while saving generation mode');
 finish();await modeSave;assert.equal(settings.$('[data-segment-mode="d01"]').value,settings.run('P.segments_plan.segments[0].mode'));
 console.log('PASS V4 concurrency: late project switch cannot mutate another project; cell preview remains bound while selection changes');
}
async function operationTests(){
 const t=batchFixture();
 t.run(`P.segment_boards.segments.d01.versions.push({...clone(P.segment_boards.segments.d01.versions[0]),id:'v2',file:'v2.jpg'});
 P.images.preview={id:'preview',state:'pending_confirmation',channel:'official',preview:{prompt:'生图预览'},storyboard_context:{sheet_id:'d01',plan_sig:'current'}};
 P.runs.d01=[{rev:'r-preview',state:'pending_confirmation',billing_seconds:8,job_sha256:'sha',preview:{tasks:[{prompt:'视频预览'}]}},{rev:'r1',state:'completed',result:'result.mp4',review:{status:'completed',verdict:'fix',issues:[{level:'high',start:0,end:1,what:'画面需调整',fix:'redraw_cell',how:'点这一镜重画'}]}}];renderSteps()`);
 assert.ok(t.$('#segmentBar [data-v5-confirm="d01"]'),'valid pending board preview restores its inline confirmation');
 await t.click(t.$('#segmentBar [data-v5-cancel="d01"]'));
 assert.equal(t.$('#segmentBar [data-v5-confirm="d01"]'),null,'explicit cancellation returns to idle operation controls');
 for(const selector of ['[data-segment-mode="d01"]','[data-edit-plan="d01"]','[data-edit-request="d01"]','[data-edit-material]','[data-edit-save="d01"]','[data-edit-preview="d01"]','[data-edit-use="v2"]','[data-director-prompt="d01"]','[data-director-materials="d01"]','[data-director-save]','[data-director-rewrite="d01"]','[data-director-preview="d01"]','[data-director-use="d01"]','[data-director-review="d01"]','[data-fix-plan="d01"]'])assert.ok(t.$(selector),'original operation retained: '+selector);
 assert.equal(t.$('[data-detail-key="edit-plan:d01"]').open,false);
 assert.equal(t.$('[data-detail-key="director-prompt:d01"]').open,false);
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c01"]'));
 assert.equal(t.$('[data-edit-confirm]'),null,'a whole-board preview cannot be mistaken for selected-cell payment');
 const input=t.$('[data-edit-cell-request="d01"]');input.value='只修改这一镜';await t.event('input',input);
 await t.click(t.$('[data-edit-cell-preview="d01"]'));
 assert.deepEqual(t.requests.at(-1).body,{cell_id:'c01',request:'只修改这一镜'});
 assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')));
 console.log('PASS V4 operations: mode/materials, plan/edit/version, cell redraw, prompt/edit/save, video preview/confirm/use/review/review rewrite');
}
async function edgeTests(){
 const t=fixture();
 for(const indices of [[2,3],[1,2,3],[5,6],[2,3,4,5,6]]){
  const outline=t.run('comparisonOutline('+JSON.stringify(indices)+')');
  assert.equal((outline.match(/M/g)||[]).length,1,'every contiguous cross-row segment has a connected outline: '+indices);
 }
 t.run(`P.director.segments[0].needs_rewrite=true;renderSteps()`);
 assert.ok(t.$('[data-director-preview]'),'needs_rewrite is repaired by the video action');assert.equal(t.run('comparisonNeedsPrompt(P.segments_plan.segments[0])'),true);
 t.run(`P.director.segments[0].needs_rewrite=false;P.director_status.segments=[{id:'d01',stale:true}];renderSteps()`);
 assert.ok(t.$('[data-director-preview]'),'stale prompt can be repaired automatically');
 const b=batchFixture();
 b.run(`P.segment_board_status={d01:{warnings:['选角或商品改过，改图方案可能要重新生成']}}`);
 assert.equal(b.run('comparisonNeedsBoard(P.segments_plan.segments[0])'),true,'stale settings cannot be skipped as latest');
 t.run(`P.director_status.segments=[];P.director.segments.forEach(s=>{s.selected_run='r1';P.runs[s.id]=[{rev:'r1',state:'completed',result:'video.mp4'}]});comparisonSelections.clear();renderSteps()`);
 assert.equal(t.run('comparisonSelection().sid'),'d01','all complete defaults to first');
 t.run(`P.director.segments[1].selected_run=null;comparisonSelections.clear();renderSteps()`);
 assert.equal(t.run('comparisonSelection().sid'),'d02','defaults to first segment needing work');
 const current=t.run('JSON.stringify(P)');t.context.savedProject=JSON.parse(current);
 t.run(`P={...clone(P),id:'another-project'};selectComparisonCell('c01','source');P=clone(savedProject);renderSteps()`);
 assert.equal(t.run('comparisonSelection().sid'),'d02','selection is isolated per project');
 console.log('PASS V4 edges: short row-wrap outline, upstream gating, stale settings, default selection and project isolation');
}
function batchFixture(){
 const t=fixture();
 t.run(`P.segments_plan.skip_cells=[];P.segments_plan.segments=[0,1,2].map(i=>({id:'d0'+(i+1),title:'段 '+(i+1),cells:P.storyboard.cells.slice(i*3,i*3+3).map(c=>c.id),source_start:i*3,source_end:i*3+3,duration:4,mode:'image',refs:[],gen_images:[P.storyboard.cells[i*3].id]}));
 P.segment_boards.segments=Object.fromEntries(P.segments_plan.segments.map(s=>[s.id,{source:{...s,file:'source.jpg',cols:2,rows:2},request:'',materials:{},plan:{sig:'current',cells:s.cells.map(cell=>({cell,changes:'替换',keep:'场景'})),summary:'方案'},current:'v1',versions:[{id:'v1',from:'full',slicer:2,file:'new.jpg',plan_sig:'current',cells:Object.fromEntries(s.cells.map(c=>[c,c+'-new.jpg']))}]}]));
 P.director={format_version:3,segments:P.segments_plan.segments.map(s=>({...s,prompt:'图1 新画面',check:['检查外观']}))};P.director_status={ready:true,segments:[]};directorDrafts.clear();newStoryboardDrafts.clear();renderSteps()`);
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body:structuredClone(body)});
  const p=JSON.parse(t.run('JSON.stringify(P)')),parts=url.split('/'),id=parts[parts.indexOf('storyboard-edits')+1] || '';
  if(url.includes('/storyboard-edits/') && url.endsWith('/preview')){
   if(t.context.previewFailure===id)throw Error('预览失败 '+id);
   return {id:'image-'+id,state:'pending_confirmation',channel:'third_party',preview:{prompt:'预览'},storyboard_context:{sheet_id:id,plan_sig:'current'}};
  }
  if(url.includes('/images/') && url.endsWith('/confirm')){
   if(t.context.confirmFailure===parts.at(-2))throw Error('提交失败 '+parts.at(-2));return {ok:true};
  }
  if(url.includes('/tasks/') && url.endsWith('/preview')){
   const sid=parts.at(-2);if(t.context.previewFailure===sid)throw Error('预览失败 '+sid);
   return {rev:'r-new',state:'pending_confirmation',billing_seconds:8,job_sha256:sid+'-sha',preview:{tasks:[{prompt:'完整提示词'}]}};
  }
  if(url.includes('/tasks/') && url.endsWith('/confirm')){
   if(t.context.confirmFailure===parts.at(-2))throw Error('提交失败 '+parts.at(-2));return {ok:true};
  }
  if(url.endsWith('/director') && body?.action==='rewrite'){
   const row=p.director_status.segments.find(s=>s.id===body.segment_id);if(row)row.stale=false;
  }
  return p;
 };t.run('api=mockApi');return t;
}
async function until(check){for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setImmediate(r));}assert.fail('async UI did not reach expected state');}
async function batchTests(){
 const t=batchFixture();
 t.run(`P.segment_boards.segments.d02.versions[0].plan_sig='old';P.segment_boards.segments.d03.versions[0].plan_sig='old';renderSteps()`);
 t.context.confirmFailure='image-d02';
 const task=t.click(t.$('[data-comparison-batch="boards"]'));
 await until(()=>t.$('#pageConfirmation').hidden===false);
 assert.equal(t.requests.filter(r=>r.url.endsWith('/preview')).length,2);
 assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')),'preview must never pay');
 assert.match(t.$('#pageConfirmation').textContent,/共 2 段.*合计计费.*0.04/s);
 assert.equal(t.$$('#pageConfirmation [data-page-confirm="accept"]').length,1);
 assert.ok(t.requests.every(r=>!r.url.includes('/storyboard-edits/d01')),'latest board skipped');
 await t.click(t.$('[data-page-confirm="accept"]'));await task;
 assert.equal(t.requests.filter(r=>r.url.endsWith('/confirm')).length,2,'one confirmation continues after segment failure');
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c04"]'));
 assert.match(t.$('#segmentAction').textContent,/提交失败 image-d02/);
 const v=batchFixture();v.context.previewFailure='d02';
 v.run(`P.runs.d01=[{rev:'r1',state:'completed',result:'video.mp4'}];renderSteps()`);
 const videos=v.click(v.$('[data-comparison-batch="videos"]'));await until(()=>v.$('#pageConfirmation').hidden===false);
 assert.match(v.$('#pageConfirmation').textContent,/共 1 段.*8.*计费秒/s);
 assert.equal(v.requests.filter(r=>r.url.endsWith('/preview')).length,2);
 await v.click(v.$('[data-page-confirm="accept"]'));await videos;
 assert.deepEqual(v.requests.filter(r=>r.url.endsWith('/confirm')).map(r=>r.body),[{rev:'r-new',job_sha256:'d03-sha'}]);
 assert.match(v.run('comparisonBatches.get(P.id).errors.d02'),/预览失败/);
 const cancel=batchFixture();const cancelled=cancel.click(cancel.$('[data-comparison-batch="videos"]'));await until(()=>cancel.$('#pageConfirmation').hidden===false);
 await cancel.click(cancel.$('[data-page-confirm="cancel"]'));await cancelled;
 assert.ok(cancel.requests.every(r=>!r.url.endsWith('/confirm')));
 const changed=batchFixture(),base=changed.context.mockApi;
 changed.context.mockApi=async(method,url,body)=>{
  if(url.endsWith('/replacements')){changed.requests.push({method,url,body});const p=JSON.parse(changed.run('JSON.stringify(P)'));p.replacements=body;p.segment_board_status={d01:{warnings:['选角或商品改过，改图方案可能要重新生成']}};return p;}
  return base(method,url,body);
 };
 changed.run(`api=mockApi;castingDraft().characters[0].description='目标人物的新要求';dirty.rep=true;renderSteps()`);
 await changed.click(changed.$('[data-comparison-batch="boards"]'));
 assert.equal(changed.requests.length,0,'V5a: batch warns about unsaved casting and never autosaves');
 assert.match(changed.$('#toast').textContent,/选角或商品改了还没保存/);
 await changed.run('saveReplacements()');
 const changedTask=changed.click(changed.$('[data-comparison-batch="boards"]'));
 await until(()=>changed.$('#pageConfirmation').hidden===false);await changed.click(changed.$('[data-page-confirm="cancel"]'));await changedTask;
 const p=batchFixture();p.run(`P.director_status.segments=[{id:'d02',stale:true}];renderSteps()`);
 assert.equal(p.$('[data-comparison-batch="prompts"]'),null);await p.run("comparisonBatch('prompts')");
 assert.deepEqual(p.requests.filter(r=>r.url.endsWith('/director')).map(r=>r.body),[{action:'rewrite',segment_id:'d02'}]);
 assert.equal(p.$('#pageConfirmation').hidden,true);
 console.log('PASS V4 batch: previews before one in-page confirmation, totals, latest skipped, preview/submit failures isolated, cancellation and prompt rewrite');
}
module.exports={main,fixture};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
