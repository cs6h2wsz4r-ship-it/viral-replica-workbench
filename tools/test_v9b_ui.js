/* V9b dashboard: synthetic data, actual DOM/events, no transport outside the VM. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {fixture: base, until} = require('./test_v5_ui');
const click = (t, selector) => t.click(t.$(selector));
const duplicate = value => JSON.parse(JSON.stringify(value));

function fixture() {
  const t = base();
  const progress = (done, state='completed', detail='') => ({done,total:2,state,detail});
  const row = (id, number) => ({project_id:id,name:'母版 · 裂变 '+number,number,
    changes:'C1 → 素材 '+number+'；商品 → 素材 5',photos:[{file:'assets/person.jpg',name:'素材 '+number}],
    boards:[{file:'analysis/new.jpg',cell_id:'c01',index:1,segment_id:'d01',segment_index:1,version:'v2',start:0,end:3}],
    progress:{boards:progress(2),prompts:progress(2),videos:progress(1,'running','第 2 段生成中'),final:progress(0,'idle')},errors:[]});
  const first=row('child-1',1), second=row('child-2',2);
  first.progress.videos=progress(2);first.progress.final=progress(1);first.final={file:'outputs/final.mp4'};
  second.errors=[{segment_id:'d02',segment_index:2,stage:'videos',error:'HTTP 502 gateway error request_id=example',retry:{kind:'video-query',rev:'r2'}}];
  second.progress.videos=progress(1,'failed','第 2 段等待查询');
  const data={master:{id:'sb1-offline',name:'母版',exists:true,photos:[{file:'assets/master.jpg',name:'母版照片'}],boards:duplicate(first.boards)},total:2,completed:1,active:true,
    can_prepare_images:true,can_prepare_videos:true,
    batches:[{id:'new',created_at:'2026-11-07T10:20:00+08:00',items:[second]},{id:'old',created_at:'2026-11-06T09:00:00+08:00',items:[first]}],
    pending:{images:{token:'image-token',project_count:2,image_count:3,items:[{project_id:'child-1',name:'裂变 1',segment_id:'d01',image_id:'i1',channel:'official'},{project_id:'child-2',name:'裂变 2',segment_id:'d01',image_id:'i2',channel:'official'},{project_id:'child-2',name:'裂变 2',segment_id:'d02',image_id:'i3',channel:'third_party'}]},
      videos:{token:'video-token',project_count:2,billing_seconds:24.5,output_seconds:18,reference_seconds:6.5,items:[{project_id:'child-1',name:'裂变 1',segment_id:'d01',rev:'r1',billing_seconds:12,output_seconds:12,reference_seconds:0},{project_id:'child-2',name:'裂变 2',segment_id:'d02',rev:'r2',billing_seconds:12.5,output_seconds:6,reference_seconds:6.5}]}}};
  const oldApi=t.context.mockApi;
  t.context.mockApi=async(method,url,body)=>{
    if(url.startsWith('fission-boards/')) {
      t.requests.push({method,url,body:duplicate(body ?? null)});
      if(t.beforeBoardRequest) await t.beforeBoardRequest(method,url,body);
      return duplicate(data);
    }
    if(url.includes('/resume')) {t.requests.push({method,url,body});return {ok:true};}
    return oldApi(method,url,body);
  };
  t.data=data;t.run(`api=mockApi;projectList=[{id:'sb1-offline',name:'母版'},{id:'child-1',name:'母版 · 裂变 1',derived_from:{project_id:'sb1-offline',row:1}},{id:'child-2',name:'母版 · 裂变 2',derived_from:{project_id:'sb1-offline',row:2}}];renderProjectOptions();`);
  t.run(fs.readFileSync('web/app.js','utf8').split('\n').find(line=>line.startsWith('$("#projectSelect").onchange')));
  return t;
}

async function dashboardAndEntrances() {
  const t=fixture();
  assert.equal(t.run('typeof openBatchBoard'),'function','dashboard entry must exist');
  assert.ok(t.$('#step-5 [data-batch-board]'));
  await click(t,'#step-5 [data-batch-board]');
  assert.equal(t.$('#workspace').hidden,true);
  assert.equal(t.$('#batchBoard').hidden,false);
  assert.match(t.$('#batchBoard').textContent,/2 条新片 · 1 条已出片/);
  assert.deepEqual(t.$$('#batchBoard [data-batch-project-row]').map(n=>n.dataset.batchProjectRow),['child-2','child-1']);
  assert.match(t.$('#batchBoard').textContent,/11\/7 10:20 创建 · 1 条/);
  assert.match(t.$('#batchBoard').textContent,/新分镜 2\/2.*提示词 2\/2.*视频 1\/2/s);
  assert.match(t.$('#batchBoard').textContent,/第 2 段等待查询/);
  assert.match(t.$('[data-batch-download]').getAttribute('href'),/\/files\/child-1\/outputs\/final.mp4$/);
  assert.equal(t.$('#deleteProjectBtn').disabled,true,'a board is not a deletable project');
  assert.doesNotMatch(t.$('#batchBoard').textContent,/免费|没有扣费/);
  t.run(`P.derived_from={project_id:'sb1-offline',master_snapshot:{name:'母版'},changes:[]};P.master_exists=false;renderFissionBanner()`);
  assert.ok(t.$('#fissionBanner [data-batch-board]'));
}

async function dropdownAndDeletedMaster() {
  const t=fixture();
  assert.equal(t.$$('#projectSelect option[value="batch:sb1-offline"]').length,1);
  assert.match(t.$('#projectSelect option[value="batch:sb1-offline"]').textContent,/批量看板（2 条）/);
  await t.$('#projectSelect').onchange({target:{value:'batch:sb1-offline'}});
  assert.equal(t.$('#projectSelect').value,'batch:sb1-offline');
  t.data.master.exists=false;t.run("projectList=projectList.filter(p=>p.id!=='sb1-offline')");
  await t.run('refreshBatchBoard()');
  assert.match(t.$('#batchBoard [data-batch-master]').textContent,/母版已删除/);
  assert.equal(t.$('#batchBoard [data-batch-master] [data-batch-open-project]'),null);
  assert.equal(t.$('[data-batch-new]').disabled,true);
  assert.equal(t.$('#projectSelect').value,'batch:sb1-offline');
  assert.doesNotMatch(t.$('#projectSelect option[value="child-1"]').textContent,/↳/);
}

async function confirmationAndCancel() {
  const t=fixture();await t.run("openBatchBoard('sb1-offline')");
  assert.match(t.$('[data-batch-confirmation="images"]').textContent,/生成 2 条裂变的新分镜：3 张图/);
  assert.match(t.$('[data-batch-confirmation="images"]').textContent,/三方渠道，约 0.02 点/);
  assert.match(t.$('[data-batch-confirmation="videos"]').textContent,/计费约 24.5 秒（输出 18 秒；带原片视频的段另算参考视频 6.5 秒）/);
  for(const kind of ['images','videos']) {
    const details=t.$(`[data-batch-confirmation="${kind}"] details`);details.open=true;
    assert.match(details.textContent,/每条各多少.*裂变 1.*裂变 2/s);
  }
  await t.run('refreshBatchBoard()');assert.equal(t.$('[data-batch-confirmation="images"] details').open,true,'poll preserves expanded rows');
  await click(t,'[data-batch-cancel="images"]');await t.run('refreshBatchBoard()');
  assert.equal(t.$('[data-batch-confirmation="images"]'),null);
  assert.equal(t.requests.filter(r=>r.method!=='GET').length,0,'cancel and reads cannot mutate or submit');
  await click(t,'[data-batch-show="images"]');
  await click(t,'[data-batch-confirm="images"]');
  assert.deepEqual(t.requests.find(r=>r.url.endsWith('/images/confirm')).body,{token:'image-token'});
  await click(t,'[data-batch-confirm="videos"]');
  assert.deepEqual(t.requests.find(r=>r.url.endsWith('/videos/confirm')).body,{token:'video-token'});
}

async function changedListRequiresNewClick() {
  const t=fixture();await t.run("openBatchBoard('sb1-offline')");
  t.beforeBoardRequest=async(method,url)=>{if(url.endsWith('/confirm')){t.data.pending.images.token='fresh-token';throw Error('刚才有内容改过，清单已更新，请再确认一次');}};
  await click(t,'[data-batch-confirm="images"]');
  assert.match(t.$('#batchBoard').textContent,/刚才有内容改过，清单已更新，请再确认一次/);
  assert.equal(t.requests.filter(r=>r.url.endsWith('/confirm')).length,1,'no automatic retry of payment');
  assert.equal(t.$('[data-batch-confirm="images"]').disabled,false);
  t.beforeBoardRequest=null;await click(t,'[data-batch-confirm="images"]');
  assert.equal(t.requests.filter(r=>r.url.endsWith('/confirm')).at(-1).body.token,'fresh-token');
}

async function retryAndPrepare() {
  const t=fixture();await t.run("openBatchBoard('sb1-offline')");
  assert.match(t.$('.batch-row-errors').textContent,/第 2 段/);
  assert.ok(t.$('.batch-row-errors .error-details'));
  await click(t,'[data-batch-retry]');
  assert.ok(t.requests.some(r=>r.url==='projects/child-2/tasks/d02/resume' && r.body.rev==='r2'));
  assert.equal(t.requests.some(r=>r.url.endsWith('/confirm')),false);
  await click(t,'[data-batch-prepare="images"]');await click(t,'[data-batch-prepare="videos"]');
  assert.ok(t.requests.some(r=>r.method==='POST' && r.url.endsWith('/images/prepare')));
  assert.ok(t.requests.some(r=>r.method==='POST' && r.url.endsWith('/videos/prepare')));
  assert.doesNotMatch(t.$('#batchBoard').textContent,/免费/);
}

async function readOnlyLargePreview() {
  const t=fixture();await t.run("openBatchBoard('sb1-offline')");const before=t.requests.length;
  const magnifier=t.$('[data-batch-project-row="child-2"] [data-batch-zoom]');
  await t.click(magnifier);
  assert.equal(t.$('#storyboardLightbox').hidden,false);
  assert.equal(t.$('#storyboardLightbox img').src,'/files/child-2/analysis/new.jpg');
  assert.equal(t.$('[data-story-preview-edit]'),null);
  assert.ok(t.$('#storyboardLightbox [data-batch-open-project="child-2"]'));
  assert.equal(t.requests.length,before,'enlarging existing image makes no business API requests');
  await click(t,'[data-story-preview-close]');
  await t.event('dblclick',t.$('[data-batch-project-row="child-2"] [data-batch-thumb]'));
  assert.equal(t.$('#storyboardLightbox').hidden,false);
  await click(t,'[data-story-preview-close]');
  await t.event('dblclick',t.$('[data-batch-master] [data-batch-thumb]'));
  assert.equal(t.$('#storyboardLightbox img').src,'/files/sb1-offline/analysis/new.jpg');
}

async function stablePollingAndLateResponse() {
  const t=fixture();await t.run("openBatchBoard('sb1-offline')");
  const row=t.$('[data-batch-project-row="child-2"]');await t.run('refreshBatchBoard()');
  assert.equal(t.$('[data-batch-project-row="child-2"]'),row,'unchanged rows are not replaced');
  let entered=false,release;const gate=new Promise(resolve=>release=resolve);
  t.beforeBoardRequest=async(method)=>{if(method==='GET'){entered=true;await gate;}};
  const p=t.run('refreshBatchBoard()');await until(()=>entered);const count=t.requests.length;
  const q=t.run('refreshBatchBoard()');assert.equal(t.requests.length,count,'overlapping refreshes share one request');
  await t.run("openProject('sb1-offline')");release();await Promise.all([p,q]);
  assert.equal(t.$('#batchBoard').hidden,true,'late board response cannot reopen it');
  assert.equal(t.$('#workspace').hidden,false);
  assert.equal(t.$('#deleteProjectBtn').disabled,false);
}

async function failedOpenPreservesDashboard() {
  const t=fixture();await t.run("openBatchBoard('sb1-offline')");
  const transport=t.context.mockApi;t.context.mockApi=async(m,u,b)=>{if(u==='projects/child-2')throw Error('读取失败');return transport(m,u,b);};t.run('api=mockApi');
  await click(t,'[data-batch-open-project="child-2"]');
  assert.equal(t.$('#batchBoard').hidden,false,'failed navigation preserves the actionable board');
  assert.ok(t.$('[data-batch-refresh]'));assert.equal(t.$('#projectSelect').value,'batch:sb1-offline');
  assert.equal(t.$('#deleteProjectBtn').disabled,true);
  await t.run('refreshBatchBoard()');assert.match(t.$('#batchBoard').textContent,/2 条新片/);
}

async function newBatchCannotClearOtherProject() {
  const t=fixture();t.context.AbortController=AbortController;
  const transport=t.context.mockApi;
  t.context.mockApi=async(m,u,b)=>{
    if(['projects/sb1-offline','projects/other'].includes(u))return {...JSON.parse(t.run('JSON.stringify(P)')),id:u.split('/')[1],final_preview_status:{ready:true,warnings:[]}};
    if(u==='library')return {items:[]};
    if(u.endsWith('/fission-draft')){t.requests.push({method:m,url:u,body:b});return {ready:true,columns:[],draft:{choices:{},removed:[]},rows:[],batches:[]};}
    return transport(m,u,b);
  };t.run("api=mockApi;P.id='child-1'");await t.run("openBatchBoard('sb1-offline')");
  let entered=false,release;const gate=new Promise(resolve=>release=resolve);
  t.beforeBoardRequest=async()=>{entered=true;await gate;};
  const pending=t.run('batchNewBatch()');await until(()=>entered);
  await t.run("openProject('other')");release();await pending;
  assert.equal(t.requests.some(r=>r.method==='PUT' && r.url==='projects/other/fission-draft'),false,'late new-batch request must never clear an unrelated draft');
  assert.equal(t.$('#fissionPanel').hidden,true);
}

async function createdEntryAndFreshEligibility() {
  const t=require('./test_v9a_ui').fixture(),transport=t.context.mockApi;
  const board={master:{id:'sb1-offline',name:'母版',exists:true},batches:[],total:0,completed:0,pending:{images:{items:[]},videos:{items:[]}}};
  t.context.mockApi=async(m,u,b)=>u.startsWith('fission-boards/')?duplicate(board):transport(m,u,b);t.run('api=mockApi');
  await click(t,'[data-fission-open]');await click(t,'[data-fission-picker="C1"]');await click(t,'[data-fission-item="person-a"]');await click(t,'[data-fission-picker-done]');await click(t,'[data-fission-create]');
  assert.ok(t.$('#fissionPanel [data-batch-board]'),'created view exposes the master board');
  await click(t,'#fissionPanel [data-batch-board]');assert.equal(t.$('#fissionPanel').hidden,true);
  t.run('P.final_preview_status={ready:false,warnings:["缓存里的旧状态"]}');
  await click(t,'[data-batch-new]');
  assert.equal(t.$('#fissionPanel').hidden,false,'new batch rechecks server eligibility rather than cached project status');
  assert.equal(t.run('fissionState.showProgress'),false);assert.equal(t.run('fissionState.rows.length'),0);
  assert.ok(t.$('[data-fission-create]'));assert.doesNotMatch(t.$('#fissionPanel').textContent,/已创建/);
}

async function main(){for(const test of [dashboardAndEntrances,dropdownAndDeletedMaster,confirmationAndCancel,changedListRequiresNewClick,retryAndPrepare,readOnlyLargePreview,stablePollingAndLateResponse,failedOpenPreservesDashboard,newBatchCannotClearOtherProject,createdEntryAndFreshEligibility]){await test();console.log('PASS V9b '+test.name);}}
module.exports={fixture,main};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
