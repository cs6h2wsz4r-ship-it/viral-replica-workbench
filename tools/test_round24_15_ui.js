/* Actual DOM renderers and events; all API responses are synthetic and offline. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
for(const property of ['hidden','disabled'])Object.defineProperty(Node.prototype,property,{get(){return this.hasAttribute(property);},set(v){v?this.setAttribute(property,''):this.removeAttribute(property);},configurable:true});
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8'),page=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
function setup(routes={}){
  const document=createDocument(),events={},requests=[];
  document.body.innerHTML=page.replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const providers={openai:{label:'官方 · GPT-5.6 Sol',model:'gpt-5.6-sol',ready:true},jimmy:{label:'三方渠道 · GPT-5.6 Sol',model:'gpt-5.6-sol',ready:true},deepseek:{label:'三方渠道 · DeepSeek',model:'deepseek-default',ready:true}};
  const status={providers,default_llm:'openai',default_image_channel:'official',openai_image_model:'gpt-image-2',routes:{analysis:{channel:'openai',model:'gpt-5.6-sol'},script:{channel:'openai',model:'gpt-5.6-sol'},check:{channel:'openai',model:'gpt-5.6-sol'},dialogue_proposal:{channel:'openai',model:'gpt-5.6-sol'},camera_prompt:{channel:'openai',model:'gpt-5.6-sol'},camera_image:{channel:'official',model:'gpt-image-2'}}};
  const project={id:'offline-24-15',routes,source:{},analysis:{},assets:[],images:{},breakdown:{characters:[{id:'C1',name:'人物'}],scene:[{id:'S1',name:'客厅',timeRanges:[[0,6]]},{id:'S2',name:'厨房',timeRanges:[[6,12]]}]},replacements:{characters:[{source_id:'C1',action:'replace',description:'已保存的人物描述'}],product:{action:'keep'}},script:{tasks:[{id:'seg-01',kind:'speaker',title:'已保存标题',language:'en',duration:6,dialogue:'Hello.',dialogue_proposal:{lines:[{start:0,end:1,original:'Hello.',proposed:'Hi.',speaker:'C1'}]}}]}};
  const context=vm.createContext({document,console,project,status,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
  run(source.slice(0,source.indexOf('// ---------- events ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  let failDelete=false;
  context.mockApi=async(method,url,body)=>{
    requests.push({method,url,body:structuredClone(body)});
    assert.equal(method,'DELETE','only the explicit restore operation may call the mock API');
    assert.match(url,/^projects\/offline-24-15\/routes\/[a-z_]+$/);
    if(failDelete)throw Error('offline restore failure');
    const updated=structuredClone(project);updated.routes=run('clone(P.routes)');delete updated.routes[url.split('/').at(-1)];
    return updated;
  };
  run('api=mockApi;STATUS=clone(status);P=clone(project);S=clone(P.script);renderAnalysis();renderScript();renderReplacements();renderTasks()');
  const event=async(name,el)=>{assert.ok(el,'event target exists');for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
  return {run,$,$$,event,requests,fail:()=>{failDelete=true;}};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
const scriptMemory={script:{channel:'deepseek',model:'deepseek-flash'}};
const actualScript='三方渠道 · DeepSeek · deepseek-flash';
const firstOption=el=>el.querySelector('option[value=""]').textContent;
function assertDialogue(t,text){const labels=t.$$('[data-dialogue-model]');assert.ok(labels.length,'dialogue model label exists');for(const label of labels)assert.equal(label.textContent,text);}
test('脚本项目记忆如实显示，默认空值和请求不变，整页只有源步骤一个恢复按钮',()=>{
  const t=setup(scriptMemory);
  assert.equal(firstOption(t.$('#scriptProvider')),'这个项目单独用：'+actualScript);
  assert.equal(t.$('#scriptProvider').value,'');assert.equal(t.run('requestRoute("script",$("#scriptProvider")).route_selected'),false);
  assert.equal(t.run('requestRoute("script",$("#scriptProvider")).route.model'),'deepseek-flash');
  assert.equal(t.$$('[data-clear-route]').length,1);assert.equal(t.$('[data-clear-route]').dataset.clearRoute,'script');
  assert.equal(t.$('[data-legacy-route="script"]').textContent,'这个项目之前单独选过这个模型 恢复跟随设置');
  assert.equal(t.$('[data-legacy-route="dialogue_proposal"]').textContent,'');
  assert.equal(t.$('[data-legacy-route="check"]').textContent,`检查模型：${actualScript}（这个项目单独设定，去第 04 步可恢复跟随设置）`);
  assertDialogue(t,`改写模型：${actualScript}（这个项目单独设定，去第 04 步可恢复跟随设置）`);
  for(const step of ['check','dialogue_proposal'])assert.match(t.run(`providerOptions('', '${step}')`),/这个项目单独用：三方渠道 · DeepSeek · deepseek-flash/);
  assert.equal(t.requests.length,0);
});
test('恢复脚本记忆立即同步三处，保留脚本、替换说明和改写草稿',async()=>{
  const t=setup(scriptMemory),title=t.$('#scriptView [data-k="title"]'),description=t.$('#replacementForm [data-cast-description="C1"]');
  title.value='未保存脚本标题';description.value='未保存替换说明';
  t.run('S.tasks[0].title="未保存脚本标题";dirty.script=true;dirty.rep=true');
  const proposal=t.$('#scriptView [data-dialogue-line="0"]');proposal.value='Unsaved proposal.';await t.event('input',proposal);
  await t.event('click',t.$('[data-clear-route="script"]'));
  assert.equal(firstOption(t.$('#scriptProvider')),'跟随设置（官方 · GPT-5.6 Sol）');
  assert.equal(t.$('[data-legacy-route="script"]').textContent,'');assert.equal(t.$$('[data-clear-route]').length,0);
  assert.equal(t.$('[data-legacy-route="check"]').textContent,'检查模型：官方 · GPT-5.6 Sol（跟随设置）');
  assertDialogue(t,'改写模型：官方 · GPT-5.6 Sol（跟随设置里的推理模型）');
  assert.equal(t.$('#scriptView [data-k="title"]').value,'未保存脚本标题');assert.equal(t.run('S.tasks[0].title'),'未保存脚本标题');
  assert.equal(t.$('#replacementForm [data-cast-description="C1"]').value,'未保存替换说明');assert.equal(t.$('#scriptView [data-dialogue-line="0"]').value,'Unsaved proposal.');
  assert.equal(t.run('dirty.script && dirty.rep'),true);assert.equal(t.requests.length,1);assert.equal(t.requests[0].url,'projects/offline-24-15/routes/script');
  for(const step of ['script','check','dialogue_proposal'])assert.equal(t.run(`routeFor('${step}').model`),'gpt-5.6-sol');
});
test('没有项目记忆时，各模型默认跟随设置且无恢复按钮',()=>{
  const t=setup();for(const id of ['analyzeProvider','scriptProvider'])assert.equal(firstOption(t.$('#'+id)),'跟随设置（官方 · GPT-5.6 Sol）');
  for(const sel of t.$$('[data-camera-channel]'))assert.equal(firstOption(sel),'跟随设置（官方 · gpt-image-2）');
  assert.equal(t.$$('[data-clear-route]').length,0);assertDialogue(t,'改写模型：官方 · GPT-5.6 Sol（跟随设置里的推理模型）');
});
test('analysis字符串记忆只在第02步有按钮，提示词跟随说明和恢复立即同步',async()=>{
  const t=setup({analysis:'deepseek:analysis-model'});
  assert.equal(firstOption(t.$('#analyzeProvider')),'这个项目单独用：三方渠道 · DeepSeek · analysis-model');
  assert.equal(t.$$('[data-clear-route]').length,1);assert.equal(t.$('[data-clear-route]').dataset.clearRoute,'analysis');
  assert.equal(t.$('[data-legacy-route="camera_prompt"]').textContent,'提示词模型：三方渠道 · DeepSeek · analysis-model（这个项目单独设定，去第 02 步可恢复跟随设置）');
  assert.equal(firstOption(t.$('#scriptProvider')),'跟随设置（官方 · GPT-5.6 Sol）');
  await t.event('click',t.$('[data-clear-route]'));
  assert.equal(firstOption(t.$('#analyzeProvider')),'跟随设置（官方 · GPT-5.6 Sol）');assert.equal(t.$$('[data-clear-route]').length,0);
  assert.equal(t.$('[data-legacy-route="camera_prompt"]').textContent,'');assert.equal(t.run('routeFor("camera_prompt").model'),'gpt-5.6-sol');
});
test('SB1 没有按场景生图渠道或主图旧记忆提示',()=>{const t=setup();assert.equal(t.$('[data-camera-channel]'),null);assert.equal(t.$('[data-scene-camera]'),null);});
test('临时选择仍只影响一次，消费后显示项目记忆而不改项目',async()=>{
  const t=setup(scriptMemory),sel=t.$('#scriptProvider');sel.value='jimmy';await t.event('change',sel);
  assert.equal(t.run('requestRoute("script",$("#scriptProvider")).provider'),'jimmy');assert.equal(firstOption(sel),'这个项目单独用：'+actualScript);
  assertDialogue(t,'改写模型：三方渠道 · GPT-5.6 Sol（本次临时选择）');
  t.run('finishTemporaryProvider("script",requestRoute("script",$("#scriptProvider")))');assert.equal(sel.value,'');
  assert.equal(firstOption(sel),'这个项目单独用：'+actualScript);assert.equal(t.run('P.routes.script.model'),'deepseek-flash');
  assertDialogue(t,`改写模型：${actualScript}（这个项目单独设定，去第 04 步可恢复跟随设置）`);assert.equal(t.requests.length,0);
});
test('缺Key回退显示resolved_routes实际模型，不把旧记忆模型写入短提示',()=>{
  const t=setup({script:'openai:old-official'});
  t.run('P.resolved_routes={script:{channel:"jimmy",model:"fallback-model"},check:{channel:"jimmy",model:"fallback-model"},dialogue_proposal:{channel:"jimmy",model:"fallback-model"}};renderScript()');
  assert.equal(firstOption(t.$('#scriptProvider')),'这个项目单独用：三方渠道 · fallback-model');
  assert.equal(t.$('[data-legacy-route="script"]').textContent,'这个项目之前单独选过这个模型 恢复跟随设置');
  assert.match(t.$('[data-legacy-route="check"]').textContent,/三方渠道 · fallback-model/);
  assertDialogue(t,'改写模型：三方渠道 · fallback-model（这个项目单独设定，去第 04 步可恢复跟随设置）');
});
test('独立步骤记忆保留自己的入口，清除源步骤不会删除独立路由',async()=>{
  const t=setup({...scriptMemory,check:'deepseek:own-check',dialogue_proposal:'jimmy:own-dialogue'});
  assert.equal(t.$$('[data-clear-route]').length,3);assert.match(t.$('[data-legacy-route="check"]').textContent,/own-check/);
  assertDialogue(t,'改写模型：三方渠道 · own-dialogue（这个项目单独设定）');
  await t.event('click',t.$('[data-clear-route="script"]'));
  assert.equal(t.run('P.routes.check'),'deepseek:own-check');assert.equal(t.run('P.routes.dialogue_proposal'),'jimmy:own-dialogue');assert.equal(t.$$('[data-clear-route]').length,2);
});
test('恢复失败维持原显示和草稿，不误报已跟随设置',async()=>{
  const t=setup(scriptMemory);t.fail();t.$('#scriptView [data-k="title"]').value='失败也保留';
  await assert.rejects(t.event('click',t.$('[data-clear-route]')),/offline restore failure/);
  assert.equal(firstOption(t.$('#scriptProvider')),'这个项目单独用：'+actualScript);assert.equal(t.$$('[data-clear-route]').length,1);assert.equal(t.$('#scriptView [data-k="title"]').value,'失败也保留');
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}}assert.equal(failed,0,`${failed} round24.15 scenarios failed`);console.log(`Round 24.15 UI: ${tests.length} scenarios passed`);})().catch(e=>{console.error(e);process.exitCode=1;});
