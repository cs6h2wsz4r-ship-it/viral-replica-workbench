/* Round 18: real DOM renderer and event handlers, offline requests only. */
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const {createDocument}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'), source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const target=path.join(root,'runtime/temp/round18-ui-project.json');
fs.copyFileSync(path.join(root,'tools/fixtures/round24/p1028222359-9b21/project.json'),target);
const project=JSON.parse(fs.readFileSync(target,'utf8'));
const document=createDocument();
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
const events={}, requests=[]; document.addEventListener=(n,fn)=>(events[n] ||= []).push(fn);
const context=vm.createContext({document,console,project,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},confirm:()=>true});
vm.runInContext(source.slice(0,source.indexOf('// ---------- settings ----------')),context);
const start=source.indexOf('document.addEventListener("click", async (e) => {');
vm.runInContext(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)),context);
context.mockApi=async(method,url,body)=>{
 requests.push({method,url,body:structuredClone(body)});
 if(method==='GET')return structuredClone(project);
 if(url.endsWith('/replacement-check'))return {};
 if(url.endsWith('/camera-repair')){project.script.tasks[0].image_ids[0]='img1029085721-2d86';project.replacement_checks={};return structuredClone(project);}
 if(url.endsWith('/dialogue-adopt')){project.script.tasks[0].dialogue=body.lines.map(l=>l.proposed).join(' ');project.script.dialogue_mode='rewrite';return structuredClone(project);}
 if(url.endsWith('/script')){if(body?.script)project.script=body.script;return structuredClone(project);}
 return {};
};
vm.runInContext('api=mockApi;P=clone(project);S=clone(P.script);STATUS={};renderReplacements();renderSteps();',context);
const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s), run=s=>vm.runInContext(s,context);
const click=async el=>{assert.ok(el,'click target exists');for(const fn of events.click || [])await fn({target:el,preventDefault(){}});};
async function main(){
 assert.equal($$('[data-flow-index]').length,5);
 assert.equal($('[data-camera-key]'),null);assert.equal($('[data-scene-camera]'),null);
 assert.equal($('[data-camdraft]'),null);
 run('PAGE_BUILD="old";STATUS={build:"new",build_on_disk:"new",providers:{}};updateBuildBanner()');
 assert.equal($('#staleBanner').hidden,false);assert.match($('#staleBanner').textContent,/工作台已更新，请刷新页面（Cmd\+Shift\+R）/);
 assert.ok($('#refreshPageBtn'));
 const card=$('.scene-camera'), flow=$('#workflow').firstChild;
 for(let i=0;i<3;i++)run('renderReplacements();renderSteps()');
 assert.equal($('.scene-camera'),card);assert.equal($('#workflow').firstChild,flow);
 const proposal={lines:[{start:0,end:2,original:'your dress is beautiful',proposed:'your outfit is beautiful',changed:true,reason:'替换商品'}],model:'mock'};
 context.proposal=proposal;
 run('P.script.tasks[0].dialogue_proposal=proposal;S=clone(P.script);renderScript(true)');
 const edit=$('[data-dialogue-line="0"]');assert.ok(edit);edit.value='your outfit looks great';
 for(const fn of events.input || [])await fn({target:edit});
 assert.equal($('[data-dialogue-adopt]').hasAttribute('disabled'),false,'shortened line can be adopted');
 await click($('[data-dialogue-adopt]'));
 assert.equal(project.script.tasks[0].dialogue,'your outfit looks great');
 assert.equal(project.script.dialogue_mode,'rewrite');
 // SB1 top navigation never starts the old paid image flow.
 Object.getPrototypeOf($('#workflowNext')).scrollIntoView=function(){};
 requests.length=0;await click($('#workflowNext'));
 assert.equal(requests.filter(r=>r.url.endsWith('/confirm')).length,0);
 // Actual merged backend issue must expose one usable repair and one precise navigation.
 const fixturePath=path.join(root,'runtime/temp/round18-project.json');
 assert.ok(fs.existsSync(fixturePath),'run offline Python suite first to generate guard fixture');
 const fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
 Object.assign(project,fixture);context.incident=fixture;
 run('P=clone(incident);S=clone(P.script);renderReplacements(true);renderSteps();renderTasks(true)');
 const guide=$('[data-replacement-check="seg-01"]');
 fs.writeFileSync(path.join(root,'runtime/temp/round18-guide-debug.txt'),guide.innerHTML);
 assert.equal(guide.querySelectorAll('.fix-issue').length,1);
 assert.equal(guide.querySelectorAll('.fix-step [data-fix-step]').length,1);
 assert.equal(guide.querySelectorAll('[data-fix-step]').length,2);
 assert.equal(guide.querySelectorAll('[data-todo-target="dialogue-proposal"]').length,1);
 assert.match(guide.textContent,/seg-01 的图1 指向一张已删除或过期的主镜头图/);
 assert.match(guide.querySelector('.fix-step button').textContent,/去更换图1/);
 fs.writeFileSync(path.join(root,'runtime/temp/round18-rendered-text.txt'),$('#workflow').textContent+'\n\n'+guide.textContent);
 requests.length=0;await click(guide.querySelector('.fix-step button'));
 assert.match($('[data-task="seg-01"] .chips').getAttribute('class'),/fix-highlight/);
 await click($('[data-task="seg-01"] [data-fix-camera]'));
 assert.equal(requests.filter(r=>r.url.endsWith('/camera-repair')).length,1);
 assert.equal(project.script.tasks[0].image_ids[0],'img1029085721-2d86');
 // New placeholders do not trigger old generation actions.
 requests.length=0;run('renderSteps()');await click($('#workflowNext'));
 assert.equal(requests.filter(r=>r.url.endsWith('/preview')).length,0);
 // A cached current camera never overrides deletion/staleness from the latest response.
 run('P.camera_current={C1:{S1:"img1029085721-2d86"}};P.assets=P.assets.filter(a=>a.id!=="img1029085721-2d86")');
 assert.equal(run('cameraImageFor("C1","S1")'),null);
 // Request headers cover all write verbs and uploads while reads stay compatible.
 assert.deepEqual(JSON.parse(run('JSON.stringify(clientBuildHeaders("PUT"))')),{'X-Client-Build':'old'});
 assert.deepEqual(JSON.parse(run('JSON.stringify(clientBuildHeaders("GET"))')),{});
 console.log('Round 18 UI: workflow / identity / build / editable proposal / stable DOM passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
