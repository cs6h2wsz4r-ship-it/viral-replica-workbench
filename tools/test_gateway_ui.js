/* Round 20: offline rendering, explicit retry and dual-build tests. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(),events={},requests=[];
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(n,fn)=>(events[n] ||= []).push(fn);
const project={id:'offline-round20',source:{},assets:[{id:'person',file:'person.png',roles:['person_identity'],type:'image'}],analysis:{},breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'室内',timeRanges:[[0,8]]}],shots:[{start:0,end:8,characters:['C1']}]},replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person'}],product:{action:'keep'}},script:{tasks:[]},images:{}};
const status={server_build:'server-one',web_build:'web-one',build:'legacy',build_on_disk:'legacy',default_llm:'openai',default_image_channel:'official',providers:{openai:{label:'官方 · GPT-5.6 Sol',ready:true},jimmy:{label:'三方渠道 · GPT-5.6 Sol',ready:true}}};
let fail='';
const context=vm.createContext({document,console,project,status,structuredClone,FormData:class {entries(){return [];}},setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},confirm:()=>true});
vm.runInContext(source.slice(0,source.indexOf('// ---------- events ----------')),context);
const start=source.indexOf('document.addEventListener("click", async (e) => {');vm.runInContext(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)),context);
context.mockApi=async(method,url,body)=>{requests.push({method,url,body:structuredClone(body)});if(url==='status')return structuredClone(status);if(url==='settings')return {_providers:status.providers,DEFAULT_LLM:'openai',OPENAI_API_KEY:'••••••'};if(method==='GET')return structuredClone(project);if(fail&&url.endsWith(fail))throw Error('官方额度不足');if(url.endsWith('/camera-image/draft'))return {prompt:'保留场景',for_task:'camera:C1:S1',image_ids:['person'],ratio:'9:16',camera_binding:'offline'};if(url.endsWith('/images/preview')){const r={id:'preview'+requests.length,state:'pending_confirmation',channel:body.channel,for_task:body.for_task,preview:{prompt:body.prompt,model:body.model},image_ids:body.image_ids};project.images[r.id]=r;return r;}if(url.endsWith('/dialogue-proposal'))return {provider:body.provider,lines:[]};return {};};
const run=s=>vm.runInContext(s,context),$=s=>document.querySelector(s),click=async el=>{assert.ok(el,'button exists');for(const fn of events.click||[])await fn({target:el,preventDefault(){}});};
async function main(){
 run('api=mockApi;P=clone(project);P.script.provider="deepseek";P.breakdown_provider="jimmy";S=clone(P.script);STATUS=clone(status);renderAnalysis();renderScript();renderReplacements()');
 assert.equal($('#analyzeProvider').value,'');assert.equal($('#scriptProvider').value,'');assert.equal(run('selectedProvider($("#analyzeProvider"))'),'openai');
 run('P.routes={script:"deepseek:custom-model"};STATUS.providers.deepseek={label:"三方渠道 · DeepSeek",ready:true};$("#scriptProvider").dataset.init="";renderScript()');assert.equal($('#scriptProvider').value,'');assert.equal(run('selectedProvider($("#scriptProvider"))'),'deepseek');assert.match($('[data-legacy-route="script"]').textContent,/这个项目之前单独选过/);
 run('$("#scriptProvider").dataset.userSelected="1";$("#scriptProvider").value="jimmy"');assert.equal(run('selectedProvider($("#scriptProvider"))'),'jimmy');
 run('P.resolved_routes={dialogue_proposal:{channel:"openai",model:"independent"}}');assert.equal(run('routeFor("dialogue_proposal").model'),'independent');
 $('#settingsDialog').showModal=()=>{};await run('openSettings()');
 assert.match($('#settingsFields').textContent,/单独给某一步换模型/);for(const step of ['ANALYSIS','SCRIPT','CHECK','DIALOGUE_PROPOSAL','CAMERA_PROMPT','CAMERA_IMAGE','VIDEO'])assert.ok($(`[name="ROUTE_${step}"]`));
 assert.equal(document.querySelectorAll('[data-test-channel]').length,2);assert.match($('#settingsFields').textContent,/会产生极少量费用/);
 await click($('[data-test-channel="openai"]'));assert.equal(requests.at(-1).url,'settings/test-connection');assert.equal(requests.at(-1).body.channel,'openai');
 run('P.usage=[{channel:"openai",kind:"reason",ok:true,tokens:{prompt:7,completion:4},billing:{unit:"tokens",amount:11}},{channel:"third_party",kind:"image",ok:true,billing:{unit:"点",amount:.02}},{channel:"third_party",kind:"video",ok:true,billing:{unit:"秒",amount:8}}]');
 const usage=run('usageHtml()');assert.match(usage,/官方 1 次/);assert.match(usage,/约 11 个 token/);assert.match(usage,/0.02 点/);assert.match(usage,/8.00 秒/);
 run('P.breakdown_versions={old:{provider:"jimmy",model:"吉米 · GPT-5.6 Sol（共用 Jimmy Key） · 外部计费"}};renderAnalysis()');assert.doesNotMatch($('#breakdownVersions').textContent,/吉米|Jimmy/);
 assert.doesNotMatch(run('imageRecordCard({id:"old-image",state:"completed",preview:{model:"吉米 · Jimmy"}})'),/吉米|Jimmy/);
 assert.match(source,/完整任务单（三方渠道请求）/);assert.doesNotMatch(source,/完整任务单 jimmy-job/);
 console.log('Round 21 UI: routes, connection buttons, historical text, and usage passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
