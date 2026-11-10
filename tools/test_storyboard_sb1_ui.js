/* SB1 actual renderer and delegated events, synthetic project and mocked HTTP. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node,installPageConfirmationHarness}=require('./ui_test_dom');
for(const prop of ['hidden','disabled'])Object.defineProperty(Node.prototype,prop,{get(){return this.hasAttribute(prop);},set(v){v?this.setAttribute(prop,''):this.removeAttribute(prop);},configurable:true});
Node.prototype.scrollIntoView=function(){};
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
function setup({autoConfirm=true}={}){
 const document=createDocument(),events={},requests=[];
 document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
 document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
 const cells=Array.from({length:10},(_,i)=>({id:`c${String(i+1).padStart(2,'0')}`,index:i+1,start:i,end:i+1,frame_at:i+.5,file:`analysis/storyboard/cell-${String(i+1).padStart(2,'0')}.jpg`,characters:['C1'],product:'手环',action:'按下按键',dialogue:'帮帮我',split_reason:i===0?'镜头中途商品出现':'',shot_index:i<2?1:i+1,part:i<2?i+1:null,manual:false}));
 const project={id:'sb1-offline',name:'SB1',source:{file:'source.mp4',probe:{width:90,height:160}},assets:[{id:'dad',type:'image',roles:['person_identity'],file:'dad.jpg',label:'父亲照'},{id:'watch',type:'image',roles:['product_truth'],file:'watch.jpg',label:'商品照'}],analysis:{},breakdown:{characters:[{id:'C1',name:'父亲',appearance:'白发',outfit:'灰毛衣',role:'老人'}],productMoments:[{product:'SOS 手环'},{product:'SOS 手环'},{product:'备用手机'}]},replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'dad',description:''}],product:{action:'replace',asset_id:'watch',description:'黑色手表'}},storyboard:{version:1,cells,sheets:[{id:'s01',file:'analysis/storyboard/sheet-01.jpg',cells:cells.slice(0,9).map(c=>c.id)},{id:'s02',file:'analysis/storyboard/sheet-02.jpg',cells:['c10']}],merges:[]},storyboard_status:{stale:false},images:{},runs:{},jobs:{},script:{tasks:[]},scene_cast:{S1:{}},camera_notice:{'scene:S1':'不应出现的通知'}};
 const context=vm.createContext({document,console,project,structuredClone,confirm:()=>true,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
 const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
 run(source.slice(0,source.indexOf('// ---------- settings ----------')));
 const start=source.indexOf('document.addEventListener("click", async (e) => {');run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
 let failure='',hold=null;
 context.mockApi=async(method,url,body)=>{requests.push({method,url,body:structuredClone(body)});if(hold)await hold;if(failure)throw Error(failure);
  if(url.endsWith('/replacements')){project.replacements=structuredClone(body);return structuredClone(project);}
  if(url.endsWith('/storyboard')){if(body.action==='merge'){project.storyboard.cells[0].manual=true;project.storyboard.merges.push(['c01','c02']);}if(body.action==='restore')project.storyboard.merges=[];return structuredClone(project);}
  if(url.endsWith('/cast/identify')){const desc='男，70岁；白色短发',entry={...body.entry,look:{hair:'白色短发'}};const suggested=!!entry.description;if(!suggested)entry.description=desc;if(body.kind==='product')project.replacements.product=entry;else{const i=project.replacements.characters.findIndex(c=>c.source_id===body.source_id);if(i<0)project.replacements.characters.push(entry);else project.replacements.characters[i]=entry;}return {project:structuredClone(project),description:desc,look:entry.look,suggested};}
  if(url.endsWith('/cast/story-check')){project.replacements.product={...body.entry,story_checks_sig:'sig',story_checks:[{id:'k1',feature:'SOS',where:'6–9 秒，老人按键',question:'支持一键呼救吗？',answer:'',note:''},{id:'k2',feature:'通知',where:'手机提醒',question:'能通知吗？',answer:'',note:''}]};return {project:structuredClone(project)};}
  if(method==='GET')return structuredClone(project);return {};
 };
 run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={};renderReplacements();renderStoryboard();renderSteps()');
 const event=async(name,el,extra={})=>{assert.ok(el,'event target exists');for(const fn of events[name]||[])await fn({target:el,preventDefault(){},...extra});};
 installPageConfirmationHarness({run,context,document,dispatch:el=>event('click',el),autoConfirm});
 return {run,$,$$,context,document,requests,project,event,click:el=>event('click',el),fail:msg=>{failure=msg;},hold:p=>{hold=p;}};
}
async function main(){
 const t=setup();
 t.run('P.assets.push({id:"dad2",type:"image",roles:["person_identity"],file:"dad2.jpg",label:"另一张人物照"},{id:"internal",type:"image",roles:["scene"],internal:true,file:"internal.jpg",label:"内部截帧"},{id:"camera",type:"image",roles:["target_state"],camera_character:"C1",file:"camera.jpg",label:"旧主图"});renderReplacements(true)');
 assert.ok(!t.$('[data-cast-asset="C1"]').querySelectorAll('option').some(o=>['internal','camera'].includes(o.value)));
 assert.equal(t.$$('[data-flow-index]').length,5);
 assert.deepEqual(JSON.parse(t.run('JSON.stringify(workflowState().map(r=>r.label))')),['01 上传对标视频','02 拆解对标视频','03 选角和商品','04 对照和修改','05 成片']);
 assert.match(t.$('#directorView').textContent,/先完成上面的新分镜/);
 assert.match(t.$('#directorGeneration').textContent,/先完成上面的提示词/);
 assert.match(t.$('#step-5').textContent,/拼成整片预览/);
 assert.equal(t.$('#legacyWorkflow').open,false);assert.ok(t.$('#legacyWorkflow #scriptBtn'));assert.ok(t.$('#legacyWorkflow #taskCards'));assert.ok(t.$('#legacyWorkflow #finalView'));
 assert.ok(t.$('#step-5').nextElementSibling===t.$('#legacyWorkflow'));
 assert.doesNotMatch(t.$('#step-3').textContent,/分场表|主镜头图|不应出现的通知/);assert.equal(t.$('#cameraImages'),null);
 assert.equal(t.$('[data-detail-key="source-storyboard"]').open,false);t.$('[data-detail-key="source-storyboard"]').open=true;
 assert.equal(t.$$('#storyboardView .storyboard-sheet img').length,2);assert.equal(t.$$('[data-story-row]').length,10);assert.equal(t.$$('#storyboardView [data-story-cell]').length,10);
 assert.match(t.$('#storyboardView').textContent,/C1 父亲|按下按键|帮帮我|镜头中途商品出现/);
 await t.click(t.$('[data-story-cell="c01"]'));assert.equal(t.$('#storyboardLightbox').hidden,false);assert.match(t.$('#storyboardLightbox img').src,/cell-01.jpg/);const originalCellUrl=t.$('#storyboardLightbox img').src;
 await t.click(t.$('[data-story-preview-close]'));await t.click(t.$('[data-story-row="c02"]'));assert.match(t.$('#storyboardLightbox img').src,/cell-02.jpg/);
 assert.equal(t.$('[data-story-row="c10"] [data-story-merge]'),null);
 await t.click(t.$('[data-story-merge="c01"]'));assert.equal(t.requests.at(-1).body.action,'merge');assert.match(t.$('#storyboardView').textContent,/已手动合并 1 处/);await t.click(t.$('[data-story-cell="c01"]'));assert.notEqual(t.$('#storyboardLightbox img').src,originalCellUrl,'merged cell must reload its updated frame');
 await t.click(t.$('[data-story-action="restore"]'));assert.equal(t.requests.at(-1).body.action,'restore');
 t.run('P.storyboard_status={stale:true};renderStoryboard()');assert.match(t.$('#storyboardView').textContent,/拆解结果变了，原片分镜要重新生成/);
 t.run('P.storyboard=null;renderStoryboard()');assert.match(t.$('[data-story-action="build"]').textContent,/生成原片分镜（免费）/);
 t.run('P.analysis={steps:{storyboard:{status:"running"}}};renderStoryboard()');assert.match(t.$('#storyboardView').textContent,/正在截帧…/);assert.equal(t.$('[data-story-action="build"]').disabled,true);t.run('P.analysis={};renderStoryboard()');
 let release;t.hold(new Promise(r=>release=r));const building=t.click(t.$('[data-story-action="build"]'));await Promise.resolve();assert.match(t.$('#storyboardView').textContent,/正在截帧…/);release();await building;t.hold(null);
 assert.match(t.$('[data-cast-card="C1"]').textContent,/C1 父亲[\s\S]*白发[\s\S]*灰毛衣/);
 assert.match(t.$('[data-cast-card="product"]').textContent,/原片里的商品：SOS 手环[\s\S]*拆解里共 2 种写法[\s\S]*SOS 手环[\s\S]*备用手机/);
 await t.click(t.$('[data-cast-identify="C1"]'));assert.match(t.$('[data-cast-description="C1"]').value,/白色短发/);
 const note=t.$('[data-cast-description="C1"]');note.value='我写的';await t.event('input',note);
 await t.click(t.$('[data-cast-identify="C1"]'));assert.equal(t.$('[data-cast-description="C1"]').value,'我写的');assert.match(t.$('[data-cast-card="C1"]').textContent,/识别结果/);
 await t.click(t.$('[data-cast-apply="C1"][data-mode="append"]'));assert.match(t.$('[data-cast-description="C1"]').value,/我写的[\s\S]*白色短发/);
 await t.click(t.$('[data-cast-identify="C1"]'));await t.click(t.$('[data-cast-apply="C1"][data-mode="replace"]'));assert.doesNotMatch(t.$('[data-cast-description="C1"]').value,/我写的/);
 await t.click(t.$('[data-cast-add]'));assert.ok(t.$('[data-cast-card="N1"]'));const name=t.$('[data-cast-name="N1"]');name.value='医生';await t.event('input',name);
 const photo=t.$('[data-cast-asset="N1"]');photo.value='dad';await t.event('change',photo);assert.equal(t.run('readReplacements().characters.at(-1).action'),'add');
 await t.click(t.$('[data-story-check]'));assert.equal(t.$$('[data-story-answer]').length,6);assert.match(t.$('#replacementForm').textContent,/还有 2 条没确认，在第 04 步写提示词时会按“不确定”处理/);
 const no=t.$('[data-story-answer="k1"][value="no"]');no.checked=true;await t.event('change',no);const correction=t.$('[data-story-note="k1"]');correction.value='改成拨电话';await t.event('input',correction);
 assert.equal(t.run('readReplacements().product.story_checks[0].note'),'改成拨电话');
 const prod=t.$('[data-cast-description="product"]');prod.value='换成新表';await t.event('input',prod);assert.match(t.$('#replacementForm').textContent,/商品改过，建议检查/);
 await t.run('saveReplacements()');assert.equal(t.requests.at(-1).body.characters.at(-1).name,'医生');assert.equal(t.requests.at(-1).body.product.story_checks[0].answer,'no');
 t.fail('照片识别暂时没完成');await t.click(t.$('[data-cast-identify="C1"]'));assert.match(t.$('[data-cast-card="C1"]').textContent,/重试|照片识别暂时没完成/);t.fail('');
 // Editing another card while recognition runs must survive its response.
 let finish;t.hold(new Promise(r=>finish=r));const identifying=t.click(t.$('[data-cast-identify="C1"]'));await Promise.resolve();
 const concurrent=t.$('[data-cast-description="product"]');concurrent.value='识别时写下的商品说明';await t.event('input',concurrent);
 finish();await identifying;t.hold(null);assert.equal(t.$('[data-cast-description="product"]').value,'识别时写下的商品说明');
 // A suggestion belongs to the selected photo and disappears when that photo changes.
 await t.click(t.$('[data-cast-identify="C1"]'));assert.ok(t.$('[data-cast-apply="C1"]'));
 const different=t.$('[data-cast-asset="C1"]');different.value='dad2';await t.event('change',different);
 assert.equal(t.$('[data-cast-apply="C1"]'),null);assert.equal(t.run('readReplacements().characters[0].look'),undefined);
 // Trust the server when the submitted photo changed during a story check.
 const baseApi=t.context.mockApi;t.context.mockApi=async(...args)=>{const out=await baseApi(...args);if(args[1].endsWith('/cast/story-check'))out.project.story_checks_stale=true;return out;};t.run('api=mockApi');
 await t.click(t.$('[data-story-check]'));assert.match(t.$('#replacementForm').textContent,/商品改过，建议检查/);
 // A response from an old project cannot replace the active project's editor.
 let done;t.hold(new Promise(r=>done=r));const pending=t.click(t.$('[data-cast-identify="C1"]'));await Promise.resolve();t.run('P={...P,id:"other"}');done();await pending;assert.equal(t.run('P.id'),'other');
 console.log('PASS SB1: source sheets/list/zoom/build/merge/restore/stale; cast/add/identify/suggestions/retry; story answers/stale/save; five steps/closed legacy');
}
module.exports={setup,main};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
