/* V5a: saved casting, live photos and segment-scoped prompt preparation. Offline only. */
const assert=require('node:assert/strict');
const {fixture,button,click,until}=require('./test_v5_ui');
const warning='选角或商品改了还没保存，点“保存”后，后面生成才会用新设定。';

async function unsavedTests(){
 for(const [selector,value,event] of [
  ['[data-cast-action="C1"]','keep','change'],
  ['[data-cast-asset="C1"]','other','change'],
  ['[data-cast-description="C1"]','新的人物描述','input'],
  ['[data-cast-action="product"]','keep','change'],
  ['[data-cast-asset="product"]','other','change'],
  ['[data-cast-description="product"]','新的商品描述','input']
 ]){
  const t=fixture();t.run(`P.assets.push({id:'other',type:'image',file:'other.jpg',roles:['person_identity','product_truth']});renderReplacements(true)`);
  const saved=t.run('JSON.stringify(P.replacements)'),input=t.$(selector);input.value=value;await t.event(event,input);
  assert.equal(t.$('#repHint').textContent,warning,selector+' warns beside Save');
  const notice=t.$('#segmentBar [data-casting-save-focus]');assert.equal(notice?.textContent,warning,selector+' warns in segment bar');
  assert.equal(t.run('JSON.stringify(P.replacements)'),saved,'unsaved edits do not replace saved settings');
  await t.click(notice);assert.equal(t.document.activeElement,t.$('#saveReplacements'),'warning focuses Step03 Save');
  assert.doesNotMatch(t.$('#step-3').getAttribute('class'),/is-collapsed/);
  for(const label of ['整段重画','生成视频','全部生成新分镜','全部生成视频']){
   assert.equal(button(t,label).disabled,false,'unsaved edits leave generation clickable');await click(t,label);
   assert.equal(t.$('#toast').textContent,warning);assert.equal(t.requests.length,0,'warning prevents generation and automatic save');
  }
  await t.run('saveReplacements()');
  assert.equal(t.requests.filter(r=>r.url.endsWith('/replacements')).length,1,'only explicit save persists casting');
  assert.equal(t.$('#repHint').textContent,'');assert.equal(t.$('#segmentBar [data-casting-save-focus]'),null);
 }
 const failed=fixture();const note=failed.$('[data-cast-description="C1"]');note.value='保存失败后保留';await failed.event('input',note);
 failed.beforeRequest=async(method,url)=>{if(url.endsWith('/replacements'))throw Error('保存失败');};
 await assert.rejects(failed.run('saveReplacements()'),/保存失败/);
 assert.equal(failed.$('#repHint').textContent,warning);assert.equal(failed.$('#segmentBar [data-casting-save-focus]').textContent,warning);
 console.log('PASS V5a all casting edits warn twice; focus, explicit save, failure and no generation autosave');
}

async function savedPhotosTests(){
 const t=fixture();
 t.run(`P.segments_plan.segments.forEach(s=>s.refs=[]);P.segment_board_status={};P.replacements.characters[0].action='keep';P.replacements.product.action='keep';newStoryboardDrafts.clear();renderReplacements(true);renderSteps()`);
 assert.equal(t.$$('[data-edit-material]').length,0);
 const replace=t.$('[data-cast-action="C1"]');replace.value='replace';await t.event('change',replace);t.run('renderSteps()');
 assert.equal(t.$$('[data-edit-material]').length,0,'unsaved replacement cannot appear in generation materials');
 await t.run('saveReplacements()');
 assert.equal(t.$('[data-edit-material="C1"]').checked,true,'saved replacement enables photo even though segment refs were empty');
 assert.equal(t.$('[data-segment-ref]'),null,'segment plan no longer edits authoritative photo choices');
 assert.match(t.$('#selectedSegmentHeader').textContent,/第 03 步.*保存/);
 const keep=t.$('[data-cast-action="C1"]');keep.value='keep';await t.event('change',keep);await t.run('saveReplacements()');
 assert.equal(t.$('[data-edit-material="C1"]'),null,'saved keep removes photo');
 t.run(`P.storyboard.cells.forEach(c=>{c.characters=[];c.product='';c.product_later=false;});P.storyboard.cells[0].product_later=true;P.replacements.product.action='replace';newStoryboardDrafts.clear();renderSteps()`);
 assert.equal(t.$('[data-edit-material="product"]').checked,true,'product_later counts as a product appearance');
 await t.click(t.$('[data-compare-segment-tab="d02"]'));assert.equal(t.$('[data-edit-material="product"]'),null,'segments without product do not use its photo');
 t.run(`P.segment_board_status.d02={materials:[{key:'product',name:'商品',file:'latest.jpg',enabled:true}],active_materials:{product:false}};renderSteps()`);
 assert.equal(t.$('[data-edit-material="product"]').checked,false,'server material switch is authoritative');
 assert.match(t.$('.edit-material img').src,/latest.jpg/);
 t.run(`P.segment_board_status.d02.materials[0].file='new-photo.jpg';P.segment_board_status.d02.active_materials.product=true;renderSteps()`);
 assert.equal(t.$('[data-edit-material="product"]').checked,true,'new saved photo resets previous switch state');
 console.log('PASS V5a materials follow saved casting, cell presence and authoritative switch state');
}

async function staleBoardTests(){
 const t=fixture();t.run(`P.segment_board_status.d01={casting_stale:true,plan_stale:true};renderSteps()`);
 assert.ok(t.$('[data-compare-side="target"] [data-compare-cell="c01"] [data-casting-stale]'));
 assert.equal(t.$('[data-casting-stale]').textContent,'按旧选角生成');
 assert.equal(t.$('[data-compare-side="source"] [data-casting-stale]'),null);
 assert.ok(button(t,'生成新分镜','#segmentBar'));assert.equal(button(t,'生成视频','#segmentBar'),undefined);
 await click(t,'生成新分镜');
 assert.deepEqual(t.requests.filter(r=>/\/(plan|preview)$/.test(r.url)).map(r=>r.url.split('/').at(-1)),['plan','preview']);
 assert.equal(t.requests.find(r=>r.url.endsWith('/plan')).body.materials.C1,true);
 assert.ok(button(t,'确认'));assert.ok(t.requests.every(r=>!r.url.endsWith('/confirm')));
 console.log('PASS V5a old casting badge and primary action rebuild plan before paid confirmation');
}

async function singlePromptTests(){
 for(const batch of [false,true]){
  const t=fixture();t.run(`P.director=null;P.director_status={ready:false,segments:[]};P.segment_boards.segments.d02.current=null;P.segment_boards.segments.d02.versions=[];directorDrafts.clear();renderSteps()`);
  const task=click(t,batch?'全部生成视频':'生成视频');
  if(batch){await until(()=>!t.$('#pageConfirmation').hidden);await t.click(t.$('[data-page-confirm="cancel"]'));}
  await task;
  assert.deepEqual(t.requests.filter(r=>r.url.endsWith('/director')).map(r=>({method:r.method,body:r.body})),[{method:'PUT',body:{action:'rewrite',segment_id:'d01'}}]);
  assert.ok(t.requests.some(r=>r.url.endsWith('/tasks/d01/preview')));
  assert.ok(t.requests.every(r=>!r.url.endsWith('/tasks/d02/preview')));
  assert.ok(!t.run('P.director.segments.find(s=>s.id==="d02")?.prompt'),'unready second segment stays unwritten');
 }
 const empty=fixture();empty.run(`P.director.segments[0].prompt='';P.director.segments[0].needs_rewrite=false;directorDrafts.clear();renderSteps()`);
 await click(empty,'生成视频');
 assert.deepEqual(empty.requests.filter(r=>r.url.endsWith('/director')).map(r=>r.body),[{action:'rewrite',segment_id:'d01'}],'an existing empty segment is still a missing prompt');
 console.log('PASS V5a single and all-video actions initialize only ready segment through PUT rewrite');
}

async function saveRaceTests(){
 const t=fixture();let release,waiting=false;const held=new Promise(r=>release=r);
 const first=t.$('[data-cast-description="C1"]');first.value='本次保存的描述';await t.event('input',first);
 t.beforeRequest=async(method,url)=>{if(url.endsWith('/replacements')){waiting=true;await held;}};
 const saving=t.run('saveReplacements()');await until(()=>waiting);
 const latest=t.$('[data-cast-description="C1"]');latest.value='保存期间又改的描述';await t.event('input',latest);
 release();await saving;
 assert.equal(t.$('[data-cast-description="C1"]').value,'保存期间又改的描述','late explicit save cannot erase new edits');
 assert.equal(t.run('P.replacements.characters[0].description'),'本次保存的描述','generation authority is exactly the saved request');
 assert.equal(t.$('#repHint').textContent,warning);assert.equal(t.$('#segmentBar [data-casting-save-focus]').textContent,warning);
 console.log('PASS V5a edits made while explicit save is pending remain unsaved');
}

async function preparationEditTests(){
 for(const during of [false,true]){
  const t=fixture();let release,waiting=false;const held=new Promise(r=>release=r);
  t.beforeRequest=async(method,url)=>{if(during && !waiting && url.endsWith('/preview')){waiting=true;await held;}};
  const pending=click(t,'整段重画');if(during)await until(()=>waiting);else await pending;
  const input=t.$('[data-cast-description="C1"]');input.value='准备期间的新选角';await t.event('input',input);
  if(during){release();await pending;}
  if(button(t,'确认'))await click(t,'确认');
  assert.equal(t.requests.filter(r=>r.url.endsWith('/replacements')).length,0,'editing during/after preparation cannot autosave');
  assert.equal(t.requests.filter(r=>r.url.endsWith('/confirm')).length,0,'old preview cannot pay with unsaved edits');
  assert.equal(t.$('#segmentBar [data-casting-save-focus]').textContent,warning);
 }
 const saved=fixture(),normal=saved.context.mockApi;
 saved.context.mockApi=async(method,url,body)=>{const next=await normal(method,url,body);if(url.endsWith('/replacements'))next.segment_board_status.d01.casting_stale=true;return next;};saved.run('api=mockApi');
 await click(saved,'整段重画');assert.ok(button(saved,'确认'));
 const note=saved.$('[data-cast-description="C1"]');note.value='确认前保存的新选角';await saved.event('input',note);await saved.run('saveReplacements()');
 assert.ok(!button(saved,'确认'),'successful explicit save removes the old paid confirmation');
 assert.ok(button(saved,'生成新分镜','#segmentBar'));
 console.log('PASS V5a edits during preparation keep draft and prevent stale payment without autosaving');
}

async function main(){
 const failures=[];for(const test of [unsavedTests,savedPhotosTests,staleBoardTests,singlePromptTests,preparationEditTests,saveRaceTests]){
  try{await test();}catch(error){failures.push(test.name);console.error('FAIL '+test.name+'\n'+error.stack);}
 }
 assert.deepEqual(failures,[],'all V5a behavior groups pass');
}
module.exports={main};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
