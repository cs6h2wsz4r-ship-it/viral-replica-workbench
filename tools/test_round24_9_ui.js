/* 24.9: saved dialogue status through actual rendering/events and offline API mocks. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
for(const property of ['disabled','hidden'])Object.defineProperty(Node.prototype,property,{
  get(){return this.hasAttribute(property);},
  set(value){value ? this.setAttribute(property,'') : this.removeAttribute(property);},configurable:true,
});
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const adoptedText='✓ 已采用改写后的台词',unsavedText='改动还没保存，点“采用这版”生效';
const todoText='核对并改写本段提到旧外观的台词';
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function setup(choice){
  const document=createDocument(),events={},requests=[],timers=new Map();let now=0,timerId=0;
  document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const lines=[{start:0,end:2,original:'Your dress is beautiful.',proposed:'Your outfit is beautiful.',speaker:'C1',changed:true},
    {start:2,end:4,original:'I love the open back.',proposed:'I love the knit top.',speaker:'C1',changed:true}];
  const task={id:'seg-01',title:'第一段',kind:'speaker',mode:'image',language:'en',duration:4,ratio:'9:16',source_start:0,source_end:4,
    dialogue_choice:choice,dialogue_proposal:{lines},image_ids:[],
    dialogue_lines:lines.map(l=>({start:l.start,end:l.end,speaker:l.speaker,text:choice==='adopt' ? l.proposed : l.original}))};
  task.dialogue=task.dialogue_lines.map(l=>l.text).join('\n');
  const project={id:'offline-24-9',name:'离线测试',source:{},assets:[],images:{},runs:{},log:[],
    breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'客厅',timeRanges:[[0,4]]}],overview:{language:'en'}},
    script:{tasks:[task]},replacement_checks:{}};
  // Mirrors replacement_guard.dialogue_conflicts: resolved choices yield no conflict.
  const checks=()=>Object.fromEntries(project.script.tasks.map(t=>{
    const resolved=['adopt','keep'].includes(t.dialogue_choice);
    const conflict={code:'dialogue_conflict',step:'04',title:'台词里有 1 处提到原片的服装特征，和新商品可能不符',details:['旧服装']};
    return [t.id,{errors:[],warnings:resolved?[]:[conflict.title],issues:[],dialogue_conflicts:resolved?[]:[conflict]}];
  }));
  project.replacement_checks=checks();
  const state={hold:null,fail:null};
  const context=vm.createContext({document,console,project,structuredClone,confirm:()=>true,
    Date:class extends Date{static now(){return now;}},setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,at:now+ms});return timerId;},clearTimeout:id=>timers.delete(id),
    localStorage:{getItem(){},setItem(){}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  run(source.slice(0,source.indexOf('// ---------- settings ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  context.mockApi=async(method,url,body)=>{
    requests.push({method,url,body:structuredClone(body)});
    if(url.endsWith('/replacement-check'))return structuredClone(checks());
    if(method==='GET')return structuredClone(project);
    if(state.hold)await state.hold.promise;
    if(state.fail)throw Error(state.fail);
    const t=project.script.tasks.find(t=>t.id===body?.task_id);
    if(url.endsWith('/dialogue-adopt')){
      t.dialogue_choice='adopt';t.dialogue_proposal.lines=structuredClone(body.lines);
      t.dialogue_lines=body.lines.map(l=>({start:l.start,end:l.end,speaker:l.speaker,text:l.proposed}));
      t.dialogue=t.dialogue_lines.map(l=>l.text).join('\n');
    }else if(url.endsWith('/dialogue-keep')){
      t.dialogue_choice='keep';t.dialogue_lines=t.dialogue_proposal.lines.map(l=>({start:l.start,end:l.end,speaker:l.speaker,text:l.original}));
      t.dialogue=t.dialogue_lines.map(l=>l.text).join('\n');
    }else if(method==='PUT' && url.endsWith('/script'))project.script=structuredClone(body.script);
    else throw Error('Unexpected offline request '+method+' '+url);
    return structuredClone(project);
  };
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={};renderAll()');
  const fire=async(name,el)=>{assert.ok(el,'event target exists');for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
  const click=selector=>fire('click',$(selector));
  const boxes=()=>$$('[data-dialogue-box="seg-01"]');
  const redraw=()=>run('renderScript(true);renderTasks(true);updateDialogueRequestUi();updateScriptSaveUi()');
  async function tick(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);await t.fn();}}
  async function edit(value,index=0,view='#scriptView',event='input'){
    const input=$(`${view} [data-dialogue-line="${index}"]`);input.focus();input.value=value;await fire(event,input);
    assert.equal(document.activeElement,input,'typing preserves focus');return input;
  }
  function assertStatus(adopted,unsaved=false){
    assert.equal(boxes().length,1,'only step 04 renders the proposal');
    assert.equal($('#taskCards [data-dialogue-box]'),null);
    for(const box of boxes()){
      const button=box.querySelector('[data-dialogue-adopt]'),status=box.querySelector('[data-dialogue-adopted]');
      assert.ok(status,'saved status node exists');assert.equal(status.hidden,!adopted);
      if(adopted){assert.equal(status.textContent,adoptedText);assert.equal(status.getAttribute('role'),'status');}
      assert.equal(button.textContent,adopted?'已采用':'采用这版');assert.equal(button.disabled,adopted);
      const hint=box.querySelector('[data-dialogue-unsaved]');assert.ok(hint);assert.equal(hint.hidden,!unsaved);
      if(unsaved){assert.equal(hint.textContent,unsavedText);assert.match(hint.getAttribute('class'),/hint/);}
      for(const input of box.querySelectorAll('[data-dialogue-line]')){
        assert.equal(input.disabled,false);assert.equal(input.hasAttribute('readonly'),false,'adopted table stays editable');
      }
    }
  }
  return {run,$,$$,project,state,requests,click,fire,boxes,redraw,tick,edit,assertStatus};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
const adopt='#scriptView [data-dialogue-adopt="seg-01"]';
test('已采用：04显示绿色状态、禁用按钮，05显示状态行，重绘/轮询不丢失',async()=>{
  const t=setup('adopt');t.assertStatus(true);t.redraw();t.assertStatus(true);
  await t.run('refresh(true)');await t.tick(1000);t.assertStatus(true);
  await t.click(adopt);assert.equal(t.requests.filter(r=>r.url.endsWith('/dialogue-adopt')).length,0);
  const css=fs.readFileSync(path.join(root,'web/style.css'),'utf8');
  assert.match(css,/\[data-dialogue-adopted\][^}]*color:\s*var\(--accent\)/);
});
test('编辑任意一句：04即时撤销已采用、显示灰字；改回逐字一致重新锁定',async()=>{
  const t=setup('adopt');
  for(const [index,view,event] of [[0,'#scriptView','input'],[1,'#scriptView','change']]){
    const saved=t.project.script.tasks[0].dialogue_lines[index].text;
    await t.edit(saved+'!',index,view,event);t.assertStatus(false,true);
    for(const box of t.boxes())assert.equal(box.querySelector(`[data-dialogue-line="${index}"]`).value,saved+'!');
    t.redraw();t.assertStatus(false,true);
    await t.edit(saved,index,view,event);t.assertStatus(true);
  }
  assert.equal(t.requests.filter(r=>r.method!=='GET' && !r.url.endsWith('/replacement-check')).length,0,'local edits make no save/model request');
});
test('不是只看 choice：文本不同、句数不同、缺少逐句台词都不能标成已采用',async()=>{
  for(const change of [
    'P.script.tasks[0].dialogue_lines[1].text="different"',
    'P.script.tasks[0].dialogue_lines.pop()',
    'P.script.tasks[0].dialogue_lines.push({text:"extra"})',
    'delete P.script.tasks[0].dialogue_lines',
  ]){const t=setup('adopt');t.run(change);t.redraw();t.assertStatus(false,true);}
});
test('逐字比较：空格变化也视为待保存；只有文本一致但 choice 未采用仍可采用',async()=>{
  const t=setup('adopt');await t.edit(t.project.script.tasks[0].dialogue_lines[0].text+' ');t.assertStatus(false,true);
  const unadopted=setup();unadopted.run('P.script.tasks[0].dialogue_lines=P.script.tasks[0].dialogue_proposal.lines.map(l=>({text:l.proposed}))');
  unadopted.redraw();unadopted.assertStatus(false);
});
test('空句仍被拦截，但撤销已采用并显示待保存说明',async()=>{
  const t=setup('adopt');await t.edit('');
  for(const box of t.boxes()){
    assert.equal(box.querySelector('[data-dialogue-adopted]').hidden,true);
    assert.equal(box.querySelector('[data-dialogue-unsaved]').hidden,false);
    assert.equal(box.querySelector('[data-dialogue-adopt]').textContent,'采用这版');
    assert.equal(box.querySelector('[data-dialogue-adopt]').disabled,true);
    assert.match(box.querySelector('[data-dialogue-adopt-reason]').textContent,/不能为空/);
  }
});
test('编辑后采用：等待文案保留，成功后按新台词显示已采用，保存周期不重新启用',async()=>{
  const t=setup('adopt');await t.edit('Your look is beautiful.');t.state.hold=deferred();const pending=t.click(adopt);
  assert.match(t.$(adopt).textContent,/正在保存并做安全校对/);assert.equal(t.$(adopt).disabled,true);
  await t.tick(1000);assert.match(t.$(adopt).textContent,/已等待 1 秒/);
  t.state.hold.resolve();await pending;t.assertStatus(true);
  assert.equal(t.project.script.tasks[0].dialogue_lines[0].text,'Your look is beautiful.');
  await t.click('#saveScript');t.assertStatus(true);await t.tick(1000);t.assertStatus(true);
});
for(const view of ['#scriptView'])test(`保存期间在${view}继续编辑：响应及轮询不把新草稿误标为已采用`,async()=>{
  const t=setup('adopt');await t.edit('Submitted B');t.state.hold=deferred();const pending=t.click(adopt);
  await t.edit('Typed C while saving',0,view);
  t.state.hold.resolve();await pending;t.assertStatus(false,true);
  assert.equal(t.project.script.tasks[0].dialogue_lines[0].text,'Submitted B','only the submitted text was saved');
  for(const box of t.boxes())assert.equal(box.querySelector('[data-dialogue-line="0"]').value,'Typed C while saving');
  await t.run('refresh(true)');t.redraw();t.assertStatus(false,true);
  for(const box of t.boxes())assert.equal(box.querySelector('[data-dialogue-line="0"]').value,'Typed C while saving');
  await t.click(adopt);t.assertStatus(true);assert.equal(t.project.script.tasks[0].dialogue_lines[0].text,'Typed C while saving');
});
test('保存结果先被轮询读到：等待期间的新草稿仍然保留',async()=>{
  const t=setup('adopt');await t.edit('Submitted B');t.state.hold=deferred();const pending=t.click(adopt);
  await t.edit('Typed C while saving');
  const saved=t.project.script.tasks[0];saved.dialogue_proposal.lines[0].proposed='Submitted B';
  saved.dialogue_lines[0].text='Submitted B';saved.dialogue=saved.dialogue_lines.map(l=>l.text).join('\n');
  await t.run('refresh(true)');t.state.hold.resolve();await pending;t.assertStatus(false,true);
  for(const box of t.boxes())assert.equal(box.querySelector('[data-dialogue-line="0"]').value,'Typed C while saving');
});
test('采用时服务端更新说话人归属：仍保留等待期间的新台词草稿',async()=>{
  const t=setup('adopt');
  t.run('const transport=api;api=async(...args)=>{const result=await transport(...args);if(args[1].endsWith("/dialogue-adopt")){result.script.tasks[0].dialogue_proposal.lines[0].speaker="C2";result.script.tasks[0].dialogue_lines[0].speaker="C2";}return result;}');
  await t.edit('Submitted B');t.state.hold=deferred();const pending=t.click(adopt);
  await t.edit('Typed C while saving');t.state.hold.resolve();await pending;t.assertStatus(false,true);
  assert.equal(t.run('S.tasks[0].dialogue_lines[0].speaker'),'C2');
  for(const box of t.boxes())assert.equal(box.querySelector('[data-dialogue-line="0"]').value,'Typed C while saving');
});
test('保存失败后保留待保存状态；保存其他字段不能重新启用已采用按钮',async()=>{
  const t=setup('adopt');await t.edit('Your look is beautiful.');t.state.fail='离线模拟保存失败';
  await assert.rejects(t.click(adopt),/离线模拟保存失败/);t.assertStatus(false,true);
  assert.equal(t.$('#toast').className,'toast show err');
  const saved=setup('adopt');saved.state.fail='离线模拟保存失败';
  await assert.rejects(saved.click('#saveScript'),/离线模拟保存失败/);saved.assertStatus(true);
});
test('keep：沿用隐藏方案区，第05步始终说明已保持及台词状态',async()=>{
  const t=setup('keep');assert.equal(t.boxes().length,0);
  const card=t.$('[data-task="seg-01"]');assert.match(card.textContent,/台词：保持原台词.*去第 04 步查看/);
  assert.doesNotMatch(card.textContent,new RegExp(todoText));
  t.run('delete P.replacement_checks');t.redraw();assert.match(t.$('[data-task="seg-01"]').textContent,/台词：保持原台词/);
});
for(const choice of ['adopt','keep'])test(`${choice}成功后自动重新检查，清单与警告均不再有台词待办`,async()=>{
  const t=setup();assert.match(t.$('#taskCards').textContent,new RegExp(todoText));
  await t.click(choice==='adopt' ? adopt : '#scriptView [data-dialogue-keep="seg-01"]');
  await t.tick(1000);
  assert.ok(t.requests.some(r=>r.url.endsWith('/replacement-check')),'save schedules the actual check refresh');
  assert.doesNotMatch(t.$('#taskCards').textContent,new RegExp(todoText));
  assert.equal(t.$('#taskCards [data-todo-target="dialogue-proposal"]'),null);
  assert.equal(t.$('#taskCards .dialogue-conflict'),null);
  if(choice==='adopt')t.assertStatus(true);else assert.match(t.$('#taskCards').textContent,/台词：保持原台词.*去第 04 步查看/);
});
async function main(){let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(error){failed++;console.error('FAIL '+name+'\n'+error.stack);}}assert.equal(failed,0,`${failed}/${tests.length} failed`);console.log(`Round 24.9 UI: ${tests.length} passed`);}
main().catch(error=>{console.error(error);process.exitCode=1;});
