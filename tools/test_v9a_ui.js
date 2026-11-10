/* V9a2: photo choices, derived combinations and library. Synthetic API only. */
const assert=require('node:assert/strict');
const {fixture:baseFixture,until}=require('./test_v5_ui');
function fixture(){
 const t=baseFixture();t.context.AbortController=AbortController;
 const items=[{id:'person-a',kind:'person',name:'新人物',url:'/api/library/person-a/file',sha256:'new-person',project_count:2},
 {id:'person-b',kind:'person',name:'另一个人',url:'/api/library/person-b/file',sha256:'person-b',project_count:0},
 {id:'product-a',kind:'product',name:'新商品',url:'/api/library/product-a/file',sha256:'new-product'},
 {id:'same-person',kind:'person',name:'母版照片副本',sha256:'master-person'}];
 let view={ready:true,warnings:[],columns:[{key:'C1',kind:'person',name:'主角',master_name:'母版人物',file:'dad.jpg',sha256:'master-person',action:'replace'},
 {key:'C2',kind:'person',name:'配角',master_name:'原片里的配角',file:null,action:'keep'},
 {key:'product',kind:'product',name:'商品',master_name:'原片商品',file:'cell-05.jpg',action:'keep',original_frame:true}],draft:{choices:{},removed:[],story_cache:{}},rows:[],row_errors:{},product_checks:{},batches:[]};
 let batch={id:'batch-a',master_id:'sb1-offline',items:[]};
 const derive=()=>{let rows=[{}];for(const c of view.columns)rows=rows.flatMap(r=>(view.draft.choices[c.key]?.length?view.draft.choices[c.key]:[null]).map(id=>({...r,[c.key]:id})));view.rows=rows.filter(r=>Object.values(r).some(Boolean)).map(selections=>({id:Object.values(selections).map(i=>i||'master').join(':'),selections})).filter(r=>!view.draft.removed.includes(r.id));};
 t.run('P.final_preview_status={ready:true,warnings:[]};P.library_saved_assets=[];renderSteps();renderReplacements(true)');
 t.context.mockApi=async(method,url,body)=>{
  t.requests.push({method,url,body:structuredClone(body)});if(t.beforeRequest)await t.beforeRequest(method,url,body);
  if(url==='library' && method==='GET')return structuredClone({items});
  if(url.endsWith('/library') && method==='POST')return {item:items[0],duplicate:false};
  if(url.endsWith('/library-import'))return {asset:{id:'imported',type:'image',file:'imported.png'},duplicate:t.importDuplicate||false};
  if(url.endsWith('/fission-draft')){if(method==='PUT'){view.draft.choices=structuredClone(body.choices);view.draft.removed=structuredClone(body.removed);derive();}return structuredClone(view);}
  if(url.endsWith('/story-check')){const id=body.library_id;
   if(method==='POST')view.product_checks[id]={status:'ready',sha256:'new-product',checks:[{id:'q1',question:'能这样使用吗？'}]};
   else view.product_checks[id].checks=view.product_checks[id].checks.map(q=>({...q,...body.answers.find(a=>a.id===q.id)}));
   return structuredClone(view);
  }
  if(url.endsWith('/fission')){batch.items=view.rows.map((r,i)=>({row:i+1,row_id:r.id,selections:r.selections,project_id:'child-'+i,status:'preparing',segments:[]}));view.batches=[batch];view.draft.choices={};view.draft.removed=[];derive();return {batch:structuredClone(batch)};}
  if(url==='batches/batch-a')return structuredClone(batch);
  if(url.includes('/fission-retry')){batch.items[0].status='preparing';return {status:'preparing'};}
  if(url.startsWith('library/') && method==='PATCH'){Object.assign(items.find(i=>i.id===url.split('/').at(-1)),body);return {};}
  if(url.startsWith('library/') && method==='DELETE'){items.splice(items.findIndex(i=>i.id===url.split('/').at(-1)),1);return {};}
  return JSON.parse(t.run('JSON.stringify(P)'));
 };
 t.run('api=mockApi');t.view=()=>view;t.batch=()=>batch;t.items=items;return t;
}
const click=(t,s)=>t.click(t.$(s));
async function select(t,key,ids){await click(t,`[data-fission-picker="${key}"]`);for(const id of ids)await click(t,`[data-fission-item="${id}"]`);await click(t,'[data-fission-picker-done]');}
async function entrance(){const t=fixture();await click(t,'[data-fission-open]');
 assert.equal(t.run('fissionState.rows.length'),0);assert.equal(t.$('[data-fission-create]').textContent,'先选新照片');
 assert.equal(t.$$('#fissionPanel .caption-warning').length,0);assert.equal(t.$$('[data-fission-slot]').length,3);
 assert.match(t.$('#fissionPanel').textContent,/原片里的配角/);assert.ok(t.$('[data-fission-slot="product"] img'));
 assert.equal(t.$('#step-3 h3').textContent,'这个项目的照片');assert.ok(t.$('.topbar [data-library-open]'));assert.ok(t.$('[data-library-project]'));
 t.run("P.final_preview_status={ready:false,warnings:['第 2 段还没选定版本，去第 04 步选用这一版。']};renderSteps()");assert.equal(t.$('[data-fission-open]').disabled,true);
 console.log('PASS empty panel, keep cards, title and eligibility');}
async function picker(){const t=fixture();await click(t,'[data-fission-open]');await click(t,'[data-fission-picker="C1"]');
 assert.match(t.$('.fission-picker').textContent,/选新人物，换掉“母版人物”/);assert.equal(t.$('[data-fission-item="same-person"]').disabled,true);
 assert.equal(t.$('[data-fission-item="product-a"]'),null);await click(t,'[data-fission-item="person-a"]');assert.match(t.$('.fission-picker').textContent,/已选 1 张/);
 await click(t,'[data-fission-picker-close]');assert.equal(t.run('fissionState.rows.length'),0);
 await select(t,'C1',['person-a','person-b']);assert.equal(t.run('fissionState.rows.length'),2);assert.match(t.$('#fissionPanel').textContent,/选了 2 个新人物，每人一条/);
 assert.equal(t.$('[data-fission-create]').disabled,false);await click(t,'[data-fission-delete]');assert.equal(t.run('fissionState.rows.length'),1);
 assert.equal(t.run('fissionState.removed.length'),1);await click(t,'[data-fission-remove="person-a"]');assert.deepEqual(JSON.parse(t.run('JSON.stringify(fissionState.choices.C1)')),['person-b']);
 console.log('PASS multi-select, disabled master, cancel and combinations');}
async function questions(){const t=fixture();await click(t,'[data-fission-open]');await select(t,'C1',['person-a','person-b']);await select(t,'product',['product-a']);
 assert.equal(t.$$('[data-fission-check]').length,1);assert.match(t.$('[data-fission-slot="product"]').textContent,/整理问题会调用一次推理/);
 assert.equal(t.$$('.fission-block-reason').length,1);assert.equal(t.$('[data-fission-create]').disabled,true);
 await click(t,'[data-fission-check]');assert.doesNotMatch(t.$('[data-fission-slot="product"]').textContent,/调用推理/);
 assert.deepEqual(t.$$('[data-fission-answer]').map(b=>b.textContent),['能','不能','不确定']);await click(t,'[data-fission-answer="unsure"]');
 assert.match(t.$('[data-fission-slot="product"]').textContent,/✓ 已回答/);assert.equal(t.$('[data-fission-create]').disabled,false);
 await click(t,'[data-fission-check]');assert.equal(t.requests.filter(r=>r.method==='POST'&&r.url.endsWith('/story-check')).length,1);
 await click(t,'[data-fission-create]');assert.deepEqual(JSON.parse(t.run('JSON.stringify(fissionState.choices)')),{});assert.equal(t.run('fissionState.removed.length'),0);
 assert.match(t.$('#fissionPanel').textContent,/裂变 1.*整理改法中/);t.batch().items[0].status='pending_confirmation';await t.run('fissionPoll()');assert.match(t.$('#fissionPanel').textContent,/裂变 1.*等你确认付费/);
 const submitted=t.requests.find(r=>r.method==='POST'&&r.url.endsWith('/fission'));assert.ok(submitted.body.request_id);assert.equal(submitted.body.rows,undefined);
 assert.ok(!t.requests.some(r=>r.url.endsWith('/confirm')));console.log('PASS per-product cached questions, answers, create/reset/progress and payment boundary');}
async function limitsAndDeleted(){const t=fixture();await click(t,'[data-fission-open]');await select(t,'C1',['person-a']);
 t.run('fissionState.rows=Array.from({length:21},(_,i)=>({id:String(i),selections:{C1:"person-a"}}));renderFission()');
 assert.equal(t.$('[data-fission-create]').disabled,true);assert.match(t.$('.fission-block-reason').textContent,/一次最多 20 条，现在是 21 条/);
 t.run('fissionState.rows=fissionState.rows.slice(0,1);fissionState.library.items=fissionState.library.items.filter(i=>i.id!=="person-a");renderFission()');
 assert.match(t.$('.fission-block-reason').textContent,/这张照片已从素材库删除/);assert.equal(t.$$('.fission-block-reason').length,1);
 console.log('PASS 20 limit and specific deleted-photo reason');}
async function library(){const t=fixture();await click(t,'[data-library-open]');assert.match(t.$('#libraryPanel').textContent,/这里的照片所有项目都能用/);
 assert.equal(t.$('[data-library-upload]').hasAttribute('multiple'),true);assert.match(t.$('#libraryPanel').textContent,/人物（3）/);assert.match(t.$('#libraryPanel').textContent,/用在 2 个项目/);assert.doesNotMatch(t.$('#libraryPanel').textContent,/用在 0 个项目|素材组|全局素材库/);
 await click(t,'[data-library-rename="person-a"]');let input=t.$('[data-library-name="person-a"]');input.value='改名';await t.event('keydown',input,{key:'Enter',preventDefault(){}});assert.equal(t.items[0].name,'改名');assert.equal(t.$('[data-library-upload]').value,'','rename focus must not restore text into a file input');
 await click(t,'[data-library-rename="person-a"]');input=t.$('[data-library-name="person-a"]');input.value='不保存';await t.event('keydown',input,{key:'Escape',preventDefault(){}});assert.equal(t.items[0].name,'改名');assert.equal(t.$('#libraryPanel').hidden,false);
 await click(t,'[data-library-rename="person-a"]');input=t.$('[data-library-name="person-a"]');input.value='失焦保存';await t.event('focusout',input);assert.equal(t.items[0].name,'失焦保存');
 console.log('PASS library tabs, usage count and inline Enter/Escape/blur rename');}
async function uploadAndProject(){const t=fixture();await click(t,'[data-library-open]');let enter=false,release;const gate=new Promise(r=>release=r);const uploads=[];
 t.context.mockUpload=async(url,file)=>{uploads.push(file.name);if(uploads.length===2){enter=true;await gate;}const item={id:'up'+uploads.length,kind:'person',name:file.name.replace(/\.[^.]+$/,''),url:'x',sha256:file.name};t.items.push(item);return {item,duplicate:uploads.length===2};};t.run('uploadPhoto=mockUpload');
 let input=t.$('[data-library-upload]');input.files=[{name:'one.png'},{name:'two.png'}];const uploading=t.event('change',input);await until(()=>enter);assert.match(t.$('#libraryPanel').textContent,/one/);release();await uploading;assert.deepEqual(uploads,['one.png','two.png']);assert.match(t.$('#libraryPanel').textContent,/已上传 2 张/);assert.match(t.$('#libraryPanel').textContent,/其中 1 张已在素材库/);
 await click(t,'[data-library-close]');await click(t,'[data-library-project]');await click(t,'[data-library-select="person-a"]');await click(t,'[data-library-tab="product"]');await click(t,'[data-library-select="product-a"]');await click(t,'[data-library-import]');assert.equal(t.requests.filter(r=>r.url.endsWith('/library-import')).length,2);
 t.importDuplicate=true;await click(t,'[data-library-tab="person"]');await click(t,'[data-library-select="person-b"]');await click(t,'[data-library-import]');assert.match(t.$('#libraryPanel').textContent,/这张已在项目里/);
 console.log('PASS sequential uploads, immediate display, duplicate messages and project import');}
async function draftRecovery(){const t=fixture();await click(t,'[data-fission-open]');let fail=true;t.beforeRequest=async(m,u)=>{if(fail&&m==='PUT'&&u.endsWith('/fission-draft'))throw Error('offline');};await select(t,'C1',['person-a']);assert.equal(t.run('fissionState.unsaved'),true);await click(t,'[data-fission-close]');await click(t,'[data-fission-open]');assert.equal(t.run('fissionState.choices.C1[0]'),'person-a');assert.equal(t.$('[data-fission-create]').disabled,true);fail=false;await click(t,'[data-fission-save]');assert.equal(t.$('[data-fission-create]').disabled,false);
 console.log('PASS unsaved draft survives close and retry');}
async function createRetry(){const t=fixture();await click(t,'[data-fission-open]');await select(t,'C1',['person-a']);let fail=true;t.beforeRequest=async(m,u)=>{if(fail&&m==='POST'&&u.endsWith('/fission'))throw Error('response lost');};await click(t,'[data-fission-create]');const first=t.requests.find(r=>r.method==='POST'&&r.url.endsWith('/fission')).body.request_id;assert.equal(t.$('[data-fission-create]').disabled,false,'transient create error must remain retryable');fail=false;await click(t,'[data-fission-create]');assert.equal(t.requests.filter(r=>r.method==='POST'&&r.url.endsWith('/fission')).at(-1).body.request_id,first);console.log('PASS creation error retries with same request id');}
async function duplicateUploadCount(){const t=fixture();await click(t,'[data-library-open]');t.context.mockUpload=async()=>{const {project_count,...item}=t.items[0];return {item,duplicate:true};};t.run('uploadPhoto=mockUpload');const input=t.$('[data-library-upload]');input.files=[{name:'same.png'}];await t.event('change',input);assert.match(t.$('#libraryPanel').textContent,/用在 2 个项目/);console.log('PASS duplicate upload preserves usage count');}
async function allRowsFailed(){const t=fixture(),base=t.context.mockApi;await click(t,'[data-fission-open]');await select(t,'C1',['person-a']);t.context.mockApi=async(m,u,b)=>u.endsWith('/fission')?{batch:{id:'failed',items:[{row:1,error:'复制失败',project_id:null,status:'failed'}]}}:base(m,u,b);t.run('api=mockApi');await click(t,'[data-fission-create]');assert.equal(t.run('fissionState.choices.C1[0]'),'person-a');assert.equal(t.$('[data-fission-create]').disabled,false);assert.match(t.$('#fissionPanel').textContent,/复制失败/);console.log('PASS all-row failure keeps selections for retry');}
async function serverReasons(){const t=fixture();await click(t,'[data-fission-open]');await select(t,'C1',['person-a']);t.run('fissionState.view.row_errors=[{row:1,error:"文件校验失败，请重新选择。",errors:["文件校验失败，请重新选择。"]}];renderFission()');assert.match(t.$('.fission-block-reason').textContent,/文件校验失败/);assert.doesNotMatch(t.$('.fission-block-reason').textContent,/object Object/);console.log('PASS server row errors display one concrete reason');}
async function missingProductFile(){const t=fixture();await click(t,'[data-fission-open]');await select(t,'product',['product-a']);t.run('fissionState.view.row_errors=[{row:1,errors:["“新商品”这张照片已从素材库删除，点它右上角的 × 去掉，或重新选一张。"]}];renderFission()');assert.match(t.$('.fission-block-reason').textContent,/这张照片已从素材库删除/);assert.equal(t.$$('.fission-block-reason').length,1);console.log('PASS missing product file takes priority over questions');}
async function main(){await missingProductFile();await allRowsFailed();await serverReasons();await createRetry();await duplicateUploadCount();await entrance();await picker();await questions();await limitsAndDeleted();await library();await uploadAndProject();await draftRecovery();const t=fixture();await click(t,'[data-fission-open]');assert.doesNotMatch(t.$('#fissionPanel').textContent,/免费/);await click(t,'[data-library-open]');assert.doesNotMatch(t.$('#libraryPanel').textContent,/免费/);}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={fixture,main};
