/* 24.5: actual DOM renderer and input/click events; no browser, server or network. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(),events={},requests=[];
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
const originals=['Excuse me, your dress is beautiful.',"It's so flowy and comfortable.",'I love the open back.'];
const lines=originals.map((original,i)=>({start:[0,2,4][i],end:[1.82,3.36,4.82][i],original,proposed:['Your look is nice.','It is so wonderfully beautiful and comfortable.','Love this.'][i],changed:true,reason:'商品冲突',speaker:'C1'}));
const project={id:'offline-24-5',assets:[],images:{},breakdown:{characters:[{id:'C1'}],overview:{language:'en'}},
  resolved_routes:{script:{channel:'openai',model:'route-script-model'},dialogue_proposal:{channel:'openai',model:'route-script-model'}},
  script:{tasks:[{id:'seg-01',kind:'speaker',language:'en',duration:6,dialogue:'Old dialogue',dialogue_proposal:{lines,model:'previous-model'}}]}};
const status={providers:{openai:{label:'官方 · label-model',model:'status-model',ready:true},jimmy:{label:'三方渠道',model:'third-model',ready:true}}};
const context=vm.createContext({document,console,project,status,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},confirm:()=>true});
vm.runInContext(source.slice(0,source.indexOf('// ---------- settings ----------')),context);
const start=source.indexOf('document.addEventListener("click", async (e) => {');
vm.runInContext(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)),context);
let failure=null;
context.mockApi=async(method,url,body)=>{
  requests.push({method,url,body:structuredClone(body)});
  if(url.endsWith('/dialogue-proposal')) {if(failure)throw failure;return structuredClone(project.script.tasks[0].dialogue_proposal);}
  if(url.endsWith('/dialogue-adopt')) {project.script.tasks[0].dialogue=body.lines.map(l=>l.proposed).join('\n');return structuredClone(project);}
  if(url.endsWith('/script')) {project.script=structuredClone(body.script);return structuredClone(project);}
  if(url.endsWith('/replacement-check'))return {};
  throw Error('Unexpected request '+method+' '+url);
};
const run=s=>vm.runInContext(s,context),$=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
const event=async(name,el)=>{assert.ok(el,`${name} target exists`);for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
const box=()=>$('#scriptView [data-dialogue-box]');
function reset(){requests.length=0;failure=null;run('P=clone(project);S=clone(P.script);STATUS=clone(status);dirty.script=false;dialogueProposalEdits.clear();operationErrors.clear();dialogueFailureKinds.clear();renderScript(true);renderTasks(true)');}
async function main(){
  // 24.7: length is advisory and never disables adoption.
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS=clone(status);renderScript(true);renderTasks(true)');
  assert.equal($('[data-dialogue-adopt]',box()).hasAttribute('disabled'),false,'overlong line remains adoptable');
  assert.equal(box().querySelectorAll('.dialogue-too-long').length,0);
  assert.equal(box().querySelector('[data-dialogue-length-warning]').textContent,'这句比原句长不少，生成后注意听是否说得太快');
  assert.doesNotMatch(box().textContent,/估算 .*音节|最多 .*音节|超出/);
  assert.match(box().textContent,/改写模型：官方 · route-script-model（跟随设置里的推理模型）/);
  assert.doesNotMatch(box().textContent,/label-model|previous-model/);
  run('P.resolved_routes.dialogue_proposal.model="route-after-refresh";renderScript()');
  assert.match(box().querySelector('[data-dialogue-model]').textContent,/route-after-refresh/,'route-only refresh updates cached script header');
  run('P.resolved_routes.dialogue_proposal.model="route-script-model";renderScript()');
  assert.equal(box().querySelector('[data-dialogue-shorten]'),null);
  assert.equal(box().querySelector('[data-third-retry="dialogue"]'),null);
  // Local input updates both visible copies, preserves focus, and sends no API calls.
  const edit=box().querySelector('[data-dialogue-line="1"]');edit.focus();edit.value='It feels nice.';
  await event('input',edit);
  assert.equal(requests.length,0,'editing uses local budget calculation');
  assert.equal(document.activeElement,edit);
  assert.equal(box().querySelectorAll('.dialogue-too-long').length,0);
  assert.equal(box().querySelector('[data-dialogue-length-warning]'),null);
  assert.equal(box().querySelector('[data-dialogue-adopt]').hasAttribute('disabled'),false);
  for(const el of $$('[data-dialogue-box]'))assert.equal(el.querySelector('[data-dialogue-line="1"]').value,'It feels nice.');
  assert.equal(run('dirty.script'),false,'proposal editing does not change the active script');
  run('renderScript(true);renderTasks(true)');
  assert.equal(box().querySelector('[data-dialogue-line="1"]').value,'It feels nice.','poll/re-render retains edits');
  await event('click',box().querySelector('[data-dialogue-adopt]'));
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-adopt')).length,1);
  assert.equal(requests.find(r=>r.url.endsWith('/dialogue-adopt')).body.lines[1].proposed,'It feels nice.');
  // Count parity with Python, including silent-e exceptions, compatibility Han and Unicode decimal digits.
  for(const [text,count] of [['Excuse me, your dress is beautiful.',9],["It's so flowy and comfortable.",9],['I love the open back.',6],['make love table coffee rhythm',7],['中文 １２٣4',6]]) {
    context.sample=text;assert.equal(run('speechUnits(sample)'),count,text);
  }
  // Length reminders use original units only, independent of language rates and time windows.
  for(const [lang,rate] of [['zh',5.25],['en',4.6],['ja',7.5],['es',5.9],['fr',4.6]]) {
    context.lang=lang;const metrics=JSON.parse(run('JSON.stringify(dialogueLineMetrics({language:lang},{start:.001,end:.5731,original:"Lovely dress",proposed:"Wonderful outfit"}))'));
    assert.equal(metrics.length_warning,false);
    assert.equal(metrics.changed,true);
  }
  // Ordinary regeneration receives current manual changes; no separate shortening path.
  project.script.tasks[0].dialogue_proposal.lines[1].proposed='It is so wonderfully beautiful and comfortable.';reset();
  const retryEdit=box().querySelector('[data-dialogue-line="1"]');retryEdit.value='It is really wonderfully beautiful and comfortable.';await event('input',retryEdit);
  await event('click',box().querySelector('[data-dialogue-propose]'));
  const retry=requests.find(r=>r.url.endsWith('/dialogue-proposal'));
  assert.ok(retry);assert.equal(retry.body.instruction,'');
  assert.equal(retry.body.previous_proposal.lines[1].proposed,retryEdit.value);
  assert.equal(retry.body.previous_proposal.lines[1].length_warning,true);
  assert.equal(box().querySelector('[data-dialogue-shorten]'),null);
  // Only channel failures offer channel retry.
  reset();failure=Object.assign(Error('官方额度不足'),{kind:'quota'});
  await assert.rejects(event('click',box().querySelector('[data-dialogue-propose]')),/官方额度不足/);
  assert.ok(box().querySelector('[data-third-retry="dialogue"]'));
  assert.equal(box().querySelector('[data-dialogue-shorten]'),null);
  failure=null;requests.length=0;await event('click',box().querySelector('[data-third-retry="dialogue"]'));
  assert.equal(requests.find(r=>r.url.endsWith('/dialogue-proposal')).body.provider,'jimmy');
  assert.equal(box().querySelector('[data-third-retry="dialogue"]'),null);
  for(const [kind,message] of [['transient','网络连接超时'],['invalid_request','改写方案改变了原句或时间'],['empty','模型空回复']]) {
    reset();failure=Object.assign(Error(message),{kind});await assert.rejects(event('click',box().querySelector('[data-dialogue-propose]')),new RegExp(message));
    assert.equal(!!box().querySelector('[data-third-retry="dialogue"]'),kind==='transient');
    assert.equal(box().querySelector('[data-dialogue-shorten]'),null);
  }
  // Programmatic adoption also accepts long text; empty text remains blocked.
  reset();await run('adoptDialogueProposal("seg-01",document.querySelector("#scriptView [data-dialogue-box]"))');
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-adopt')).length,1);
  const empty=box().querySelector('[data-dialogue-line="1"]');empty.value='';await event('input',empty);
  assert.ok(box().querySelector('[data-dialogue-adopt]').hasAttribute('disabled'));
  // Explicit selections must show the server-resolved fallback, including a missing official key.
  run('STATUS.dialogue_provider_routes={openai:{channel:"jimmy",model:"fallback-model"}};STATUS.providers.openai.ready=false;$("#scriptProvider").value="openai";$("#scriptProvider").dataset.userSelected="1"');
  await event('change',$('#scriptProvider'));
  assert.match(box().querySelector('[data-dialogue-model]').textContent,/三方渠道 · fallback-model/);
  console.log('Round 24.5 UI: gentle warnings / live edits / adoption / channel retry / resolved model passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
