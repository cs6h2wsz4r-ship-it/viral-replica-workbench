/* V9a5 material names: synthetic upload bytes and API, real UI handlers. */
const assert=require('node:assert/strict');
const {fixture:baseFixture}=require('./test_v9a3_ui');
function fixture(){
 const t=baseFixture();let number=0;
 t.onFetch=req=>{
  if(req.method==='POST' && req.url.startsWith('library?')){
   assert.equal(new URLSearchParams(req.url.split('?')[1]).has('name'),false,'filename is not sent as name');
   const item={id:'new-'+(++number),kind:new URLSearchParams(req.url.split('?')[1]).get('kind'),name:'素材 '+number,url:'/api/library/new-'+number+'/file',sha256:'new-'+number};
   t.items.push(item);return {ok:true,status:200,json:async()=>({item,duplicate:false})};
  }
 };
 return t;
}
const click=(t,s)=>t.click(t.$(s));
const file=name=>({name,size:4,arrayBuffer:async()=>new Uint8Array([1,2,3,4]).buffer});
async function upload(t,selector,n=1){const input=t.$(selector);input.files=Array.from({length:n},(_,i)=>file('用户文件名'+i+'.png'));await t.event('change',input);}
async function rename(t,id,name,key='Enter',scope='#libraryPanel'){
 await click(t,`${scope} [data-library-rename="${id}"]`);
 const input=t.$(`${scope} [data-library-name="${id}"]`);assert.ok(input,'inline input');input.value=name;
 await t.event(key==='blur'?'focusout':'keydown',input,{key,preventDefault(){}});
}
async function autoLastAndEscape(){const t=fixture();await click(t,'[data-library-open]');await upload(t,'[data-library-upload]',3);
 assert.deepEqual(t.$$('#libraryPanel [data-library-name]').map(i=>i.dataset.libraryName),['new-3']);
 const input=t.$('[data-library-name="new-3"]');assert.equal(input.value,'素材 3');assert.equal(t.document.activeElement,input);
 input.value='不要保存';await t.event('keydown',input,{key:'Escape',preventDefault(){}});
 assert.equal(t.items.find(i=>i.id==='new-3').name,'素材 3');assert.equal(t.$('#libraryPanel').hidden,false);assert.equal(t.fetches.filter(r=>r.method==='PATCH').length,0);
}
async function saveEnterBlur(){const t=fixture();await click(t,'[data-library-open]');await upload(t,'[data-library-upload]');
 let input=t.$('[data-library-name="new-1"]');assert.ok(input);input.value='我的素材';await t.event('keydown',input,{key:'Enter',preventDefault(){}});
 assert.equal(t.items.find(i=>i.id==='new-1').name,'我的素材');await rename(t,'new-1','失焦名字','blur');assert.equal(t.items.find(i=>i.id==='new-1').name,'失焦名字');
}
async function pickerUploadAndRename(){const t=fixture();await click(t,'[data-fission-open]');await click(t,'[data-fission-picker="C1"]');await upload(t,'[data-fission-upload]',2);
 assert.equal(t.$('.fission-picker [data-library-name]'),null);const selected=t.run('JSON.stringify(fissionState.picker.selected)');
 await rename(t,'new-2','弹层改名','Enter','.fission-picker');assert.equal(t.items.find(i=>i.id==='new-2').name,'弹层改名');assert.equal(t.run('JSON.stringify(fissionState.picker.selected)'),selected);
 await rename(t,'person-a','取消改名','Escape','.fission-picker');assert.equal(t.$('.fission-picker').hidden,false);assert.equal(t.items[0].name,'新人物');
}
async function projectRename(){const t=fixture();await click(t,'[data-library-project]');await click(t,'[data-library-select="person-a"]');
 await rename(t,'person-a','导入时改名');assert.equal(t.items[0].name,'导入时改名');assert.deepEqual(JSON.parse(t.run('JSON.stringify(libraryState.selected)')),['person-a']);assert.equal(t.$('[data-library-import]').disabled,false);
}
async function renameSyncAndFailure(){const t=fixture();await click(t,'[data-fission-open]');await click(t,'[data-library-open]');await rename(t,'person-a','两处同步');await click(t,'[data-library-close]');await click(t,'[data-fission-picker="C1"]');
 assert.match(t.$('.fission-picker').textContent,/两处同步/);t.beforeRequest=(m,u)=>{if(m==='PATCH')throw Error('offline rename failure');};
 await rename(t,'person-a','失败值','Enter','.fission-picker');assert.ok(t.$('.fission-picker [data-library-name]'));assert.match(t.$('.fission-picker').textContent,/offline rename failure/);assert.equal(t.run('fissionState.busy'),null);
}
async function createdOneButton(){const t=fixture();await click(t,'[data-fission-open]');t.run('fissionState.showProgress=true;fissionState.batches=[{id:"b",items:[{project_id:"child",status:"preparing",row:1}]}];renderFission()');assert.equal(t.$$('[data-fission-new-batch]').length,1);assert.ok(t.$('.fission-foot [data-fission-new-batch]'));}
async function closedUploadNoAutofocus(){const t=fixture();await click(t,'[data-library-open]');let release;t.onFetch=req=>req.url.startsWith('library?')?new Promise(r=>release=()=>r({ok:true,status:200,json:async()=>({item:{id:'late',kind:'person',name:'素材 1'},duplicate:false})})):undefined;
 const pending=upload(t,'[data-library-upload]');for(let i=0;i<10&&!release;i++)await new Promise(r=>setImmediate(r));await click(t,'[data-library-close]');release();await pending;assert.equal(t.$('#libraryPanel').hidden,true);assert.equal(t.run('libraryState.editing'),null);
}
async function longNames(){const t=fixture(),name='长素材名字'.repeat(20);t.view().columns[0].master_name=name;t.items.find(i=>i.id==='product-a').name=name;await click(t,'[data-fission-open]');
 assert.ok(t.$$('.material-name-inline').some(el=>el.title===name || el.getAttribute('title')===name));
 await click(t,'[data-fission-picker="C1"]');assert.match(t.$('.fission-picker h3').getAttribute('title')||'',new RegExp(name));
 await click(t,'[data-fission-picker-close]');await click(t,'[data-fission-picker="product"]');await click(t,'[data-fission-item="product-a"]');await click(t,'[data-fission-picker-done]');
 assert.equal(t.$('.fission-check .material-name-inline').getAttribute('title'),name);
}
async function renameBeatsOlderPickerRead(){
 const t=fixture();await click(t,'[data-library-open]');await click(t,'#libraryPanel [data-library-rename="person-a"]');
 const input=t.$('#libraryPanel [data-library-name="person-a"]');input.value='改后的名字';
 let releasePatch,releaseRead;t.beforeRequest=(m)=>m==='PATCH'?new Promise(r=>releasePatch=r):undefined;
 const renaming=t.event('keydown',input,{key:'Enter',preventDefault(){}});
 for(let i=0;i<20&&!releasePatch;i++)await new Promise(r=>setImmediate(r));assert.ok(releasePatch);
 await click(t,'[data-library-close]');
 t.onFetch=req=>{if(req.url==='library'&&req.method==='GET'&&!releaseRead){const old={items:structuredClone(t.items)};return new Promise(r=>releaseRead=()=>r({ok:true,status:200,json:async()=>old}));}};
 const opening=t.run('openFission()');for(let i=0;i<20&&!releaseRead;i++)await new Promise(r=>setImmediate(r));assert.ok(releaseRead);
 releasePatch();await renaming;releaseRead();await opening;
 assert.equal(t.items.find(i=>i.id==='person-a').name,'改后的名字');
 assert.equal(t.run('fissionState.library.items.find(i=>i.id==="person-a").name'),'改后的名字');
}
const cases=[autoLastAndEscape,saveEnterBlur,pickerUploadAndRename,projectRename,renameSyncAndFailure,createdOneButton,closedUploadNoAutofocus,longNames,renameBeatsOlderPickerRead];
async function main(){let failed=0;for(const test of cases){try{await test();console.log('PASS V9a5 library '+test.name);}catch(e){failed++;console.error('FAIL '+test.name+'\n'+e.stack);}}assert.equal(failed,0);}
if(require.main===module)main().catch(e=>{console.error(e.message);process.exitCode=1;});module.exports={fixture,main};
