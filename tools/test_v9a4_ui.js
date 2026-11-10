/* V9a4 UI regressions: synthetic project and transport only. */
const assert=require('node:assert/strict');
const {fixture:base}=require('./test_v9a_ui');
const {fixture:boardFixture}=require('./test_v5_ui');
const click=(t,s,extra={})=>t.event('click',t.$(s),extra);
const selected=t=>JSON.parse(t.run('JSON.stringify(comparisonSelectedCells())'));
function fixture(){
 const t=base();
 t.run(`P.breakdown.characters=[{id:'C1',name:'未具名女性',role:'穿搭推荐者兼服装模特'}];P.replacements.characters.push({source_id:'C2',action:'keep'},{source_id:'N8',action:'keep'});renderReplacements(true);renderSteps()`);
 t.view().columns=t.view().columns.filter(c=>c.key!=='C2');
 Object.assign(t.view().columns[0],{source_name:'C1 未具名女性',source_description:'穿搭推荐者兼服装模特',title:'人物 · C1 未具名女性',master_status:'母版已换成：母版人物'});
 const transport=t.context.mockApi;
 t.context.mockApi=async(m,u,b)=>{
  if(u==='projects')return [{id:'sb1-offline',name:'母版'},...t.batch().items.map(i=>({id:i.project_id,name:'母版 · 裂变 '+i.row,derived_from:{project_id:'sb1-offline',row:i.row}}))];
  return transport(m,u,b);
 };t.run('api=mockApi');return t;
}
async function ghosts(){const t=fixture();
 assert.doesNotMatch(t.$('#step-3 [data-step-summary]').textContent,/C2|N8/);
 assert.equal(t.$('[data-cast-card="N8"]'),null);
 assert.equal(t.run('readReplacements().characters.filter(c=>["C2","N8"].includes(c.source_id)).length'),2,'existing ghost records preserved without mutation');
 await click(t,'[data-fission-open]');assert.equal(t.$$('[data-fission-slot]').length,2);
 assert.match(t.$('[data-fission-slot="C1"]').textContent,/人物 · C1 未具名女性/);
 assert.match(t.$('[data-fission-slot="C1"]').textContent,/穿搭推荐者兼服装模特/);
 assert.match(t.$('[data-fission-slot="C1"]').textContent,/母版已换成：母版人物/);
 assert.match(t.$('[data-fission-slot="product"]').textContent,/母版没换，用的是原片里的商品/);
 assert.match(t.$('#fissionPanel').textContent,/想换谁，就在谁的卡片里选照片/);
 const name='照片'.repeat(40);t.run(`fissionState.known.long={name:${JSON.stringify(name)}};fissionState.choices.C1=['long'];renderFission()`);
 assert.ok(t.$$('.fission-photo-name').some(el=>el.getAttribute('title')===name));
}
async function created(){const t=fixture();await click(t,'[data-fission-open]');await click(t,'[data-fission-picker="C1"]');await click(t,'[data-fission-item="person-a"]');await click(t,'[data-fission-picker-done]');await click(t,'[data-fission-create]');
 assert.match(t.$('#fissionPanel').textContent,/已创建 1 条新视频/);assert.doesNotMatch(t.$('#fissionPanel').textContent,/会出这些新视频（0 条）/);
 assert.match(t.$('#fissionPanel').textContent,/整理改法中/);assert.match(t.$('#fissionPanel').textContent,/换了：C1.*新人物/);
 assert.ok(t.$('[data-fission-project]'));assert.ok(t.$('#projectSelect option[value="child-0"]'));
 await click(t,'[data-fission-close]');await click(t,'[data-fission-open]');assert.match(t.$('#fissionPanel').textContent,/已创建 1 条新视频/);
 t.batch().items[0].status='pending_confirmation';await t.run('fissionPoll()');assert.match(t.$('#fissionPanel').textContent,/等你确认付费/);
 await click(t,'[data-fission-new-batch]');assert.ok(t.$('[data-fission-create]'));assert.equal(t.run('Object.keys(fissionState.choices).length'),0);
 assert.doesNotMatch(t.$('#fissionPanel').textContent,/免费/);
}
async function projectList(){const t=fixture();t.run(`projectList=[{id:'other',name:'别的'},{id:'child',name:'母版 · 裂变 1',derived_from:{project_id:'master',row:1}},{id:'master',name:'母版'}];renderProjectOptions('child')`);
 assert.deepEqual(t.$$('#projectSelect option').map(el=>el.value),['other','master','child','batch:master']);assert.equal(t.$('#projectSelect').value,'child');
 assert.match(t.$('#projectSelect option[value="master"]').textContent,/母版/);assert.match(t.$('#projectSelect option[value="child"]').textContent,/↳/);
 t.run(`projectList=projectList.filter(p=>p.id!=='master');renderProjectOptions('child')`);assert.doesNotMatch(t.$('#projectSelect option[value="child"]').textContent,/↳/);
 const get=t.context.mockApi;t.context.mockApi=async(m,u,b)=>u==='projects/missing'?{...JSON.parse(t.run('JSON.stringify(P)')),id:'missing',name:'当前新项目'}:get(m,u,b);t.run('api=mockApi');await t.run('openProject("missing")');assert.equal(t.$('#projectSelect').value,'missing');assert.ok(t.$('#projectSelect option[value="missing"]'));
}
async function banner(){const t=fixture();t.run(`P.derived_from={project_id:'master',master_name:'母版名字',changes:[{key:'C1',from_name:'C1 未具名女性',to_name:'照片'}],master_snapshot:{boards:{d01:{}}}};P.master_exists=true;projectList=[{id:'master',name:'母版名字'}];renderAll()`);
 assert.match(t.$('#fissionBanner').textContent,/裂变项目：照“母版名字”做的/);assert.match(t.$('#fissionBanner').textContent,/原片、拆解、分段方案照搬母版/);assert.ok(t.$('[data-open-master]'));
 t.run('P.master_exists=false;renderAll()');assert.match(t.$('#fissionBanner').textContent,/母版已删除/);assert.equal(t.$('[data-open-master]'),null);
 t.run(`P.segment_boards.segments.d01.versions=[];P.segment_boards.segments.d01.current=null;renderSteps()`);assert.match(t.$('[data-compare-preparing]').textContent,/母版的新分镜做底/);assert.equal(t.$('[data-compare-side] h3'),null);assert.equal(t.$('[data-casting-stale]'),null);
}
async function errors(){const t=fixture(),raw='HTTP 403 rejected by the safety system request_id=req_'+ 'x'.repeat(200)+' https://example.invalid/'+ 'y'.repeat(100);t.context.raw=raw;
 t.run(`P.images.failure={id:'failure',state:'failed',channel:'official',error:raw,storyboard_context:{sheet_id:'d01'}};renderSteps()`);
 assert.match(t.$('#segmentBar').textContent,/官方的安全审核没有通过这次生图/);const details=t.$('#segmentBar .error-details');assert.ok(details);assert.equal(details.open,false);assert.match(details.textContent,/request_id=req_/);assert.ok(t.$('#segmentBar [data-copy-error]'));
 assert.doesNotMatch(t.$('#segmentBar').textContent,/没有扣费/);assert.ok(t.$('[data-edit-third-retry]'));
}
async function multiselect(){const t=boardFixture();
 await click(t,'[data-compare-column="target"][data-compare-cell="c01"]');await click(t,'[data-compare-column="target"][data-compare-cell="c02"]',{shiftKey:true});await click(t,'[data-compare-column="target"][data-compare-cell="c04"]',{metaKey:true});
 assert.deepEqual(selected(t),['c01','c02','c04']);assert.equal(t.$$('.compare-cell.is-cell').length,3);assert.match(t.$('#segmentCellEditor').textContent,/第 1、2、4 镜要改什么/);assert.match(t.$('#segmentCellEditor').textContent,/只重画这几镜，其他镜不动/);assert.match(t.$('[data-compare-selection-count]').textContent,/已选 3 镜/);
 await click(t,'[data-compare-column="target"][data-compare-cell="c02"]',{shiftKey:true});assert.deepEqual(selected(t),['c01','c04']);
 await click(t,'[data-compare-column="target"][data-compare-cell="c05"]',{metaKey:true});assert.deepEqual(selected(t),['c01','c04']);assert.match(t.$('[data-compare-selection-hint]').textContent,/一次只能选同一段里的镜头/);
 t.run('P=clone(P);renderSteps()');assert.deepEqual(selected(t),['c01','c04']);await t.event('keydown',t.$('#step-4'),{key:'Escape'});assert.deepEqual(selected(t),[]);
 await click(t,'[data-compare-column="target"][data-compare-cell="c02"]');assert.equal(t.run('comparisonSelection().cell'),'c02');await click(t,'[data-compare-column="target"][data-compare-cell="c02"]');assert.equal(t.run('comparisonSelection().cell'),null);
 t.run(`P.images.busy={state:'running',storyboard_context:{sheet_id:'d01',cell_id:'c02'}};renderSteps()`);await click(t,'[data-compare-column="target"][data-compare-cell="c02"]');assert.deepEqual(selected(t),[]);
}
async function multirequest(){const t=boardFixture(),normal=t.context.mockApi;t.context.mockApi=async(m,u,b)=>{if(u.endsWith('/preview')&&b?.cells){t.requests.push({method:m,url:u,body:structuredClone(b)});const p=JSON.parse(t.run('JSON.stringify(P)')),board=p.segment_boards.segments.d01;return {id:'multi-image',channel:'official',state:'pending_confirmation',preview:{},storyboard_context:{sheet_id:'d01',selected_cells:b.cells,plan_sig:board.plan.sig,storyboard_sig:board.storyboard_sig,base_current:board.current,cells_revision:{why:'只改选中格',cells:b.cells.map(cell=>({cell,changes:'调整光线',keep:'其他内容'}))}}};}return normal(m,u,b);};t.run('api=mockApi');
 await click(t,'[data-compare-column="target"][data-compare-cell="c01"]');await click(t,'[data-compare-column="target"][data-compare-cell="c02"]',{shiftKey:true});const input=t.$('[data-edit-cell-request]');input.value='调整光线';await t.event('input',input);await click(t,'[data-edit-cell-preview]');
 assert.deepEqual(t.requests.filter(r=>r.url.endsWith('/preview')).at(-1).body,{cells:['c01','c02'],request:'调整光线'});assert.match(t.$('.inline-confirm').textContent,/重画第 1、2 镜：1 张图/);assert.ok(!t.requests.some(r=>r.url.endsWith('/confirm')),'no payment before user confirmation');
}
async function draftRevisions(){
 for(const loseResponse of [false,true]){
  const t=fixture(),transport=t.context.mockApi;let revision=0,created=null,lost=false;
  t.view().draft.revision=revision;
  t.context.mockApi=async(m,u,b)=>{
   if(u.endsWith('/fission-draft') && m==='PUT'){assert.equal(b.revision,revision,'only current draft may save');const out=await transport(m,u,b);t.view().draft.revision=++revision;return {...out,draft:{...out.draft,revision}};}
   if(u.endsWith('/fission') && m==='POST'){
    if(created){t.requests.push({method:m,url:u,body:structuredClone(b)});assert.equal(b.request_id,created.request);return {...created.result,draft_revision:revision};}
    assert.equal(b.revision,revision);const result=await transport(m,u,b);t.view().draft.revision=++revision;created={request:b.request_id,result};if(loseResponse && !lost){lost=true;throw Error('response lost');}return {...result,draft_revision:revision};
   }
   return transport(m,u,b);
  };t.run('api=mockApi');await click(t,'[data-fission-open]');await click(t,'[data-fission-picker="C1"]');await click(t,'[data-fission-item="person-a"]');await click(t,'[data-fission-picker-done]');await click(t,'[data-fission-create]');
  if(loseResponse)await click(t,'[data-fission-create]');assert.match(t.$('#fissionPanel').textContent,/已创建 1 条新视频/);
  await click(t,'[data-fission-new-batch]');assert.equal(t.run('fissionState.unsaved'),false);assert.equal(t.run('Object.keys(fissionState.choices).length'),0);assert.match(t.$('#fissionPanel').textContent,/还没选新照片/);assert.doesNotMatch(t.$('#fissionPanel').textContent,/整理改法中/);
 }
}
async function copyingAndUpload(){const t=fixture(),raw='HTTP 500 request_id=req_'+ 'x'.repeat(200);let copied='';t.context.navigator={clipboard:{writeText:async text=>{copied=text;}}};t.context.raw=raw;t.run(`P.images.failure={id:'failure',state:'failed',channel:'official',error:raw,storyboard_context:{sheet_id:'d01'}};renderSteps()`);await click(t,'#segmentBar [data-copy-error]');assert.equal(copied,raw);
 let url='';t.context.uploadStub=async(u)=>{url=u;return {item:{id:'a',name:'人物 1'},duplicate:false};};t.run('uploadPhoto=uploadStub');await t.run(`uploadPhotos([{name:'ChatGPT Image long export.png'}],'person',()=>{},()=>{})`);assert.equal(url,'library?kind=person');
}
async function batchCompletion(){const t=fixture();await click(t,'[data-fission-open]');t.run(`fissionState.showProgress=true;fissionState.batches=[{items:[{row:1,project_id:'child',status:'completed',slots:[]}]}];renderFission()`);assert.match(t.$('#fissionPanel').textContent,/已生成新分镜/);assert.doesNotMatch(t.$('.fission-progress-row').textContent,/整理改法中/);t.run(`fissionState.batches[0].items[0].status='image_failed';renderFission()`);assert.match(t.$('.fission-progress-row').textContent,/生图失败，打开第 04 步重试/);assert.equal(t.$('[data-fission-retry]'),null);}
async function preparingSelection(){const t=boardFixture(),base=t.context.mockApi;let release,entered=false;const gate=new Promise(resolve=>release=resolve);t.context.mockApi=async(m,u,b)=>{if(u.endsWith('/preview') && b?.cells){entered=true;await gate;return {id:'held-multi',state:'pending_confirmation',channel:'official',preview:{},storyboard_context:{sheet_id:'d01',selected_cells:b.cells}};}return base(m,u,b);};t.run('api=mockApi');
 await click(t,'[data-compare-column="target"][data-compare-cell="c01"]');await click(t,'[data-compare-column="target"][data-compare-cell="c02"]',{shiftKey:true});const input=t.$('[data-edit-cell-request]');input.value='调整光线';await t.event('input',input);const preparing=click(t,'[data-edit-cell-preview]');await require('./test_v5_ui').until(()=>entered);
 assert.equal(t.$('[data-compare-column="target"][data-compare-cell="c04"]').disabled,true);assert.match(t.$('[data-compare-selection-hint]').textContent,/正在整理所选镜头的改法/);t.run(`selectComparisonCell('c04','target',{shiftKey:true})`);assert.deepEqual(selected(t),['c01','c02']);
 await t.event('keydown',t.$('#step-4'),{key:'Escape'});release();await preparing;assert.deepEqual(selected(t),[]);assert.equal(t.$('[data-v5-confirm]'),null,'Esc during preparation never confirms the obsolete selection');assert.match(t.$('#segmentBar').textContent,/没有进入付费确认/);
}
async function main(){for(const test of [ghosts,created,projectList,banner,errors,multiselect,multirequest,draftRevisions,copyingAndUpload,batchCompletion,preparingSelection]){await test();console.log('PASS V9a4 '+test.name);}}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={fixture,main};
