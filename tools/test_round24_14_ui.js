/* Real renderers and events, mock API only. Never starts a server or reads configuration. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
for(const property of ['hidden','disabled'])Object.defineProperty(Node.prototype,property,{get(){return this.hasAttribute(property);},set(v){v?this.setAttribute(property,''):this.removeAttribute(property);},configurable:true});
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8'),page=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
function setup(overrides={}){
  const document=createDocument(),events={},requests=[];
  document.body.innerHTML=page.replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const providers={openai:{label:'官方 · GPT-5.6 Sol',model:'gpt-5.6-sol',ready:true},jimmy:{label:'三方渠道 · GPT-5.6 Sol',model:'gpt-5.6-sol',ready:true},deepseek:{label:'三方渠道 · DeepSeek',model:'deepseek-test',ready:false},doubao:{label:'三方渠道 · 豆包',model:'doubao-test',ready:false},custom:{label:'三方渠道 · 自定义',model:'custom-test',ready:false}};
  const settings={_providers:providers,OPENAI_API_KEY:'已填写 ···test',JIMMY_API_KEY:'已填写 ···test',_jimmy_configured:true,DEFAULT_LLM:'jimmy',
    OPENAI_MODEL:'gpt-5.6-sol',OPENAI_IMAGE_MODEL:'gpt-image-2',JIMMY_LLM_MODEL:'gpt-5.6-sol',DEEPSEEK_MODEL:'deepseek-test',DOUBAO_MODEL:'doubao-test',CUSTOM_MODEL:'custom-test',ASR_MODEL:'medium',
    ROUTE_ANALYSIS:'openai:gpt-5.6-sol',ROUTE_SCRIPT:'openai:gpt-5.6-sol',ROUTE_CHECK:'follow:script',ROUTE_DIALOGUE_PROPOSAL:'follow:script',ROUTE_CAMERA_PROMPT:'follow:analysis',ROUTE_CAMERA_IMAGE:'official:gpt-image-2',ROUTE_VIDEO:'third_party:seedance2.0-mini-video',...overrides};
  const routes={analysis:{channel:'openai',model:'gpt-5.6-sol'},script:{channel:'openai',model:'gpt-5.6-sol'},camera_image:{channel:'official',model:'gpt-image-2'}};
  const project={id:'offline-24-14',source:{},analysis:{},assets:[],images:{},breakdown:{characters:[],scene:[]},script:{tasks:[]}};
  const status={providers,routes,default_llm:'openai',runtime:{root:'/offline/workbench',runtime:'/offline/runtime',projects:'/offline/projects',config:'/offline/settings',runtime_mb:12}};
  const context=vm.createContext({document,console,settings,status,project,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},
    FormData:class{constructor(form){this.form=form;}entries(){return this.form.querySelectorAll('[name]').filter(e=>!e.disabled).map(e=>[e.getAttribute('name'),e.value]);}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
  $('#settingsDialog').showModal=()=>{};$('#settingsDialog').close=()=>{};
  run(source.slice(0,source.indexOf('// ---------- events ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  let modelResponse={models:[{name:'语音识别（faster-whisper medium）',status:'ok',path:'local/model'},{name:'中文对齐模型',status:'ok'},{name:'英文对齐模型',status:'ok'},{name:'FFmpeg',status:'ok'}]},hold;
  context.mockApi=async(method,url,body)=>{requests.push({method,url,body:structuredClone(body)});
    if(url==='settings/local-models'){if(hold)await hold;return structuredClone(modelResponse);}
    if(url==='settings/test-connection')return {status:'成功',elapsed_ms:3};
    if(url==='settings')return structuredClone(settings);
    if(url==='status')return structuredClone(status);
    if(method==='DELETE'){Object.assign(project,run('clone(P)'));delete project.routes?.[url.split('/').at(-1)];project.resolved_routes={};return structuredClone(project);}
    if(method==='GET')return structuredClone(project);
    return {};
  };
  run('api=mockApi;STATUS=clone(status);P=clone(project);S=clone(P.script)');
  const event=async(name,el)=>{assert.ok(el,'event target exists');for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
  return {run,$,$$,event,requests,settings,project,open:()=>run('openSettings()'),models:value=>{modelResponse=value;},hold:value=>{hold=value;}};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
test('三张卡片、准确说明、高级默认折叠、旧配置字段完整',async()=>{
  const t=setup();await t.open();assert.equal(t.$$('[data-model-card]').length,3);
  assert.match(t.$('#settingsFields').textContent,/推理模型.*拆解、写脚本、改写台词、安全检查都用它.*生图模型.*生成新分镜图，包括按格改图和批量裂变.*生视频模型.*生成分段视频/s);
  assert.match(t.$('#settingsForm').textContent,/Key 只保存在这台电脑上，不会写进项目文件。/);
  const advanced=t.$('#settingsAdvanced');assert.equal(advanced.open,false);assert.equal(advanced.querySelector('summary').textContent,'高级设置（一般不用动）');
  assert.match(advanced.textContent,/单独给某一步换模型.*不设置时，各步骤跟随上面三张卡片/s);
  const oldFields=['DEFAULT_LLM','JIMMY_LLM_MODEL','JIMMY_LLM_BASE_URL','OPENAI_BASE_URL','OPENAI_MODEL','OPENAI_IMAGE_MODEL','OPENAI_REASONING_EFFORT','DOUBAO_BASE_URL','DOUBAO_MODEL','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL','CUSTOM_BASE_URL','CUSTOM_MODEL','ASR_MODEL','ASR_COMPUTE_TYPE','ASR_LANGUAGE','ASR_ENGINE','HF_ENDPOINT','MAX_PASSAGES','LLM_PARALLEL',...['ANALYSIS','SCRIPT','CHECK','DIALOGUE_PROPOSAL','CAMERA_PROMPT','CAMERA_IMAGE','VIDEO'].map(s=>'ROUTE_'+s)];
  for(const key of oldFields)assert.ok(advanced.querySelector(`[name="${key}"]`),key);
  for(const channel of ['openai','jimmy','deepseek','doubao','custom'])assert.equal(t.$$(`[data-channel-key="${channel}"]`).length,1);
  assert.doesNotMatch(t.$('#settingsForm').textContent,/WhisperX 对齐模型|三方渠道沿用已有的本机配置|项目显式路由/);
});
test('卡片选择更新推理两路、跟随三路与DEFAULT_LLM；生图视频配置分开',async()=>{
  const t=setup();await t.open();const reason=t.$('[data-model-card="reason"]');reason.value='deepseek';await t.event('change',reason);
  await t.run('saveSettings({preventDefault(){}})');const body=t.requests.find(r=>r.method==='POST'&&r.url==='settings').body;
  assert.equal(body.ROUTE_ANALYSIS,'deepseek');assert.equal(body.ROUTE_SCRIPT,'deepseek');assert.equal(body.DEFAULT_LLM,'deepseek');
  assert.equal(body.ROUTE_CHECK,'follow:script');assert.equal(body.ROUTE_DIALOGUE_PROPOSAL,'follow:script');assert.equal(body.ROUTE_CAMERA_PROMPT,'follow:analysis');
  const image=t.$('[data-model-card="image"]');image.value='third_party:gpt-image-2|auto';await t.event('change',image);
  const video=t.$('[data-model-card="video"]');assert.equal(video.querySelectorAll('option').length,1);assert.match(video.textContent,/Seedance 2.0 mini/);
  await t.event('change',video);await t.run('saveSettings({preventDefault(){}})');const last=t.requests.filter(r=>r.method==='POST'&&r.url==='settings').at(-1).body;
  assert.equal(last.ROUTE_CAMERA_IMAGE,'third_party:gpt-image-2');assert.equal(last.ROUTE_STORYBOARD_IMAGE,'third_party:gpt-image-2');assert.equal(last.CAMERA_IMAGE_QUALITY,'auto');assert.equal(last.STORYBOARD_IMAGE_QUALITY,'auto');assert.equal(last.ROUTE_VIDEO,'third_party');
});
test('打开保存不改已有高级路由；主卡改选才恢复跟随',async()=>{
  const t=setup({ROUTE_SCRIPT:'deepseek:legacy-global',ROUTE_CHECK:'doubao:independent'});await t.open();await t.run('saveSettings({preventDefault(){}})');
  let body=t.requests.find(r=>r.method==='POST').body;assert.equal(body.ROUTE_SCRIPT,'deepseek:legacy-global');assert.equal(body.ROUTE_CHECK,'doubao:independent');
  const reason=t.$('[data-model-card="reason"]');reason.value='jimmy';await t.event('change',reason);
  assert.equal(t.$('[name="ROUTE_CHECK"]').value,'follow:script');
});
test('已填Key默认无输入，更换才出现；共享只填一次，留空原样提交',async()=>{
  const t=setup();await t.open();assert.equal(t.$('[name="OPENAI_API_KEY"]'),null);
  assert.match(t.$('[data-card-key="reason"]').textContent,/✓ 官方 OpenAI Key 已填/);
  assert.match(t.$('[data-card-key="image"]').textContent,/✓ 和上面用的是同一个 官方 OpenAI Key，不用再填/);
  await t.event('click',t.$('[data-change-key="openai"]'));const input=t.$('[name="OPENAI_API_KEY"]');assert.ok(input);assert.equal(input.value,'');assert.equal(input.type,'password');
  await t.event('click',t.$('[data-test-channel="openai"]'));assert.equal(t.requests.at(-1).url,'settings/test-connection');assert.match(t.$('[data-connection-result="openai"]').textContent,/成功/);
  await t.run('saveSettings({preventDefault(){}})');assert.equal(t.requests.find(r=>r.method==='POST'&&r.url==='settings').body.OPENAI_API_KEY,'');
});
test('未填Key选项仍可选且就地输入；三方三卡共用，切换保留未保存Key',async()=>{
  const t=setup({OPENAI_API_KEY:'',JIMMY_API_KEY:'',_jimmy_configured:false});await t.open();
  assert.match(t.$('[data-model-card="reason"]').textContent,/官方 · GPT-5.6 Sol（未填 Key）/);
  assert.ok(t.$('[data-card-key="reason"] [name="OPENAI_API_KEY"]'));const reason=t.$('[data-model-card="reason"]');reason.value='jimmy';await t.event('change',reason);
  assert.ok(t.$('[data-card-key="reason"] [name="JIMMY_API_KEY"]'));
  const image=t.$('[data-model-card="image"]');image.value='third_party:gpt-image-2|auto';await t.event('change',image);
  assert.match(t.$('[data-card-key="image"]').textContent,/同一个 三方渠道 Key/);assert.match(t.$('[data-card-key="video"]').textContent,/同一个 三方渠道 Key/);
  assert.equal(t.$$('[name="JIMMY_API_KEY"]').length,1);t.$('[name="JIMMY_API_KEY"]').value='offline-draft';
  reason.value='deepseek';await t.event('change',reason);assert.equal(t.$('[name="JIMMY_API_KEY"]').value,'offline-draft');assert.ok(t.$('[data-card-key="reason"] [name="DEEPSEEK_API_KEY"]'));
});
test('安装位置内联展开收起；本地检查loading、绿红灯、详情默认折叠',async()=>{
  const t=setup();await t.open();assert.equal(t.$('#installationInfo').hidden,true);
  await t.event('click',t.$('[data-installation-toggle]'));assert.equal(t.$('#installationInfo').hidden,false);assert.match(t.$('#installationInfo').textContent,/删除整个工作台文件夹即可完全卸载/);
  await t.event('click',t.$('[data-installation-toggle]'));assert.equal(t.$('#installationInfo').hidden,true);
  let release;t.hold(new Promise(r=>release=r));const pending=t.event('click',t.$('[data-local-models]'));assert.equal(t.$('[data-local-models]').textContent,'检查中…');release();await pending;
  assert.equal(t.$$('[data-local-model-state="ok"]').length,4);assert.match(t.$('#localModelsInfo').textContent,/语音识别（faster-whisper medium）.*已安装/s);
  assert.equal(t.$('#localModelsInfo details').open,false);assert.ok(t.$('#localModelsInfo details').textContent.includes('local/model'));
  await t.event('click',t.$('[data-local-models]'));assert.equal(t.$('#localModelsInfo').hidden,true);
  t.models({models:[{name:'语音识别（faster-whisper medium）',status:'missing'},{name:'英文对齐模型',status:'broken',detail:'文件损坏'}]});await t.event('click',t.$('[data-local-models]'));
  assert.equal(t.$$('[data-local-model-state="missing"], [data-local-model-state="broken"]').length,2);assert.match(t.$('#localModelsInfo').textContent,/缺失.*不可用：文件损坏/s);
});
test('页面跟随设置默认项，旧项目提示与清除接口仅删目标步骤',async()=>{
  const t=setup();t.run('P.routes={script:"deepseek:old-model"};renderAnalysis();renderScript()');
  assert.equal(t.$('#analyzeProvider').value,'');assert.equal(t.$('#scriptProvider').value,'');assert.match(t.$('#scriptProvider').textContent,/这个项目单独用：三方渠道 · DeepSeek · old-model/);
  assert.equal(t.$('[data-legacy-route="script"]').textContent,'这个项目之前单独选过这个模型 恢复跟随设置');
  const sel=t.$('#scriptProvider');sel.value='jimmy';await t.event('change',sel);assert.equal(t.run('requestRoute("script",$("#scriptProvider")).provider'),'jimmy');
  assert.equal(t.run('P.routes.script'),'deepseek:old-model');
  await t.event('click',t.$('[data-clear-route="script"]'));assert.equal(t.requests.find(r=>r.method==='DELETE').url,'projects/offline-24-14/routes/script');
  assert.equal(t.$('[data-legacy-route="script"]').textContent,'');assert.equal(t.$('#scriptProvider').value,'');
});
test('生图跟随设置与旧记忆提示；台词改写标签如实标注来源',()=>{
  const t=setup();t.run('P.routes={camera_image:"third_party:legacy-image"};P.breakdown={characters:[{id:"C1"}],scene:[{id:"S1",name:"客厅"}]};P.replacements={characters:[{source_id:"C1",action:"replace"}],product:{action:"keep"}};renderReplacements()');
  assert.equal(t.$('[data-camera-channel]'),null);
  assert.match(t.run('dialogueModelLabel()'),/改写模型：官方 · GPT-5.6 Sol（跟随设置里的推理模型）/);
  t.run('P.routes.script="deepseek:old-model"');assert.match(t.run('dialogueModelLabel()'),/old-model.*这个项目单独设定，去第 04 步可恢复跟随设置/);
});
test('设置保存和清除生图旧记忆保留替换草稿，跟随渠道不被自动缓存锁住',async()=>{
  const t=setup();t.run('P.breakdown={characters:[{id:"C1",name:"人物"}],scene:[{id:"S1",name:"客厅"}]};P.replacements={characters:[{source_id:"C1",action:"replace"}],product:{action:"keep"}};renderReplacements()');
  const input=t.$('#replacementForm [data-cast-description="C1"]');assert.ok(input);input.value='未保存的新人物描述';t.run('captureCasting();cameraChannels.set(cameraDraftKey("C1","S1"),"official");STATUS.routes.camera_image={channel:"third_party",model:"gpt-image-2"}');
  assert.equal(t.run('imageChannel("C1","S1")'),'third_party');
  await t.open();await t.run('saveSettings({preventDefault(){}})');assert.equal(t.$('#replacementForm [data-cast-description="C1"]').value,'未保存的新人物描述');
  t.run('P.routes={camera_image:"third_party:old-image"};renderLegacyRoutes()');
  await t.event('click',t.$('[data-clear-route="camera_image"]'));assert.equal(t.$('#replacementForm [data-cast-description="C1"]').value,'未保存的新人物描述');
});
test('历史模型标签不混入当前渠道默认模型，状态缺字段也能渲染',()=>{
  const t=setup();t.run('STATUS.providers.deepseek={label:"三方渠道 · DeepSeek · configured-model",model:"configured-model",ready:true}');
  assert.equal(t.run('routeName({channel:"deepseek",model:"old-model"})'),'三方渠道 · DeepSeek · old-model');
  assert.equal(t.run('routeName({})').includes('未配置'),true);
});
test('高级模型字段修改更新卡片和实际默认路由，独立模型仍保留',async()=>{
  const t=setup({ROUTE_CHECK:'openai:independent-check'});await t.open();
  const model=t.$('[name="OPENAI_MODEL"]');model.value='new-official-model';await t.event('change',model);
  const image=t.$('[name="OPENAI_IMAGE_MODEL"]');image.value='new-image-model';await t.event('change',image);
  assert.match(t.$('[data-model-card="reason"]').textContent,/官方 · new-official-model/);
  assert.doesNotMatch(t.$('[data-model-card="reason"]').textContent,/官方 · GPT-5.6 Sol/);
  assert.match(t.$('[data-model-card="image"]').textContent,/官方 · new-image-model/);
  await t.run('saveSettings({preventDefault(){}})');const body=t.requests.find(r=>r.method==='POST'&&r.url==='settings').body;
  assert.equal(body.ROUTE_ANALYSIS,'openai');assert.equal(body.ROUTE_SCRIPT,'openai');assert.equal(body.ROUTE_CAMERA_IMAGE,'official:new-image-model');assert.equal(body.ROUTE_CHECK,'openai:independent-check');
});
test('独立台词、提示词、检查和视频旧记忆均有对应入口，清除后立即更新',async()=>{
  const t=setup();t.run('P.routes={dialogue_proposal:"deepseek:old-dialogue",camera_prompt:"jimmy:old-prompt",check:"deepseek:old-check",video:"third_party:old-video"};renderAnalysis();renderScript();renderTasks()');
  for(const step of ['dialogue_proposal','camera_prompt','check','video'])assert.ok(t.$(`[data-clear-route="${step}"]`),step);
  assert.match(t.$('[data-legacy-route="dialogue_proposal"]').textContent,/old-dialogue/);
  await t.event('click',t.$('[data-clear-route="dialogue_proposal"]'));assert.equal(t.$('[data-legacy-route="dialogue_proposal"]').textContent,'');
  assert.ok(t.$('[data-clear-route="camera_prompt"]'));
});
test('临时推理选择成功后只消费一次，后续提示词与改写重新跟随设置',async()=>{
  const t=setup();t.run('renderAnalysis();renderScript()');
  const analysis=t.$('#analyzeProvider');analysis.value='deepseek';await t.event('change',analysis);
  await t.run('submitAnalyze()');assert.equal(t.requests.find(r=>r.url.endsWith('/analyze')).body.provider,'deepseek');assert.equal(analysis.value,'');
  assert.equal(t.run('requestRoute("camera_prompt",$("#analyzeProvider")).provider'),'openai');
  const script=t.$('#scriptProvider');script.value='jimmy';await t.event('change',script);
  await t.run('submitScript()');assert.equal(t.requests.find(r=>r.url.endsWith('/script')).body.provider,'jimmy');assert.equal(script.value,'');
  assert.equal(t.run('requestRoute("dialogue_proposal",$("#scriptProvider")).provider'),'openai');
  assert.equal(t.run('!!P.routes'),false);
});
test('旧记忆短提示不重复模型，缺Key回退如实显示在默认项和改写标签',()=>{
  const t=setup();t.run('P.routes={script:"openai:old-official"};P.resolved_routes={script:{channel:"jimmy",model:"fallback-model"},dialogue_proposal:{channel:"jimmy",model:"fallback-model"}};renderScript()');
  assert.equal(t.$('[data-legacy-route="script"]').textContent,'这个项目之前单独选过这个模型 恢复跟随设置');
  assert.match(t.$('#scriptProvider').textContent,/这个项目单独用：三方渠道 · fallback-model/);
  assert.equal(t.run('requestRoute("script",$("#scriptProvider")).route.model'),'fallback-model');
  assert.match(t.run('dialogueModelLabel()'),/三方渠道 · fallback-model.*这个项目单独设定，去第 04 步可恢复跟随设置/);
});
test('SB1 不再展示第03步主图生图渠道、状态和恢复按钮',()=>{const t=setup();assert.equal(t.$('[data-camera-channel]'),null);assert.equal(t.$('.scene-camera'),null);});
test('临时改走官方生图使用官方配置模型，提示词内容原样提交',async()=>{
  const t=setup();t.run('STATUS.openai_image_model="configured-official-image";STATUS.routes.camera_image={channel:"third_party",model:"gpt-image-2"};cameraChannels.set(cameraDraftKey("C1","S1"),"official");cameraRouteSelected.add(cameraDraftKey("C1","S1"))');
  await t.run('previewSceneCamera("C1","S1",{draft:{prompt:"unchanged offline prompt",model:"gpt-image-2",image_ids:[],for_task:"camera:C1:S1"}})');
  const body=t.requests.find(r=>r.url.endsWith('/images/preview')).body;assert.equal(body.model,'configured-official-image');assert.equal(body.prompt,'unchanged offline prompt');
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}}assert.equal(failed,0,`${failed} round24.14 scenarios failed`);console.log(`Round 24.14 UI: ${tests.length} scenarios passed`);})().catch(e=>{console.error(e);process.exitCode=1;});
