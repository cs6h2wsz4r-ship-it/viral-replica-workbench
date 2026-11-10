/* Round 22: actual card renderer and zoom handler, no browser/network service. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(),events={};
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
const overlap='参考帧的字幕区域与人物/商品重叠，已模糊处理，生成后请检查这一块';
const project={id:'offline-round22',assets:[
 {id:'person',file:'person.png',type:'image'},
 {id:'original',file:'original.jpg',internal:true,frame_at:3.36},
 {id:'clean',file:'clean.png',internal:true,derived_from:'original',caption_overlap_warning:overlap,
  caption_residue:{status:'checked',suspected:[{score:.01}]}}],
 breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'室外',timeRanges:[[0,10]]}]},
 replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person'}],product:{action:'keep'}},
 images:{one:{id:'one',for_task:'camera:C1:S1',channel:'official',state:'pending_confirmation',
  frame_asset_id:'original',clean_frame_asset_id:'clean',image_ids:['person','clean'],preview:{prompt:'ready'}}},script:{tasks:[]}};
const context=vm.createContext({document,console,project,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
vm.runInContext(source.slice(0,source.indexOf('// ---------- settings ----------')),context);
const start=source.indexOf('document.addEventListener("click", async (e) => {');
vm.runInContext(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)),context);
const run=s=>vm.runInContext(s,context),$=s=>document.querySelector(s);
async function main(){
 run('P=clone(project);S=clone(P.script);STATUS={default_image_channel:"official"};renderReplacements(true)');
 assert.equal($('.scene-camera'),null);assert.doesNotMatch($('#step-3').textContent,/主镜头图|疑似字幕残留/);
 // Exclude explicit previews, zero/unknown historical previews, retain real unmetered calls.
 run(`P.usage=[
  {channel:'official',kind:'preview',billing:{unit:'tokens',amount:99}},
  {channel:'third_party',kind:'preview',billing:{unit:'点',amount:99}},
  {channel:'official',kind:'image',ok:true,tokens:{prompt:null,completion:null},billing:{unit:'tokens',amount:null}},
  {channel:'official',kind:'image',ok:true,tokens:{prompt:0,completion:0},billing:{unit:'tokens',amount:0}},
  {channel:'official',kind:'image',ok:true,network_request:true,tokens:{},billing:{unit:'tokens',amount:null}},
  {channel:'official',kind:'image',ok:true,tokens:{prompt:5,completion:8},billing:{unit:'tokens',amount:13}},
  {channel:'third_party',kind:'image',ok:true,billing:{unit:'点',amount:.02}}]`);
 const before=run('JSON.stringify(P.usage)'),html=run('usageHtml()');
 assert.match(html,/官方 2 次 \/ 约 13 个 token/);assert.match(html,/三方渠道 0.02 点/);
 assert.equal(run('JSON.stringify(P.usage)'),before,'历史文件/数据不被改写');
 run('cameraDrafts.clear();P.images={};P.assets=P.assets.filter(a=>a.id!=="clean");renderReplacements(true)');
 assert.equal($('.scene-camera'),null);
 console.log('Round 22 UI: original/clean frames, zoom, overlap/residue warnings and preview-free usage passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
