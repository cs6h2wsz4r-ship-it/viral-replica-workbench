/* 24.8: real DOM events, deferred offline transport and deterministic timers. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node,installPageConfirmationHarness}=require('./ui_test_dom');
// Reflect the disabled property as native HTML buttons do, rather than two states.
Object.defineProperty(Node.prototype,'disabled',{get(){return this.hasAttribute('disabled');},
  set(value){value ? this.setAttribute('disabled','') : this.removeAttribute('disabled');},configurable:true});
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const conflict='校对期间脚本或替换设定已变化，请刷新后重新保存';
const aligned='刚才有另一次保存先完成了，页面已更新到最新内容，请检查后再操作';
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function setup(){
  const document=createDocument(),events={},requests=[],timers=new Map();let now=0,timerId=0;
  document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const image=(id,roles,extra={})=>({id,type:'image',label:id,file:id+'.png',roles,...extra});
  const proposal={lines:[{start:0,end:2,original:'Your dress is beautiful.',proposed:'Your look is nice.',changed:true,speaker:'C1',reason:'换成新外观'}]};
  const task={id:'seg-01',title:'第一段',kind:'speaker',mode:'video',language:'en',duration:4,ratio:'9:16',source_start:0,source_end:4,
    dialogue:'Your dress is beautiful.',image_ids:['old','person'],dialogue_proposal:proposal};
  const check={errors:[],warnings:[],issues:[{code:'dialogue_conflict',step:'04',title:'台词冲突',details:[]}],dialogue_conflicts:[{title:'台词冲突',details:[]}]};
  const project={id:'round24-8',name:'离线测试',source:{},assets:[image('person',['person_identity']),image('product',['product_truth']),
    ...['old','new'].map(id=>image(id,['target_state'],{camera_character:'C1',camera_scene:'S1',frame_at:1}))],
    breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'客厅',timeRanges:[[0,8]]}],overview:{language:'en'}},
    replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person',camera_assets:{S1:'old'}}],product:{action:'keep'}},
    images:{old:{id:'old',asset_id:'old',state:'completed',for_task:'camera:C1:S1'},new:{id:'new',asset_id:'new',state:'completed',for_task:'camera:C1:S1'}},
    script:{model:'mock',tasks:[task,{...structuredClone(task),id:'seg-02',title:'第二段',source_start:4,source_end:8}]},
    replacement_checks:{'seg-01':structuredClone(check),'seg-02':structuredClone(check)},runs:{},log:[]};
  const state={hold:null,getHold:null,fail:null};
  const context=vm.createContext({document,console,project,structuredClone,confirm:()=>true,
    Date:class extends Date {static now(){return now;}},setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,at:now+ms});return timerId;},clearTimeout:id=>timers.delete(id),
    localStorage:{getItem(){},setItem(){}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
  run(source.slice(0,source.indexOf('// ---------- settings ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  context.mockApi=async(method,url,body)=>{
    const wire=body===undefined ? undefined : structuredClone(body);requests.push({method,url,body:wire});
    if(url.endsWith('/replacement-check'))return structuredClone(project.replacement_checks);
    if(method==='GET'){if(state.getHold)await state.getHold.promise;return structuredClone(project);}
    if(state.hold)await state.hold.promise;
    if(state.fail)throw Error(state.fail);
    const t=project.script.tasks.find(t=>t.id===wire?.task_id);
    if(method==='PUT' && url.endsWith('/script'))project.script=wire.script;
    else if(url.endsWith('/dialogue-adopt')){t.dialogue_choice='adopt';t.dialogue=wire.lines.map(l=>l.proposed).join('\n');t.dialogue_proposal.lines=wire.lines;t.dialogue_lines=wire.lines.map(l=>({text:l.proposed,speaker:l.speaker,start:l.start,end:l.end}));}
    else if(url.endsWith('/dialogue-keep')){t.dialogue_choice='keep';t.dialogue=t.dialogue_proposal.lines.map(l=>l.original).join('\n');}
    else if(url.endsWith('/camera-repair'))t.image_ids[0]=project.replacements.characters[0].camera_assets.S1;
    else if(url.endsWith('/camera-image/use')){
      project.replacements.characters[0].camera_assets.S1=wire.asset_id;
      project.script.tasks.forEach(t=>{if(t.image_ids[0]==='old')t.image_ids[0]=wire.asset_id;});
      project.camera_selection={message:'已改用新图，图1 已同步'};
    }else if(url.endsWith('/resume')){project.images.old.removed=false;}
    else if(method==='DELETE')project.assets=project.assets.filter(a=>!url.endsWith('/'+a.id));
    else if(!url.endsWith('/preview'))throw Error('Unexpected request '+method+' '+url);
    return structuredClone(project);
  };
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={default_image_channel:"official",providers:{}};renderAll()');
  const fire=async(name,el)=>{assert.ok(el,'event target exists');for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
  installPageConfirmationHarness({run,context,document,dispatch:el=>fire('click',el)});
  const click=selector=>fire('click',typeof selector==='string'?$(selector):selector);
  const writes=()=>requests.filter(r=>r.method!=='GET' && !r.url.endsWith('/replacement-check'));
  const redraw=()=>run('renderReplacements(true);renderScript(true);renderTasks(true)');
  async function tick(ms){now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);await t.fn();}}
  function busy(selector,seconds=0){
    const b=$(selector);assert.ok(b);assert.ok(b.hasAttribute('disabled'),'trigger disabled immediately');
    assert.equal(b.textContent,`正在保存并做安全校对…（已等待 ${seconds} 秒）`);
    for(const other of $$('#saveScript, [data-dialogue-adopt], [data-dialogue-keep], [data-dialogue-propose], [data-camera-use], [data-fix-image1], [data-fix-camera], [data-preview]')){
      assert.ok(other.hasAttribute('disabled'),'other script writer disabled: '+other.outerHTML);
    }
  }
  return {run,$,$$,project,state,requests,writes,click,fire,tick,redraw,busy};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
const adopt='#scriptView [data-dialogue-adopt="seg-01"]';
test('采用：立即反馈、全项目互斥、计时、重绘/轮询、成功提示与恢复',async()=>{
  const t=setup();t.state.hold=deferred();const pending=t.click(adopt);t.busy(adopt);assert.equal(t.writes().length,1);
  await t.click(adopt);await t.click('#saveScript');await t.click('[data-dialogue-keep="seg-02"]');
  await t.run('adoptDialogueProposal("seg-01",document.querySelector("#scriptView [data-dialogue-box]"))');
  assert.equal(t.writes().length,1,'duplicate DOM and direct invocations do not submit');
  await t.tick(2000);t.busy(adopt,2);t.redraw();t.busy(adopt,2);
  await t.run('refresh(true)');t.busy(adopt,2);await t.tick(1000);t.busy(adopt,3);
  t.state.hold.resolve();await pending;
  assert.equal(t.$(adopt).hasAttribute('disabled'),true);assert.equal(t.$(adopt).textContent,'已采用');
  assert.equal(t.$('[data-dialogue-adopt="seg-02"]').hasAttribute('disabled'),false);
  assert.match(t.$('#toast').textContent,/已采用改写后的台词/);assert.equal(t.run('S.tasks[0].dialogue_choice'),'adopt');
  await t.tick(4000);assert.equal(t.$('#toast').className,'toast','success is temporary');
});
for(const [name,selector,prepare] of [
  ['保持原台词','#scriptView [data-dialogue-keep="seg-01"]',()=>{}],
  ['保存脚本','#saveScript',t=>{t.$('[data-seg-key="seg-01"] [data-k="title"]').value='手改标题';t.run('dirty.script=true');}],
  ['把人物放图1','[data-fix-image1="seg-01"]',t=>t.run('P.script.tasks[0].image_ids=["product","person"];P.replacement_checks["seg-01"].issues=[{code:"image1_not_person",step:"05"}];renderScript(true)')],
  ['修复失效主镜头图','[data-fix-camera="seg-01"]',t=>t.run('P.replacements.characters[0].camera_assets.S1="new";renderScript(true)')],
  ['换成当前主镜头图','[data-fix-camera="seg-01"]',t=>t.run('P.script.tasks[0].image_ids=["product","person"];P.replacement_checks["seg-01"].issues=[{code:"missing_camera_image",expected_asset_id:"old",character_id:"C1",scene_id:"S1"}];renderScript(true)')],
  ['生成预览的自动保存','[data-preview="seg-01"]',()=>{}],
  ['删除主镜头图的同步','[data-delasset="old"]',()=>{}],
])test(name+'：互斥/计时/重复点击/恢复',async()=>{
  const t=setup();prepare(t);t.state.hold=deferred();const pending=t.click(selector);if(selector.includes('data-delasset'))await Promise.resolve();t.busy(selector);
  await t.click(selector);await t.click(adopt);assert.equal(t.writes().length,1);
  await t.tick(1000);t.busy(selector,1);t.redraw();t.busy(selector,1);
  t.state.hold.resolve();await pending;
  assert.equal(t.$('#saveScript').hasAttribute('disabled'),false);
  assert.doesNotMatch(t.$('#saveScript').textContent,/正在保存/);
});
test('采用前自动保存也持同一把锁，编辑触发的渲染不能重新启用',async()=>{
  const t=setup();t.run('dirty.script=true');t.state.hold=deferred();const pending=t.click(adopt);t.busy(adopt);
  assert.equal(t.writes()[0].method,'PUT');await t.click(adopt);assert.equal(t.writes().length,1);
  t.$('[data-dialogue-line="0"]').value='Your look is nice.';await t.fire('input',t.$('[data-dialogue-line="0"]'));t.busy(adopt);
  t.state.hold.resolve();await pending;
  assert.equal(t.writes().filter(r=>r.url.endsWith('/dialogue-adopt')).length,1);assert.equal(t.writes().length,2);
});
for(const message of [conflict,'刚才有另一次保存先完成了，请刷新到最新内容，检查后再操作']){
  test('冲突已采用：拉取、核对提交的手改台词、已保存且无红错 '+message,async()=>{
    const t=setup(),line=t.$('#scriptView [data-dialogue-line="0"]');line.value='Your new style is nice.';await t.fire('input',line);
    t.state.hold=deferred();t.state.getHold=deferred();const pending=t.click(adopt);t.busy(adopt);
    const submitted=t.writes()[0].body.lines;t.project.script.tasks[0].dialogue_choice='adopt';t.project.script.tasks[0].dialogue=submitted.map(l=>l.proposed).join('\n');
    t.project.script.tasks[0].dialogue_proposal.lines=submitted;t.project.script.tasks[0].dialogue_lines=submitted.map(l=>({text:l.proposed}));t.state.hold.reject(Error(message));
    for(let i=0;i<12;i++)await Promise.resolve();t.busy(adopt);assert.ok(t.requests.some(r=>r.method==='GET'));
    t.state.getHold.resolve();await pending;
    assert.equal(t.$('#toast').textContent,'已保存');assert.doesNotMatch(t.$('#toast').className,/err/);
    assert.equal(t.$('[data-dialogue-error]'),null);assert.equal(t.run('S.tasks[0].dialogue'),t.project.script.tasks[0].dialogue);
    assert.equal(t.$(adopt).hasAttribute('disabled'),true);assert.equal(t.$(adopt).textContent,'已采用');
  });
}
for(const choice of ['keep','adopt'])test('冲突数据不一致：'+choice+'，刷新并显示直白提示',async()=>{
  const t=setup();t.state.hold=deferred();const pending=t.click(adopt);
  t.project.script.tasks[0].dialogue_choice=choice;t.project.script.tasks[0].dialogue='Someone else saved different words.';
  t.project.script.tasks[0].title='另一次保存的标题';t.project.replacements.characters[0].camera_assets.S1='new';
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,aligned);assert.doesNotMatch(t.$('#toast').className,/err/);
  assert.equal(t.run('dirty.script'),false);assert.equal(t.run('S.tasks[0].title'),'另一次保存的标题');
  assert.equal(t.$('[data-seg-key="seg-01"] [data-k="title"]').value,'另一次保存的标题');
  assert.equal(t.$('.camera-pair'),null);assert.equal(t.$('[data-dialogue-error]'),null);
  assert.ok(t.$('[data-replacement-check]'));assert.ok(t.requests.some(r=>r.method==='GET'));
});
for(const [name,selector,prepare,applyLatest] of [
  ['保持原台词','#scriptView [data-dialogue-keep="seg-01"]',()=>{},t=>{const task=t.project.script.tasks[0];task.dialogue_choice='keep';task.dialogue=task.dialogue_proposal.lines[0].original;}],
  ['保存脚本','#saveScript',t=>{t.$('[data-seg-key="seg-01"] [data-k="title"]').value='手改标题';t.run('dirty.script=true');},t=>{
    t.project.script=structuredClone(t.writes()[0].body.script);t.project.script.tasks.forEach(task=>{task.blank_captions=true;});
  }],
  ['人物放图1','[data-fix-image1="seg-01"]',t=>t.run('P.script.tasks[0].image_ids=["product","person"];P.replacement_checks["seg-01"].issues=[{code:"image1_not_person",step:"05"}];renderScript(true)'),t=>{t.project.script=structuredClone(t.writes()[0].body.script);}],
  ['修复主镜头图','[data-fix-camera="seg-01"]',t=>t.run('P.replacements.characters[0].camera_assets.S1="new";renderScript(true)'),t=>{t.project.replacements.characters[0].camera_assets.S1='new';t.project.script.tasks[0].image_ids[0]='new';}],
])for(const same of [true,false])test(name+'冲突自动对齐：'+(same?'目标已完成':'目标不一致'),async()=>{
  const t=setup();prepare(t);t.state.hold=deferred();const pending=t.click(selector);if(selector.includes('data-delasset'))await Promise.resolve();t.busy(selector);
  if(same)applyLatest(t);else t.project.script.tasks[0].title='其他用户的版本';
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,same?'已保存':aligned);assert.doesNotMatch(t.$('#toast').className,/err/);
  assert.ok(t.requests.some(r=>r.method==='GET'));assert.equal(t.$('#saveScript').hasAttribute('disabled'),false);
});
test('冲突对齐清除过期的改写错误与草稿',async()=>{
  const t=setup();t.run('operationErrors.set(operationKey("dialogue","seg-01"),"上一次改写失败");renderScript(true)');
  t.state.hold=deferred();const pending=t.click(adopt);
  t.project.script.tasks[0].dialogue_choice='adopt';t.project.script.tasks[0].dialogue='Your look is nice.';
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,'已保存');assert.equal(t.$('[data-dialogue-error]'),null);
});
test('冲突刷新覆盖仍有焦点的旧编辑框，重新检查使用最新脚本',async()=>{
  const t=setup(),title=t.$('[data-seg-key="seg-01"] [data-k="title"]');
  title.value='未完成的旧草稿';title.focus();t.run('dirty.script=true');
  t.state.hold=deferred();const pending=t.click('#saveScript');
  t.project.script.tasks[0].title='另一次保存的最新标题';t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,aligned);
  assert.equal(t.$('[data-seg-key="seg-01"] [data-k="title"]').value,'另一次保存的最新标题');
  assert.equal(t.run('S.tasks[0].title'),'另一次保存的最新标题');
});
test('冲突刷新不能由相同的缓存 HTML 留下旧草稿，同时显示最新替换设定',async()=>{
  const t=setup(),title=t.$('[data-seg-key="seg-01"] [data-k="title"]');title.value='旧草稿';t.run('dirty.script=true');
  const description=t.$('[data-cast-description="C1"]');description.value='未保存说明';await t.fire('input',description);
  t.state.hold=deferred();const pending=t.click('#saveScript');
  t.project.replacements.characters[0].description='服务器最新人物说明';t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('[data-seg-key="seg-01"] [data-k="title"]').value,'第一段');
  assert.equal(t.$('[data-cast-description="C1"]').value,'服务器最新人物说明');assert.equal(t.run('dirty.rep'),false);
  assert.equal(t.run('S.tasks[0].title'),'第一段');
});
for(const same of [false,true])test('修复图1冲突还要核对同次提交的标题与引用文字：'+same,async()=>{
  const t=setup();t.run('P.script.tasks[0].image_ids=["product","person"];P.replacement_checks["seg-01"].issues=[{code:"image1_not_person",step:"05"}];renderScript(true)');
  t.$('[data-seg-key="seg-01"] [data-k="title"]').value='同次提交的新标题';
  t.$('[data-seg-key="seg-01"] [data-k="action"]').value='Smile at @image2 next to @image1.';t.run('dirty.script=true');
  t.state.hold=deferred();const pending=t.click('[data-fix-image1="seg-01"]');
  if(same){t.project.script=structuredClone(t.writes()[0].body.script);t.project.script.tasks[0].action='Smile at @image1 next to @image2.';}
  else t.project.script.tasks[0].image_ids=['person','product'];
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,same?'已保存':aligned);
});
for(const same of [false,true])test('采用前自动保存冲突核对同次提交的标题，允许采用改变台词：'+same,async()=>{
  const t=setup();t.$('[data-seg-key="seg-01"] [data-k="title"]').value='新标题';t.run('dirty.script=true');
  t.state.hold=deferred();const pending=t.click(adopt);
  if(same)t.project.script=structuredClone(t.writes()[0].body.script);
  t.project.script.dialogue_mode='rewrite';t.project.script.tasks[0].dialogue_choice='adopt';t.project.script.tasks[0].dialogue='Your look is nice.';
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,same?'已保存':aligned);
  assert.equal(t.writes().length,1,'no follow-up adoption after conflicting pre-save');
});
for(const same of [false,true])test('修复主镜头图不能遗漏同次提交的图2修改：'+same,async()=>{
  const t=setup();t.run('P.replacements.characters[0].camera_assets.S1="new";renderScript(true);S.tasks[0].image_ids=["old","product"];dirty.script=true;renderTasks(true)');
  t.state.hold=deferred();const pending=t.click('[data-fix-camera="seg-01"]');
  assert.deepEqual(t.writes()[0].body.script.tasks[0].image_ids,['old','product']);
  t.project.script=structuredClone(t.writes()[0].body.script);t.project.replacements.characters[0].camera_assets.S1='new';
  t.project.script.tasks[0].image_ids=['new',same?'product':'person'];
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,same?'已保存':aligned);assert.equal(t.writes().length,1);
});
for(const keep of [false,true])for(const same of [false,true])test('采用/保持核对服务端台词锚点同步且拒绝额外文字变化：'+keep+'/'+same,async()=>{
  const t=setup(),before='Smile while saying "Your dress is beautiful."',after='Smile while saying "Your look is nice."';
  t.project.script.tasks[0].action=keep?after:before;
  t.run('P=clone(project);renderScript(true)');t.$('[data-seg-key="seg-01"] [data-k="title"]').value='同次修改标题';t.run('dirty.script=true');
  t.state.hold=deferred();const pending=t.click(keep?'#scriptView [data-dialogue-keep="seg-01"]':adopt);
  t.project.script=structuredClone(t.writes()[0].body.script);const task=t.project.script.tasks[0];
  task.dialogue_choice=keep?'keep':'adopt';task.dialogue=keep?'Your dress is beautiful.':'Your look is nice.';
  t.project.script.dialogue_mode=keep?'keep':'rewrite';
  task.dialogue_anchor_mapping={active:!keep,fields:{action:{before,after}}};task.action=(keep?before:after)+(same?'':' Then jump.');
  t.state.hold.reject(Error(conflict));await pending;
  assert.equal(t.$('#toast').textContent,same?'已保存':aligned);
});
test('普通失败仍显示错误且解锁，可重新保存',async()=>{
  const t=setup();t.state.hold=deferred();const pending=t.click(adopt);t.busy(adopt);
  t.state.hold.reject(Error('安全校对服务暂时不可用'));await assert.rejects(pending,/安全校对服务/);
  assert.match(t.$('#toast').textContent,/安全校对服务/);assert.match(t.$('#toast').className,/err/);
  assert.equal(t.$(adopt).hasAttribute('disabled'),false);t.state.hold=null;await t.click(adopt);assert.equal(t.writes().length,2);
});
test('保存结束恢复当前的校验状态，不能启用等待期间被编辑为空的采用按钮',async()=>{
  const t=setup();t.state.hold=deferred();const pending=t.click(adopt);
  const input=t.$('#scriptView [data-dialogue-line="0"]');input.value='';await t.fire('input',input);t.busy(adopt);
  t.state.hold.reject(Error('临时失败'));await assert.rejects(pending,/临时失败/);
  assert.ok(t.$(adopt).hasAttribute('disabled'),'empty edited proposal remains disabled after save unlock');
  assert.match(t.$('#scriptView [data-dialogue-adopt-reason]').textContent,/不能为空/);
  assert.equal(t.$('#saveScript').hasAttribute('disabled'),false);
});
test('保存结束仍尊重轮询更新的生成任务禁用状态',async()=>{
  const t=setup();t.state.hold=deferred();const pending=t.click(adopt);
  t.run('P.jobs={script:{state:"running"}};renderScript()');t.busy(adopt);
  t.state.hold.reject(Error('临时失败'));await assert.rejects(pending,/临时失败/);
  assert.equal(t.$('#scriptBtn').textContent,'生成中…');assert.equal(t.$('#scriptBtn').disabled,true);
});
test('项目切换隔离：旧项目的结果不覆盖新项目，切回仍有等待状态',async()=>{
  const t=setup();t.state.hold=deferred();const pending=t.click(adopt);t.busy(adopt);
  t.run('P={...clone(project),id:"other"};renderAll()');assert.equal(t.$(adopt).hasAttribute('disabled'),false);
  await t.tick(1000);assert.equal(t.$(adopt).textContent,'采用这版');
  t.run('P=clone(project);renderAll()');t.busy(adopt,1);
  t.run('P={...clone(project),id:"other"};renderAll()');t.state.hold.resolve();await pending;
  assert.equal(t.run('P.id'),'other');assert.equal(t.run('S.tasks[0].dialogue_choice'),undefined);
  assert.doesNotMatch(t.$('#toast').textContent,/已采用/);
});
async function main(){let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(error){failed++;console.error('FAIL '+name+'\n'+error.stack);}}assert.equal(failed,0,`${failed}/${tests.length} failed`);console.log(`Round 24.8 UI: ${tests.length} passed`);}
main().catch(error=>{console.error(error);process.exitCode=1;});
