/* V9a3: real upload/request helpers and UI events; synthetic files, fetch and clock only. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {fixture:baseFixture}=require('./test_v9a_ui');
const {until}=require('./test_v5_ui');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const MB=1024*1024;
const quiet=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function response(data={},status=200){return {ok:status>=200&&status<300,status,statusText:status===200?'OK':'Request failed',json:async()=>structuredClone(data)};}
class TestFile{
 constructor(name,{size=4,read,bytes=new Uint8Array([1,2,3,4]).buffer}={}){this.name=name;this.size=size;this.type='image/png';this.reads=0;this.bytes=bytes;this.read=read;}
 arrayBuffer(){this.reads++;return this.read?this.read():Promise.resolve(this.bytes);}
}
function fakeClock(context){
 let now=0,next=0;const timers=new Map();
 context.setTimeout=(fn,ms=0,...args)=>{const id=++next;timers.set(id,{at:now+Number(ms),fn:()=>fn(...args)});return id;};
 context.clearTimeout=id=>timers.delete(id);
 return {async tick(ms){await quiet();const target=now+ms;for(;;){const due=[...timers].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);due[1].fn();await quiet();}now=target;await quiet();},pending:()=>[...timers.values()]};
}
function fixture(){
 const t=baseFixture(),mockApi=t.context.mockApi;
 Object.assign(t.context,{AbortController,Blob,File:TestFile,URL,URLSearchParams,TypeError});
 t.clock=fakeClock(t.context);t.fetches=[];
 // baseFixture intentionally mocks api. Reload only its actual declaration;
 // upload, uploadPhoto, libraryRequest and all event handlers remain untouched.
 t.run(source.slice(source.indexOf('async function api('),source.indexOf('async function workbenchFetch(')));
 t.context.fetch=async(url,options={})=>{
  assert.ok(url.startsWith('/api/'),'test fetch is local and synthetic');
  const req={url:url.slice(5),...options};t.fetches.push(req);
  if(t.onFetch){const result=await t.onFetch(req);if(result!==undefined)return result;}
  if(req.method==='POST'&&req.url.startsWith('library?')){
   const query=new URLSearchParams(req.url.split('?')[1]),filename=decodeURIComponent(req.headers['X-Filename']);
   const item={id:'upload-'+filename,kind:query.get('kind'),name:query.get('name'),url:'/synthetic/'+filename,sha256:filename};
   t.items.push(item);return response({item,duplicate:false});
  }
  if(req.method==='POST'&&/\/assets\?/.test(req.url))return response({id:'legacy-asset'});
  return response(await mockApi(req.method||'GET',req.url,req.body?JSON.parse(req.body):undefined));
 };
 return t;
}
const click=(t,selector)=>t.click(t.$(selector));
const text=(t,selector='#libraryPanel')=>t.$(selector)?.textContent||'';
const uploadRequests=t=>t.fetches.filter(r=>r.method==='POST'&&r.url.startsWith('library?'));
function startUpload(t,files,selector='[data-library-upload]'){const input=t.$(selector);assert.ok(input,'upload control exists');input.files=files;return t.event('change',input);}
async function completed(promise,message='operation settles'){
 let done=false,failure;promise.then(()=>{done=true;},error=>{done=true;failure=error;});
 await until(()=>done);if(failure)throw failure;assert.ok(done,message);
}
function assertLibraryIdle(t){assert.equal(t.run('libraryState.busy'),false);assert.equal(t.$('[data-library-close]').disabled,false);assert.equal(t.$('[data-library-tab="person"]').disabled,false);}
function assertCopies(t,selector='#libraryPanel'){assert.doesNotMatch(text(t,selector)+text(t,'#toast'),/免费/);}
function assertProgress(t,index,total,name,selector='#libraryPanel'){
 const message=`正在上传 ${index}/${total}：${name}…`,panel=t.$(selector),label=panel.querySelector('.fission-upload');
 assert.ok(label?.textContent.includes(message),'upload tile displays '+message);
 assert.ok(panel.querySelectorAll('[role="status"]').some(el=>el.textContent.includes(message)),'status region displays '+message);
 assertCopies(t,selector);
}
async function oversized(){
 const t=fixture();await click(t,'[data-library-open]');const file=new TestFile('超大照片.png',{size:30*MB+1});
 await completed(startUpload(t,[file]));
 assert.equal(uploadRequests(t).length,0,'photos larger than 30 MB must never reach fetch');
 assert.equal(file.reads,0);assert.ok(text(t).includes('“超大照片.png”超过 30MB，换一张小一点的照片。'));assertLibraryIdle(t);
}
async function readFailure(mode){
 const t=fixture();await click(t,'[data-library-open]');const gate=deferred();
 const file=new TestFile('云端照片.png',{read:mode==='timeout'?()=>gate.promise:()=>Promise.reject(new TypeError('unreadable file'))});
 const pending=startUpload(t,[file]);await quiet();
 if(mode==='timeout'){
  assertProgress(t,1,1,file.name);await t.clock.tick(19999);assert.equal(t.run('libraryState.busy'),true,'read remains active before 20 seconds');
  assert.equal(uploadRequests(t).length,0);await t.clock.tick(1);
 }
 await completed(pending);assertLibraryIdle(t);assert.equal(file.reads,1);assert.equal(uploadRequests(t).length,0);
 assert.ok(text(t).includes('“云端照片.png”读不出来，没有上传。如果照片在 iCloud 或“照片”App 里，先把它存到本机（比如桌面）再选。'));
 assert.doesNotMatch(text(t),/连不上工作台/);assertCopies(t);
 if(mode==='timeout'){gate.resolve(file.bytes);await quiet();assert.equal(uploadRequests(t).length,0,'late local read cannot start an upload');}
}
async function uploadTimeout(bodyStalls){
 const t=fixture();await click(t,'[data-library-open]');const gate=deferred();let request;
 t.onFetch=req=>{if(req.method==='POST'&&req.url.startsWith('library?')){request=req;return bodyStalls?{...response(),json:()=>gate.promise}:gate.promise;}};
 const file=new TestFile('慢照片.png'),pending=startUpload(t,[file]);await until(()=>request);
 assert.equal(request.body,file.bytes,'upload sends bytes already read into memory');assert.ok(request.signal,'upload has an AbortController signal');
 assertProgress(t,1,1,file.name);await t.clock.tick(59999);assert.equal(request.signal.aborted,false);assert.equal(t.run('libraryState.busy'),true);
 await t.clock.tick(1);await completed(pending);assert.equal(request.signal.aborted,true,'the 60 second deadline aborts fetch');assertLibraryIdle(t);
 assert.ok(text(t).includes('“慢照片.png”上传超过 1 分钟还没完成，已停止。请重试，或换一张照片。'));assertCopies(t);
 const late={item:{id:'too-late',kind:'person',name:'迟到照片',sha256:'late'},duplicate:false};gate.resolve(bodyStalls?late:response(late));await quiet();
 assert.equal(t.$('[data-library-photo="too-late"]'),null,'a late response after timeout is ignored');
}
async function uploadTimeoutButStored(){
 // Review fix: the server may store the photo just before the 60 s deadline; find it by sha256 instead of reporting failure.
 const t=fixture();t.context.crypto=require('node:crypto').webcrypto;await click(t,'[data-library-open]');let request;
 const sha=require('node:crypto').createHash('sha256').update(Buffer.from([1,2,3,4])).digest('hex');
 t.onFetch=req=>{
  if(req.method==='POST'&&req.url.startsWith('library?')){request=req;return new Promise(()=>{});}
  if((req.method||'GET')==='GET'&&req.url==='library'&&request)return response({items:[...t.items,{id:'arrived',kind:'person',name:'素材 9',url:'/synthetic/arrived',sha256:sha}]});
 };
 const pending=startUpload(t,[new TestFile('慢但到了.png')]);await until(()=>request);await t.clock.tick(60000);await completed(pending);
 assert.ok(t.$('[data-library-photo="arrived"]'),'photo stored before the deadline is shown');
 assert.ok(!text(t).includes('上传超过 1 分钟'),'no false failure');assertLibraryIdle(t);
}
async function readThenUploadBudgets(){
 const t=fixture();await click(t,'[data-library-open]');const reading=deferred(),network=deferred();let req;
 t.onFetch=r=>{if(r.method==='POST'&&r.url.startsWith('library?')){req=r;return network.promise;}};
 const file=new TestFile('先读后传.png',{size:30*MB,read:()=>reading.promise}),pending=startUpload(t,[file]);
 await quiet();await t.clock.tick(19000);assert.equal(uploadRequests(t).length,0);
 reading.resolve(file.bytes);await until(()=>req);assert.equal(req.body,file.bytes);await t.clock.tick(1000);
 assert.equal(req.signal.aborted,false,'finished file-read deadline does not abort upload');await t.clock.tick(58999);assert.equal(req.signal.aborted,false);
 await t.clock.tick(1);await completed(pending);assert.equal(req.signal.aborted,true);assertLibraryIdle(t);
}
async function mixedBatch(){
 const t=fixture();await click(t,'[data-library-open]');const thirdGate=deferred();
 const files=[new TestFile('第一张.png'),new TestFile('第二张.png'),new TestFile('第三张.png',{read:()=>thirdGate.promise}),new TestFile('第四张.png',{read:()=>Promise.reject(Error('local read failed'))}),new TestFile('第五张.png',{size:31*MB})];
 t.onFetch=req=>{
  if(req.method!=='POST'||!req.url.startsWith('library?'))return;
  const name=decodeURIComponent(req.headers['X-Filename']);
  if(name===files[1].name)return response({error:'第二张服务拒绝'},422);
  if(name===files[2].name){const {project_count,...item}=t.items[0];return response({item,duplicate:true});}
 };
 const pending=startUpload(t,files);await until(()=>files[2].reads===1);
 assert.ok(t.$('[data-library-photo="upload-第一张.png"]'),'first success is visible before the batch finishes');
 assertProgress(t,3,5,files[2].name);thirdGate.resolve(files[2].bytes);await completed(pending);
 assert.deepEqual(uploadRequests(t).map(r=>decodeURIComponent(r.headers['X-Filename'])),files.slice(0,3).map(f=>f.name));
 assert.match(text(t),/已上传 2 张/);assert.match(text(t),/其中 1 张已在素材库/);assert.match(text(t),/第二张.png.*第二张服务拒绝/s);
 assert.ok(text(t).includes('“第四张.png”读不出来，没有上传。'));assert.ok(text(t).includes('“第五张.png”超过 30MB'));
 assert.match(text(t),/用在 2 个项目/);assertLibraryIdle(t);assertCopies(t);
}
async function successfulBatch(){
 const t=fixture();await click(t,'[data-library-open]');await completed(startUpload(t,[new TestFile('one.png'),new TestFile('two.png')]));
 assert.match(text(t),/已上传 2 张/);assert.doesNotMatch(text(t),/没有上传|其中/);assert.equal(t.$$('[data-library-photo]').length,5);assertLibraryIdle(t);
}
async function libraryCloseAndReopen(){
 const t=fixture();await click(t,'[data-library-open]');const gate=deferred(),file=new TestFile('后台照片.png',{read:()=>gate.promise});
 const pending=startUpload(t,[file]);await quiet();assertProgress(t,1,1,file.name);
 assert.equal(t.$('[data-library-close]').disabled,false,'close stays usable while reading');
 for(const selector of ['[data-library-tab="product"]','[data-library-rename="person-a"]','[data-library-delete="person-a"]','[data-library-upload]'])assert.equal(t.$(selector).disabled,true,selector+' is locked');
 await click(t,'[data-library-tab="product"]');assert.equal(t.$('[data-library-tab="person"]').getAttribute('aria-pressed'),'true');
 await click(t,'[data-library-close]');assert.equal(t.$('#libraryPanel').hidden,true);
 const gets=t.fetches.filter(r=>r.url==='library'&&r.method==='GET').length;
 await click(t,'[data-library-open]');assertProgress(t,1,1,file.name);assert.equal(t.fetches.filter(r=>r.url==='library'&&r.method==='GET').length,gets,'reopen preserves the in-flight batch');
 await t.event('keydown',t.$('#libraryPanel'),{key:'Escape'});assert.equal(t.$('#libraryPanel').hidden,true,'Escape closes an upload in progress');
 gate.resolve(file.bytes);await completed(pending);assert.match(text(t,'#toast'),/素材库：已上传 1 张/);
 await click(t,'[data-library-open]');assert.equal(t.fetches.filter(r=>r.url==='library'&&r.method==='GET').length,gets+1,'completed upload reopens with a fresh list');
 assert.ok(t.$('[data-library-photo="upload-后台照片.png"]'));assertLibraryIdle(t);assertCopies(t);
}
async function backgroundFailure(){
 const t=fixture();await click(t,'[data-library-open]');const pending=startUpload(t,[new TestFile('后台读取失败.png',{read:()=>new Promise(()=>{})})]);
 await quiet();await click(t,'[data-library-close]');await t.clock.tick(20000);await completed(pending);
 assert.match(text(t,'#toast'),/素材库：.*1 张没有上传/s);assert.match(text(t,'#toast'),/后台读取失败.png.*读不出来/s);assert.doesNotMatch(text(t,'#toast'),/连不上工作台/);
 await click(t,'[data-library-open]');assertLibraryIdle(t);assertCopies(t);
}
async function beginLibraryAction(t,action){
 if(action==='open')return {start:()=>click(t,'[data-library-open]'),matches:r=>r.method==='GET'&&r.url==='library'};
 await click(t,action==='import'?'[data-library-project]':'[data-library-open]');
 if(action==='rename'){
  await click(t,'#libraryPanel [data-library-rename="person-a"]');const input=t.$('#libraryPanel [data-library-name="person-a"]');input.value='新名字';
  return {start:()=>t.event('keydown',input,{key:'Enter'}),matches:r=>r.method==='PATCH'&&r.url==='library/person-a'};
 }
 if(action==='delete')return {start:async()=>{const pending=click(t,'[data-library-delete="person-a"]');await until(()=>t.$('#pageConfirmation').hidden===false);await click(t,'[data-page-confirm="accept"]');return pending;},matches:r=>r.method==='DELETE'&&r.url==='library/person-a'};
 await click(t,'[data-library-select="person-a"]');return {start:()=>click(t,'[data-library-import]'),matches:r=>r.method==='POST'&&r.url.endsWith('/library-import')};
}
async function libraryRequestFailure(action,mode){
 const t=fixture(),operation=await beginLibraryAction(t,action),gate=deferred();let request;
 t.onFetch=req=>{if(!operation.matches(req))return;request=req;
  if(mode==='error')throw Error('synthetic request failure');
  return mode==='body'?{...response(),json:()=>gate.promise}:gate.promise;
 };
 const pending=operation.start();await until(()=>request);
 if(mode!=='error'){
  assert.ok(request.signal,'library request carries an abort signal');await t.clock.tick(29999);assert.equal(t.run('libraryState.busy'),true);
  await t.clock.tick(1);
 }
 await completed(pending);assertLibraryIdle(t);
 if(mode==='error')assert.match(text(t),/synthetic request failure/);
 else{assert.equal(request.signal.aborted,true);assert.ok(text(t).includes('素材库没有响应，请关掉再打开；照片不受影响。'));}
 assertCopies(t);t.onFetch=null;await click(t,'[data-library-close]');await click(t,'[data-library-open]');assertLibraryIdle(t);
}
async function mutationProjectRefreshFailure(action,timeout){
 const t=fixture(),operation=await beginLibraryAction(t,action);let refresh;
 t.onFetch=req=>{if(req.method==='GET'&&/^projects\/[^/]+$/.test(req.url)){refresh=req;if(timeout)return new Promise(()=>{});throw Error('marker refresh failed');}};
 const pending=operation.start();await until(()=>refresh);if(timeout)await t.clock.tick(30000);await completed(pending);
 assertLibraryIdle(t);assert.doesNotMatch(text(t),/操作没有完成|marker refresh failed/);
 if(action==='delete')assert.equal(t.$('[data-library-photo="person-a"]'),null,'successful deletion remains visible despite project refresh failure');
 else assert.match(t.$('[data-library-photo="person-a"]').textContent,/新名字/);
}
async function libraryRenderFailure(action){
 const t=fixture();let operation;
 if(action==='upload'){await click(t,'[data-library-open]');operation={start:()=>startUpload(t,[new TestFile('render.png')])};}
 else operation=await beginLibraryAction(t,action);
 const before=t.fetches.length;
 // Inject a single DOM-render failure, preserving the actual final render and
 // every request/upload helper. The first exception must still reach finally.
 t.run('const renderLibraryBeforeFault=renderLibrary;let renderLibraryFault=true;renderLibrary=(...args)=>{if(renderLibraryFault){renderLibraryFault=false;throw Error("synthetic render failure");}return renderLibraryBeforeFault(...args);}');
 await completed(operation.start());assertLibraryIdle(t);assert.match(text(t),/synthetic render failure/);
 assert.equal(t.fetches.length,before,'render failure does not begin a new request');assertCopies(t);
}
async function openPicker(t,key='C1'){await click(t,'[data-fission-open]');await click(t,`[data-fission-picker="${key}"]`);}
async function fissionBatch(){
 const t=fixture();await openPicker(t);const gate=deferred(),files=[new TestFile('裂变一.png'),new TestFile('裂变二.png',{read:()=>Promise.reject(Error('local'))}),new TestFile('裂变三.png',{read:()=>gate.promise})];
 const pending=startUpload(t,files,'[data-fission-upload]');await until(()=>files[2].reads===1);
 assertProgress(t,3,3,files[2].name,'.fission-picker');assert.equal(t.$('[data-fission-item="upload-裂变一.png"]').getAttribute('aria-pressed'),'true','open original picker selects successful uploads');
 assert.equal(t.$('[data-fission-close]').disabled,false);assert.equal(t.$('[data-fission-picker-close]').disabled,false);
 for(const selector of ['[data-fission-picker="C2"]','[data-fission-picker-done]','[data-fission-upload]','[data-fission-item="person-a"]'])assert.equal(t.$(selector).disabled,true,selector+' stays locked');
 gate.resolve(files[2].bytes);await completed(pending);assert.equal(t.$('[data-fission-picker-done]').disabled,false);
 assert.equal(t.$('[data-fission-item="upload-裂变三.png"]').getAttribute('aria-pressed'),'true');
 assert.match(text(t,'.fission-picker'),/已上传 2 张/);assert.match(text(t,'#fissionPanel'),/裂变二.png.*读不出来/s);assertCopies(t,'#fissionPanel');
}
async function fissionReadAndUploadTimeout(uploading){
 const t=fixture();await openPicker(t);let req;const gate=deferred();
 if(uploading)t.onFetch=r=>{if(r.method==='POST'&&r.url.startsWith('library?')){req=r;return gate.promise;}};
 const file=new TestFile('裂变超时.png',uploading?{}:{read:()=>gate.promise});
 const pending=startUpload(t,[file],'[data-fission-upload]');await quiet();if(uploading)await until(()=>req);
 assertProgress(t,1,1,file.name,'.fission-picker');await t.clock.tick(uploading?60000:20000);await completed(pending);
 assert.equal(t.$('[data-fission-picker-done]').disabled,false);assert.equal(t.$('[data-fission-upload]').disabled,false);
 assert.match(text(t,'#fissionPanel'),uploading?/上传超过 1 分钟/:/读不出来，没有上传/);
 if(uploading)assert.equal(req.signal.aborted,true);else assert.equal(uploadRequests(t).length,0);
 assert.doesNotMatch(text(t,'#fissionPanel'),/连不上工作台/);assertCopies(t,'#fissionPanel');
}
async function fissionCloseAndReopen(escape){
 const t=fixture();await openPicker(t);const gate=deferred(),file=new TestFile('关闭后完成.png',{read:()=>gate.promise});
 const pending=startUpload(t,[file],'[data-fission-upload]');await quiet();
 if(escape)await t.event('keydown',t.$('.fission-picker'),{key:'Escape'});else await click(t,'[data-fission-picker-close]');
 assert.equal(t.$('.fission-picker'),null,'cancel or Escape closes the uploading picker');assert.equal(t.$('#fissionPanel').hidden,false);
 if(escape)await t.event('keydown',t.$('#fissionPanel'),{key:'Escape'});else await click(t,'[data-fission-close]');
 assert.equal(t.$('#fissionPanel').hidden,true,'whole fission panel can close while uploading');
 await click(t,'[data-fission-open]');assert.equal(t.$('[data-fission-close]').disabled,false);assert.equal(t.$('[data-fission-picker="C2"]').disabled,true,'reopened active batch stays locked');
 await click(t,'[data-fission-close]');gate.resolve(file.bytes);await completed(pending);
 assert.match(text(t,'#toast'),/素材库：已上传 1 张/);await openPicker(t,'C2');
 const item=t.$('[data-fission-item="upload-关闭后完成.png"]');assert.ok(item,'background upload is available in the library');assert.equal(item.getAttribute('aria-pressed'),'false','closing original picker prevents automatic selection in another position');
 assert.match(text(t,'.fission-picker'),/已选 0 张/);assertCopies(t,'#fissionPanel');
}
async function fissionSwitchProject(){
 const t=fixture();await openPicker(t);const gate=deferred(),files=[new TestFile('旧项目上传一.png',{read:()=>gate.promise}),new TestFile('旧项目上传二.png')];
 const pending=startUpload(t,files,'[data-fission-upload]');await quiet();await click(t,'[data-fission-close]');
 t.run('P={...clone(P),id:"other-project",name:"另一个项目"};renderSteps()');await openPicker(t,'C2');
 gate.resolve(files[0].bytes);await completed(pending);
 assert.equal(t.run('P.id'),'other-project');assert.deepEqual(uploadRequests(t).map(r=>decodeURIComponent(r.headers['X-Filename'])),files.map(f=>f.name),'project switch must not abandon remaining photos');
 assert.match(text(t,'.fission-picker'),/原片里的配角/);assert.match(text(t,'.fission-picker'),/已选 0 张/);
 for(const item of t.$$('.fission-picker [data-fission-item]'))assert.equal(item.getAttribute('aria-pressed'),'false','old upload cannot select in the new project');
 assert.ok(!t.fetches.some(r=>r.method==='PUT'&&r.url.includes('other-project')),'old upload cannot save the new project');
 assert.match(text(t,'#toast'),/素材库：已上传 2 张/);await click(t,'[data-fission-picker-close]');await click(t,'[data-fission-close]');await openPicker(t,'C2');
 for(const file of files)assert.ok(t.$(`[data-fission-item="upload-${file.name}"]`),'all background photos reach library');assertCopies(t,'#fissionPanel');
}
function holdLibrarySnapshot(t){
 const gate=deferred();let snapshot;
 t.onFetch=req=>{
  if(req.method==='GET'&&req.url==='library'&&!snapshot){snapshot={items:structuredClone(t.items)};return gate.promise;}
 };
 return {started:()=>!!snapshot,release:()=>gate.resolve(response(snapshot))};
}
async function libraryReadPreservesFissionUpload(mutation){
 const t=fixture();await openPicker(t);const fileRead=deferred(),file=new TestFile('列表读取期间上传.png',{read:()=>fileRead.promise});
 const uploading=startUpload(t,[file],'[data-fission-upload]');await quiet();await click(t,'[data-fission-close]');
 const operation=mutation?await beginLibraryAction(t,'rename'):{start:()=>click(t,'[data-library-open]')};
 const oldList=holdLibrarySnapshot(t),reading=operation.start();await until(oldList.started);
 fileRead.resolve(file.bytes);await completed(uploading);
 assert.ok(t.$('[data-library-photo="upload-列表读取期间上传.png"]'),'completed background photo appears while the older list is pending');
 oldList.release();await completed(reading);assertLibraryIdle(t);
 assert.ok(t.$('[data-library-photo="upload-列表读取期间上传.png"]'),(mutation?'post-rename':'opening')+' old list must preserve a concurrent completed upload');
 if(mutation)assert.match(t.$('#libraryPanel [data-library-photo="person-a"]').textContent,/新名字/,'renaming result is retained alongside the concurrent upload');
 assertCopies(t);
}
async function fissionReadPreservesLibraryUpload(){
 const t=fixture();await click(t,'[data-library-open]');const fileRead=deferred(),file=new TestFile('反向列表竞态.png',{read:()=>fileRead.promise});
 const uploading=startUpload(t,[file]);await quiet();await click(t,'[data-library-close]');
 const oldList=holdLibrarySnapshot(t),opening=click(t,'[data-fission-open]');await until(oldList.started);
 fileRead.resolve(file.bytes);await completed(uploading);oldList.release();await completed(opening);
 await click(t,'[data-fission-picker="C1"]');const photo=t.$('[data-fission-item="upload-反向列表竞态.png"]');
 assert.ok(photo,'fission opening list must preserve the completed library upload');
 assert.equal(photo.getAttribute('aria-pressed'),'false','a concurrent library upload is available without selecting a fission position');
 assert.match(text(t,'.fission-picker'),/已选 0 张/);assertCopies(t,'#fissionPanel');
}
async function fissionDraftWaitPreservesLibraryUpload(){
 const t=fixture();await click(t,'[data-library-open]');const fileRead=deferred(),draftRead=deferred();let draftStarted=false;
 const file=new TestFile('等待草稿期间上传.png',{read:()=>fileRead.promise});
 const uploading=startUpload(t,[file]);await quiet();await click(t,'[data-library-close]');
 t.onFetch=req=>{if(req.method==='GET'&&req.url.endsWith('/fission-draft')){draftStarted=true;return draftRead.promise;}};
 const opening=click(t,'[data-fission-open]');await until(()=>draftStarted);await quiet();
 // GET/library is immediate; only GET/fission-draft is pending here.
 fileRead.resolve(file.bytes);await completed(uploading);draftRead.resolve(response(t.view()));await completed(opening);
 await click(t,'[data-fission-picker="C1"]');const photo=t.$('[data-fission-item="upload-等待草稿期间上传.png"]');
 assert.ok(photo,'finishing the delayed draft must preserve uploads completed after the library read');
 assert.equal(photo.getAttribute('aria-pressed'),'false','upload completed outside the picker is not selected automatically');
 assert.match(text(t,'.fission-picker'),/已选 0 张/);assertCopies(t,'#fissionPanel');
}
async function legacyAssetUpload(){
 const t=fixture();let handler;
 t.context.captureAssetHandler=fn=>{handler=fn;};
 t.run('function bindDrop(label,input,handler){captureAssetHandler(handler);} refresh=async()=>{}');
 const start=source.indexOf('bindDrop($("#assetInput").closest("label")');
 assert.ok(start>=0,'existing assetInput handler remains present');
 t.run(source.slice(start,source.indexOf('bindDrop($("#audioInput")',start)));
 const file=new TestFile('商品原照片.png',{size:31*MB,read:()=>{throw Error('legacy asset upload must not pre-read');}});
 await completed(handler([file]));const req=t.fetches.find(r=>r.method==='POST'&&r.url.includes('/assets?'));
 assert.ok(req);assert.equal(req.body,file,'step 03 keeps the original File request body');assert.equal(file.reads,0);
 assert.equal(req.signal,undefined,'step 03 does not inherit new photo deadlines');assert.match(req.url,/roles=product_truth/);
 assert.equal(decodeURIComponent(req.headers['X-Filename']),file.name);assert.match(text(t,'#toast'),/素材已上传，记得补充说明并确认类型/);
}
const cases=[
 ['30 MB limit without file read or request',oversized],
 ['read failure is local and unlocks the panel',()=>readFailure('error')],
 ['20 second read deadline and late result',()=>readFailure('timeout')],
 ['60 second upload deadline aborts fetch',()=>uploadTimeout(false)],
 ['60 second upload deadline includes response body',()=>uploadTimeout(true)],
 ['upload timeout finds a photo the server already stored',uploadTimeoutButStored],
 ['read and upload get separate deadlines; exactly 30 MB allowed',readThenUploadBudgets],
 ['sequential mixed batch preserves successes and reports every failure',mixedBatch],
 ['successful batch summary',successfulBatch],
 ['progress, locked controls, close, Escape, reopen and background toast',libraryCloseAndReopen],
 ['background failure toast',backgroundFailure],
 ...['open','rename','delete','import'].flatMap(action=>[['30 second '+action+' timeout',()=>libraryRequestFailure(action,'timeout')],[action+' exception resets busy',()=>libraryRequestFailure(action,'error')]]),
 ['30 second library request deadline includes response body',()=>libraryRequestFailure('open','body')],
 ...['delete','rename'].flatMap(action=>[[action+' survives failed project marker refresh',()=>mutationProjectRefreshFailure(action,false)],[action+' survives project marker refresh timeout',()=>mutationProjectRefreshFailure(action,true)]]),
 ...['open','rename','delete','import','upload'].map(action=>[action+' render exception resets busy',()=>libraryRenderFailure(action)]),
 ['fission progress, partial success and original picker selection',fissionBatch],
 ['fission file-read deadline unlocks controls',()=>fissionReadAndUploadTimeout(false)],
 ['fission upload deadline unlocks controls',()=>fissionReadAndUploadTimeout(true)],
 ['fission cancel, close, reopen and another position',()=>fissionCloseAndReopen(false)],
 ['fission Escape closes picker and panel while uploading',()=>fissionCloseAndReopen(true)],
 ['fission project switch preserves all uploads without wrong selection',fissionSwitchProject],
 ['old library opening list preserves background fission upload',()=>libraryReadPreservesFissionUpload(false)],
 ['old fission opening list preserves background library upload without selecting it',fissionReadPreservesLibraryUpload],
 ['delayed fission draft preserves upload completed after library read',fissionDraftWaitPreservesLibraryUpload],
 ['old post-mutation list preserves background fission upload',()=>libraryReadPreservesFissionUpload(true)],
 ['step 03 assetInput keeps raw upload behavior',legacyAssetUpload],
];
async function main(){let failed=0;for(const [name,test]of cases){try{await test();console.log('PASS V9a3 '+name);}catch(error){failed++;console.error('FAIL V9a3 '+name+'\n'+error.stack);}}assert.equal(failed,0,failed+' V9a3 UI scenarios failed');}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1;});
module.exports={fixture,main};
