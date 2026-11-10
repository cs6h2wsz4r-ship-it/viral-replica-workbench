/* 24.6 actual DOM events, deferred mock requests and a deterministic clock. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(),events={},requests=[],timers=new Map();let now=0,timerId=0,hold=null,failSave=false;
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
const texts=['Excuse me, your dress is beautiful.','Oh, thank you so much.',"It's so flowy and comfortable.",'I love the open back.','The fabric is perfect for this weather.'];
const proposal={lines:texts.map((original,i)=>({start:i*3,end:i*3+[1.82,2,1.36,.82,2.5][i],original,
  proposed:['Your look is nice.',texts[1],'It feels nice.','Love this.',texts[4]][i],changed:![1,4].includes(i),restored:i===4,speaker:i?'C1':'C2',reason:'商品冲突'}))};
const task={id:'seg-01',kind:'speaker',language:'en',duration:15,source_start:0,source_end:15,dialogue:texts.join('\n'),dialogue_proposal_requested:true};
const check={errors:[],warnings:[],issues:[{code:'dialogue_conflict',step:'04',title:'台词冲突',details:[]}],dialogue_conflicts:[{title:'台词冲突',details:[]}]};
const project={id:'offline-24-6',assets:[],images:{},breakdown:{characters:[{id:'C1'},{id:'C2',name:'画外角色'}],overview:{language:'en'}},
  resolved_routes:{script:{channel:'deepseek',model:'rewrite-model'},dialogue_proposal:{channel:'deepseek',model:'rewrite-model'}},
  script:{tasks:[task]},replacement_checks:{'seg-01':check}};
const status={providers:{deepseek:{label:'DeepSeek',model:'rewrite-model',ready:true}}};
const context=vm.createContext({document,console,project,status,structuredClone,confirm:()=>true,Date:class extends Date {static now(){return now;}},
  setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,at:now+ms});return timerId;},clearTimeout:id=>timers.delete(id),localStorage:{getItem(){},setItem(){}}});
const run=s=>vm.runInContext(s,context),$=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
run(source.slice(0,source.indexOf('// ---------- settings ----------')));
const eventStart=source.indexOf('document.addEventListener("click", async (e) => {');
run(source.slice(eventStart,source.indexOf('\n$("#newProjectBtn").onclick',eventStart)));
context.mockApi=async(method,url,body)=>{
  requests.push({method,url,body:structuredClone(body)});
  if(url.endsWith('/dialogue-proposal'))return hold ? await hold.promise : structuredClone(proposal);
  if(url.endsWith('/script')){if(failSave)throw Error('保存脚本失败');project.script=structuredClone(body.script);return structuredClone(project);}
  if(url.endsWith('/replacement-check'))return structuredClone(project.replacement_checks);
  if(method==='GET')return structuredClone(project);
  throw Error('Unexpected request '+url);
};
const box=()=>$('#scriptView [data-dialogue-box]');
const fire=async(name,el)=>{assert.ok(el);for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function reset(withProposal=false){
  requests.length=0;timers.clear();hold=null;failSave=false;now=0;
  project.script={tasks:[structuredClone(task)]};if(withProposal)project.script.tasks[0].dialogue_proposal=structuredClone(proposal);
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS=clone(status);dirty.script=false;dialogueProposalEdits.clear();operationErrors.clear();dialogueFailureKinds.clear();renderScript(true);renderTasks(true)');
}
async function tick(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);await t.fn();}}
function assertBusy(seconds,saving=false){for(const b of $$('[data-dialogue-box]')){
  const button=b.querySelector('[data-dialogue-propose]');assert.ok(button.hasAttribute('disabled'));
  assert.equal(button.textContent,`${saving ? '正在保存并做安全校对' : '正在改写'}…（已等待 ${seconds} 秒）`);
  assert.ok(b.querySelector('[data-dialogue-keep]').hasAttribute('disabled'));
  assert.equal(b.querySelector('[data-dialogue-shorten]'),null);
}}
async function main(){
  reset();
  assert.equal(box().querySelector('[data-dialogue-propose]').textContent,'改写冲突台词');
  assert.match(box().textContent,/会调用一次 .*rewrite-model.*，通常需要 20–60 秒/);
  assert.doesNotMatch(document.body.textContent,/免费预览前的模型调用/);
  const jump=$('[data-todo-target="dialogue-proposal"]');assert.ok(jump);assert.equal(jump.textContent,'去核对台词');
  let scrolled=false;const target=$('#scriptView [data-dialogue-box]');target.scrollIntoView=()=>{scrolled=true;};
  await fire('click',jump);assert.ok(scrolled);assert.match(target.getAttribute('class'),/fix-highlight/);
  hold=deferred();const click=fire('click',box().querySelector('[data-dialogue-propose]'));
  assertBusy(0);assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-proposal')).length,1);
  await fire('click',box().querySelector('[data-dialogue-propose]'));
  await run('requestDialogueProposal("seg-01",document.querySelector("#scriptView [data-dialogue-box]"))');
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-proposal')).length,1,'same segment cannot submit twice');
  await fire('click',box().querySelector('[data-dialogue-keep]'));
  await tick(1000);assertBusy(1);
  run('renderScript(true);renderTasks(true)');assertBusy(1);
  await tick(2000);assertBusy(3);
  // A polling response goes through the normal render path without clearing activity.
  await run('refresh(true)');assertBusy(3);
  hold.resolve(structuredClone(proposal));await click;hold=null;
  assert.equal(box().querySelector('[data-dialogue-propose]').hasAttribute('disabled'),false);
  assert.equal(box().querySelector('[data-dialogue-propose]').textContent,'按我的要求重新改写');
  assert.equal(box().querySelector('[data-dialogue-keep]').hasAttribute('disabled'),false);
  assert.match(document.body.textContent,/已生成改写方案，请核对后点“采用这版”/);
  const restored=box().querySelector('[data-dialogue-line="4"]').closest('tr');
  assert.equal(restored.querySelector('[data-dialogue-reason]').textContent,'不涉及新人物或新商品，保留原句');
  assert.equal(restored.querySelector('[data-dialogue-restored]'),null);
  await tick(1000);await fire('click',box().querySelector('[data-dialogue-propose]'));
  await tick(3000);assert.match(box().textContent,/已生成改写方案，请核对后点“采用这版”/,'an older success timer cannot clear a newer notice');
  await tick(1000);assert.doesNotMatch(box().textContent,/已生成改写方案，请核对后点“采用这版”/);
  reset();hold=deferred();const failed=fire('click',box().querySelector('[data-dialogue-propose]'));
  hold.reject(Error('第 3 句，原句「flowy」：改写方案改变了原句或时间'));await assert.rejects(failed,/第 3 句/);hold=null;
  for(const b of $$('[data-dialogue-box]')){
    const error=b.querySelector('[data-dialogue-error]');assert.ok(error);assert.equal(error.getAttribute('role'),'alert');
    assert.match(error.textContent,/第 3 句.*flowy/);assert.match(error.textContent,/可以再点一次改写，或点保持原台词/);
    assert.equal(b.querySelector('[data-dialogue-propose]').hasAttribute('disabled'),false);
    assert.equal(b.querySelector('[data-dialogue-keep]').hasAttribute('disabled'),false);
  }
  run('renderScript(true);renderTasks(true)');assert.ok(box().querySelector('[data-dialogue-error]'));
  hold=deferred();const retry=fire('click',box().querySelector('[data-dialogue-propose]'));
  assert.equal(box().querySelector('[data-dialogue-error]'),null,'retry clears the previous error immediately');
  hold.resolve(structuredClone(proposal));await retry;hold=null;
  // Regenerating a long draft keeps the ordinary lock, timer and error recovery.
  reset(true);run('P.script.tasks[0].dialogue_proposal.lines[2].proposed="It is so wonderfully beautiful and comfortable.";renderScript(true)');
  hold=deferred();const regenerate=fire('click',box().querySelector('[data-dialogue-propose]'));assertBusy(0);
  assert.equal(box().querySelector('[data-dialogue-shorten]'),null);
  await tick(1000);assertBusy(1);
  await fire('click',box().querySelector('[data-dialogue-propose]'));assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-proposal')).length,1);
  hold.resolve(structuredClone(proposal));await regenerate;hold=null;
  assert.equal(box().querySelector('[data-dialogue-propose]').hasAttribute('disabled'),false);
  run('P.script.tasks[0].dialogue_proposal.lines[2].proposed="It is so wonderfully beautiful and comfortable.";renderScript(true)');
  hold=deferred();const regenerateFailure=fire('click',box().querySelector('[data-dialogue-propose]'));
  hold.reject(Error('网络超时'));await assert.rejects(regenerateFailure,/网络超时/);hold=null;
  assert.match(box().querySelector('[data-dialogue-error]').textContent,/网络超时/);
  assert.equal(box().querySelector('[data-dialogue-propose]').hasAttribute('disabled'),false);
  // Lock covers the script save before the model call; save failure is local and unlocks.
  reset();run('dirty.script=true');failSave=true;
  const saveFailure=fire('click',box().querySelector('[data-dialogue-propose]'));assertBusy(0,true);
  await assert.rejects(saveFailure,/保存脚本失败/);
  assert.match(box().querySelector('[data-dialogue-error]').textContent,/保存脚本失败/);
  assert.equal(box().querySelector('[data-dialogue-propose]').hasAttribute('disabled'),false);
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-proposal')).length,0);
  // A different segment remains actionable while this segment is waiting.
  reset();run('P.script.tasks.push({...clone(P.script.tasks[0]),id:"seg-02"});renderScript(true)');
  hold=deferred();const first=fire('click',box().querySelector('[data-dialogue-propose]'));
  assert.equal($('#scriptView [data-dialogue-propose="seg-02"]').hasAttribute('disabled'),false);
  hold.resolve(structuredClone(proposal));await first;
  // Switching to another project with the same segment id must not share its lock or result.
  reset();hold=deferred();const switched=fire('click',box().querySelector('[data-dialogue-propose]'));
  run('P={...clone(project),id:"other-project"};renderScript(true);renderTasks(true)');
  assert.equal(box().querySelector('[data-dialogue-propose]').hasAttribute('disabled'),false);
  await tick(1000);assert.equal(box().querySelector('[data-dialogue-propose]').textContent,'改写冲突台词');
  run('P=clone(project);renderScript(true);renderTasks(true)');assertBusy(1);
  run('P={...clone(project),id:"other-project"};renderScript(true);renderTasks(true)');
  hold.resolve(structuredClone(proposal));await switched;
  assert.equal(run('P.id'),'other-project');assert.equal(box().querySelector('[data-dialogue-adopt]'),null);
  console.log('Round 24.6 UI: labels / restored reasons / immediate lock / clock / duplicate prevention / poll / error / regeneration passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
