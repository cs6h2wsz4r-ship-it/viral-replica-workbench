/* V2 actual renderers and delegated events, with synthetic projects and mocked HTTP. */
const assert=require('node:assert/strict');
const {setup}=require('./test_storyboard_sb1_ui');
function seed(t){
 t.run(`P.storyboard.cells=P.storyboard.cells.slice(0,8);
 P.segments_plan={format_version:2,summary:'先门廊，后室内',why_segments:'地点和商品状态变化',skip_cells:[],questions:[],segments:[
 {id:'d01',title:'门廊跌倒',cells:['c01','c02','c03'],mode:'video',refs:['C1'],what:'父亲在门廊跌倒',mode_reason:'本段没有商品',source_start:0,source_end:5.4,duration:6,gen_images:['c01','C1']},
 {id:'d02',title:'室内求助',cells:['c04','c05','c06','c07','c08'],mode:'image',refs:['C1','product'],what:'展示并戴表',mode_reason:'原片有旧商品特写',source_start:5.4,source_end:12.3,duration:7,gen_images:['c04','C1','product']}]};
 P.segment_boards={segments:Object.fromEntries(P.segments_plan.segments.map((s,i)=>[s.id,{source:{...s,file:s.id+'-source.jpg',cols:i?3:2,rows:2},current:'v1',versions:[{id:'v1',from:'full',slicer:2,file:s.id+'-new.jpg',cells:Object.fromEntries(s.cells.map(c=>[c,c+'-new.jpg']))}]}]))};
 P.director_status={ready:true};
 P.director={format_version:3,summary:'按段写提示词',segments:P.segments_plan.segments.map(s=>({...s,images:[s.cells[0],...s.refs],gen_images:[s.cells[0],...s.refs],prompt:'English prompt',timeline:[],check:['看手表状态']}))};renderSteps()`);
}
async function main(){
 const t=setup();
 assert.equal(t.$$('[data-flow-index]').length,5);
 assert.match(t.$('#step-4').textContent,/对照和修改/);
 for(const id of ['newStoryboards','directorView','directorGeneration'])assert.ok(t.$('#step-4 #'+id));
 assert.match(t.$('#step-5').textContent,/05.*成片/);
 seed(t);
 assert.equal(t.$$('#selectedSegmentHeader h3').length,1);
 assert.match(t.$('#segmentAdvanced').textContent,/为什么是 2 段/);
 assert.match(t.$('#selectedSegmentHeader').textContent,/本段生成方式和素材/);
 assert.equal(t.$$('#segmentPlanView .director-panels button').length,0);
 assert.equal(t.$$('[data-segment-split]').length,0);assert.equal(t.$$('[data-segment-merge]').length,0);
 assert.equal(t.$$('[data-edit-sheet]').length,1);
 for(const [id,first,count] of [['d01','c01',3],['d02','c04',5]]){
  await t.click(t.$(`[data-compare-side="source"] [data-compare-cell="${first}"]`));
  assert.ok(t.$(`[data-edit-sheet="${id}"]`));assert.equal(t.$$('[data-edit-sheet]').length,1);
  for(const side of ['source','target']){
   const cells=t.$$(`[data-compare-side="${side}"] [data-compare-cell]`);
   assert.equal(cells.length,8);assert.equal(cells.filter(c=>c.dataset.compareSegment===id).length,count);
   assert.equal(cells.filter(c=>c.getAttribute('aria-pressed')==='true').length,count);
  }
 }
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));
 assert.equal(t.$$('[data-director-mode]').length,0);assert.equal(t.$$('[data-director-ref]').length,0);
 assert.match(t.$('#directorView').textContent,/生成全部提示词|生成后重点看|完整提示词/);
 const beforePlan=t.context.mockApi;
 t.context.mockApi=async(method,url,body)=>{
  if(!url.includes('/segment-plan/'))return beforePlan(method,url,body);
  t.requests.push({method,url,body:structuredClone(body)});
  const p=JSON.parse(t.run('JSON.stringify(P)')),id=url.split('/').at(-1),s=p.segments_plan.segments.find(s=>s.id===id);
  if(body.mode)s.mode=body.mode;if(body.refs)s.refs=body.refs;
  const warning='第 04 步改过这一段，重新生成这一段的新分镜。';
  p.segment_board_status={[id]:{source_stale:true,warnings:[warning],active_materials:Object.fromEntries(['C1','C2','N1','product'].map(k=>[k,s.refs.includes(k)]))}};
  p.director_status={ready:false,segments:[{id,stale:true,warnings:[warning]},{id:'d02',stale:false}]};return p;
 };t.run('api=mockApi');
 t.$('[data-detail-key="segment-plan:d01"]').open=true;
 const mode=t.$('[data-segment-mode="d01"]');mode.value='image';await t.event('change',mode);
 assert.equal(t.requests.at(-1).method,'PUT');assert.match(t.requests.at(-1).url,/segment-plan\/d01$/);assert.equal(t.requests.at(-1).body.mode,'image');
 assert.doesNotMatch(t.$('#step-4').textContent,/第 04 步改过这一段/);assert.equal(t.run('comparisonNeedsBoard(P.segments_plan.segments[0])'),true);
 assert.match(t.$('#directorView').textContent,/先完成上面的新分镜/);assert.equal(t.$('[data-director-card="d01"]'),null);
 assert.ok(t.$('[data-edit-preview="d01"]'),'source changes require a new storyboard');
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c04"]'));assert.equal(t.$('[data-director-preview="d02"]').disabled,false);
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));
 t.run(`P.assets.push({id:'son',type:'image',file:'son.jpg'},{id:'doctor',type:'image',file:'doctor.jpg'});P.replacements.characters.push({source_id:'C2',action:'replace',asset_id:'son'},{source_id:'N1',action:'add',asset_id:'doctor',name:'医生'});P.storyboard.cells[0].characters.push('C2','N1');P.segment_board_status={};renderSteps()`);
 assert.equal(t.$('[data-segment-ref]'),null,'V5a: segment refs are no longer editable photo authority');
 assert.match(t.$('[data-segment-photos="d01"]').textContent,/C1.*C2.*N1.*商品/);
 for(const key of ['C1','C2','N1','product'])assert.equal(t.$('[data-edit-material="'+key+'"]').checked,true,'V5a: each live saved photo defaults on');
 assert.deepEqual(JSON.parse(t.run('JSON.stringify(P.segments_plan.segments[0].refs)')),['C1'],'live photos do not rewrite historical segment refs');
 assert.match(t.$('#finalPreviewView').textContent,/第 1 段还没有选定的结果，在第 04 步选中这一段，在“视频”里生成或选定一版。/);
 assert.equal(t.$('[data-final-preview]').disabled,true);
 t.run(`P.director.segments.forEach(s=>{s.selected_run='r1';P.runs[s.id]=[{rev:'r1',state:'completed',result:s.id+'.mp4'}]});renderSteps()`);
 assert.equal(t.$('[data-final-preview]').disabled,false);
 const base=t.context.mockApi;t.context.mockApi=async(method,url,body)=>{t.requests.push({method,url,body});if(url.endsWith('/final-preview'))return {...t.project,final_preview:{file:'final/preview-test.mp4',duration:13}};return base(method,url,body);};t.run('api=mockApi');
 await t.click(t.$('[data-final-preview]'));assert.match(t.requests.at(-1).url,/final-preview$/);assert.match(t.$('#finalPreviewView video').src,/preview-test/);assert.match(t.$('#finalPreviewView').textContent,/13/);
 t.run(`P.segment_board_status={};P.director={segments:[{id:'old',timeline:[]}]};renderDirector()`);
 assert.match(t.$('#directorView').textContent,/生成视频/);
 assert.equal(t.$$('[data-director-card]').length,0);
 console.log('PASS V2: five steps, selected segment controls, matched 3/5-cell groups, prompt ownership, final selection/assembly, legacy notice');
}
module.exports={main,seed};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
