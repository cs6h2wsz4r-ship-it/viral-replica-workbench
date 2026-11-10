/* 24.17: self-made project, actual scene editor/events, HTTP mocked. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
for(const property of ['hidden','disabled'])Object.defineProperty(Node.prototype,property,{get(){return this.hasAttribute(property);},set(v){v?this.setAttribute(property,''):this.removeAttribute(property);},configurable:true});
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
function setup(){
  const document=createDocument(),events={},requests=[];
  document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const project={id:'offline-24-17',name:'自己造的父子四场',assets:[
    {id:'dad',label:'老人照片',file:'dad.jpg',type:'image',roles:['person_identity']},{id:'son',label:'男子照片',file:'son.jpg',type:'image',roles:['person_identity']},
    {id:'other',label:'另一位老人',file:'other.jpg',type:'image',roles:['person_identity']},{id:'watch',label:'手表',file:'watch.jpg',type:'image',roles:['product_truth']}],
    breakdown:{characters:[{id:'C1',name:'父亲'},{id:'C2',name:'儿子'},{id:'C3',name:'旁白',appearance:'画外角色'}],
      scene:[{id:'S1',name:'门廊',timeRanges:[[0,4.8]]},{id:'S2',name:'展示',timeRanges:[[4.8,5.8]]},{id:'S3',name:'佩戴',timeRanges:[[5.8,9]]},{id:'S4',name:'厨房',timeRanges:[[9,12.3]]}],
      shots:[{start:0,end:4.8,characters:['C1'],product:'无商品'},{start:4.8,end:5.8,characters:['C1'],product:'手环'},{start:5.8,end:9,characters:['C1'],product:'手环'},{start:9,end:10.58,characters:['C1'],product:'手环'},{start:10.58,end:12.3,characters:['C1','C2'],product:'手环'}],productMoments:[{start:4.8,end:12.3,product:'手环'}]},
    replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'dad',description:''},{source_id:'C2',action:'replace',asset_id:'son',description:''},{source_id:'C3',action:'keep'}],product:{action:'replace',asset_id:'watch',description:'智能手表'},scene:''},images:{},script:{tasks:[]},runs:{},jobs:{},scene_cast:{}};
  for(const scene of project.breakdown.scene){const keys=['C1',...(scene.id==='S4'?['C2']:[]),...(scene.id==='S1'?[]:['product'])];project.scene_cast[scene.id]={scene_id:scene.id,name:scene.name,time_ranges:scene.timeRanges,subjects:keys.map(key=>({key,source_id:key,name:key==='product'?'商品':key==='C1'?'父亲':'儿子',kind:key==='product'?'product':'character',on:true,asset_id:key==='product'?'watch':key==='C1'?'dad':'son',override:false,added:false})),camera_candidates:[],frame_choices:[{at:11.4,file:'shot-kitchen.jpg',characters:['C1','C2'],present_text:'父亲、儿子都在画面里'}]};}
  let hold;
  const context=vm.createContext({document,console,project,structuredClone,confirm:()=>true,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  run(source.slice(0,source.indexOf('// ---------- settings ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  context.mockApi=async(method,url,body)=>{requests.push({method,url,body:structuredClone(body)});
    if(url.endsWith('/replacements')){project.replacements=structuredClone(body);return structuredClone(project);}
    if(url.endsWith('/scene-camera/frame')){if(hold)await hold;const frame={asset_id:'frame-'+body.scene_id,at:body.at??11.4,file:'frame-'+body.scene_id+'.jpg',characters:['C1','C2'],present_text:'父亲、儿子都在画面里',reason:'儿子 10.6 秒后才进画面，已选他在画面里的一帧'};project.assets.push({id:frame.asset_id,type:'image',file:frame.file,internal:true});project.scene_cast[body.scene_id].frame=frame;return frame;}
    if(url.endsWith('/scene-camera/draft'))return {prompt:'图1 父亲，图2 儿子，图3 商品，图4 原片参考帧',image_ids:['dad','son','watch','frame-S4'],frame_asset_id:'frame-S4',for_task:'scene-camera:'+body.scene_id,camera_binding:'scene-binding',frame_at:11.4};
    if(url.endsWith('/images/preview')){project.images.new={id:'new',for_task:body.for_task,state:'pending_confirmation',channel:'third_party',image_ids:body.image_ids,preview:{prompt:body.prompt},camera_binding:body.camera_binding};return project.images.new;}
    if(url.endsWith('/scene-camera/use')){project.scene_cast[body.scene_id].camera_asset=body.asset_id;return structuredClone(project);}
    if(url.endsWith('/confirm'))return {};
    if(method==='GET')return structuredClone(project);
    throw Error('Unexpected mock API '+url);
  };
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={};renderReplacements()');
  const event=async(name,el)=>{assert.ok(el,'event target exists');for(const fn of events[name]||[])await fn({target:el,preventDefault(){}});};
  return {run,$,$$,project,requests,event,click:el=>event('click',el),hold:promise=>{hold=promise;}};
}
const tests=[];
(() => {
  const t=setup();
  assert.equal(t.$('[data-scene-camera]'),null);
  assert.equal(t.$('[data-camera-key]'),null);
  assert.equal(t.$('[data-camdraft]'),null);
  assert.doesNotMatch(t.$('#step-3').textContent,/分场表|主镜头图/);
  assert.ok(t.$('[data-cast-card]'));
  console.log('PASS SB1: 第03步只做选角和商品设定，旧分场与主镜头图不再出现');
})();
