/* SB3 real renderers/events, synthetic director state and mocked HTTP only. */
const assert=require('node:assert/strict');
const {setup}=require('./test_storyboard_sb1_ui');
const {seed}=require('./test_v2_ui');

async function main(){
 const t=setup();
 assert.ok(t.$('#directorView'),'selected segment prompt view exists');
 assert.match(t.$('#directorView').textContent,/先完成上面的新分镜/);
 assert.equal(t.$('[data-comparison-batch="prompts"]'),null);
 assert.match(t.$('#directorGeneration').textContent,/先完成上面的提示词/);
 seed(t);Object.assign(t.project,JSON.parse(t.run('JSON.stringify(P)')));t.run('P.director=null;directorDrafts.clear();renderSteps()');
 assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 const seg=(id,cells,mode)=>({id,title:id==='d01'?'门廊摔倒':'室内救援',cells,mode,mode_reason:'动作重要',what:'老人摔倒后由儿子救援',check:['手表在左手'],timeline:[],images:['c01','C1','product'],gen_images:['c01','C1','product'],source_start:id==='d01'?0:3,source_end:id==='d01'?3:10,source_seconds:id==='d01'?3:7,duration:id==='d01'?4:7,target_seconds:id==='d01'?4:7,prompt:'Create a realistic vertical 9:16 phone-shot video. @image1 @image2 @image3 '+(mode==='video'?'@video1':''),warnings:['说明较短'],needs_rewrite:false,edited:false});
 const plan={format_version:3,summary:'分两段，先事故再救援',split_reason:'室外切到室内',skip_cells:[],questions:['背景音乐不确定'],segments:[seg('d01',['c01','c02','c03'],'video'),seg('d02',['c04','c05','c06','c07','c08'],'image')]};
 const base=t.context.mockApi;let previewIndex=0;
 t.context.mockApi=async(method,url,body)=>{
  if(!url.includes('/director')&&!url.includes('/tasks/d0'))return base(method,url,body);
  t.requests.push({method,url,body:structuredClone(body)});
  if(url.endsWith('/director')){
   if(body.action==='generate')t.project.director=structuredClone(plan);
   else if(body.action==='save')t.project.director=structuredClone(body.director);
   else {const s=t.project.director.segments.find(s=>s.id===body.segment_id);
    if(body.action==='mode'){s.mode=body.mode;s.needs_rewrite=true;}
    if(body.action==='images'){s.images=body.images;s.needs_rewrite=true;}
    if(body.action==='rewrite'){s.prompt='Rewritten English director brief.';s.needs_rewrite=false;}
    if(body.action==='select-run')s.selected_run=body.rev;
   }
   return structuredClone(t.project);
  }
  if(url.endsWith('/preview')){
   const r={rev:'r'+(++previewIndex),state:'pending_confirmation',kind:'director',model:'mock',billing_seconds:8,billing_note:'输出 4s + 参考视频 4s；单价以三方渠道账户为准',job_sha256:'mock-sha',clip:'clips/mock.mp4',preview:{tasks:[{prompt:plan.segments[0].prompt,duration:4}]},references:[{key:'c01',tag:'图1',label:'图1',description:'第 1 格的新画面（替换后的状态）',file:'c01-new.jpg'},{key:'C1',tag:'图2',label:'图2',description:'THE FATHER 的人物照',file:'dad.jpg'}]};
   t.project.runs.d01=[r];return structuredClone(r);
  }
  return {ok:true};
 };
 t.run('api=mockApi');
 await t.click(t.$('[data-director-preview="d01"]'));
 const generated=t.requests.find(r=>r.body?.action==='rewrite');assert.equal(generated.method,'PUT');assert.deepEqual(generated.body,{action:'rewrite',segment_id:'d01'});
 assert.ok(t.requests.every(r=>r.body?.action!=='generate'),'first segment uses scoped initialization instead of whole-plan generation');
 assert.ok(t.requests.at(-1).url.endsWith('/preview'));await t.click(t.$('[data-v5-cancel]'));
 assert.equal(t.$$('[data-director-card]').length,1);
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c04"]'));assert.ok(t.$('[data-director-card="d02"]'));
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));assert.ok(t.$('[data-director-card="d01"]'));
 assert.match(t.$('#directorView').textContent,/分两段|背景音乐不确定|拍什么：/);
 assert.match(t.$('#selectedSegmentHeader').textContent,/第 1 段 · 门廊跌倒/);assert.match(t.$('#segmentBar').textContent,/原片 0–5.4 秒/);assert.equal(t.run('P.runs.d01[0].preview.tasks[0].duration'),4);
 assert.equal(t.$$('[data-director-merge]').length,0);assert.equal(t.$$('[data-director-split]').length,0);
 assert.match(t.$('#directorView').textContent,/完整提示词|重新写这一段/);
 await t.click(t.$('[data-director-zoom]'));assert.equal(t.$('#cameraLightbox').hidden,false);
 t.run('P.director.format_version=2;directorDrafts.clear();renderSteps()');
 let cancelShown=0;t.context.confirm=message=>{cancelShown++;assert.match(message,/覆盖现在的方案和你的修改/);return false;};const requests=t.requests.length;await t.run("directorAction('generate')");assert.equal(t.requests.length,requests);assert.equal(cancelShown,1);assert.equal(t.$('#pageConfirmation').hidden,true);t.context.confirm=()=>true;
 t.run('P.director.format_version=3;directorDrafts.clear();renderSteps()');
 t.$('[data-detail-key="director-prompt:d01"]').open=true;
 const brief=t.$('[data-director-prompt="d01"]');brief.value='I edited the full English brief.';await t.event('input',brief);
 assert.match(t.$('[data-director-count="d01"]').textContent,/32 \/ 5000/);
 t.run('renderSteps()');assert.equal(t.$('[data-director-prompt="d01"]').value,brief.value,'poll render keeps draft');
 await t.click(t.$('[data-director-save]'));assert.equal(t.requests.at(-1).method,'PUT');assert.equal(t.requests.at(-1).body.action,'save');assert.equal(t.requests.at(-1).body.director.segments[0].edited,true);
 assert.equal(t.$('[data-director-mode]'),null);assert.equal(t.$('[data-director-ref]'),null);
 await t.click(t.$('[data-director-rewrite="d01"]'));assert.equal(t.requests.at(-1).body.action,'rewrite');assert.equal(t.$('[data-director-preview="d01"]').disabled,false);
 await t.click(t.$('[data-director-preview="d01"]'));assert.match(t.requests.at(-1).url,/tasks\/d01\/preview$/);
 assert.match(t.$('#segmentAdvanced').textContent,/完整导演说明|完整提示词|THE FATHER/);assert.match(t.$('#segmentBar').textContent,/计费约 8 秒/);
 assert.ok(t.$('#segmentAdvanced video'));assert.match(t.$('#segmentAdvanced').textContent,/Create a realistic/);
 await t.click(t.$('[data-v5-confirm="d01"]'));assert.match(t.requests.filter(r=>r.url.includes('/confirm')).at(-1).url,/tasks\/d01\/confirm$/);
 t.context.project.director=structuredClone(t.project.director);
 t.run(`P.director_status={ready:true,stale:true,segments:[{id:'d01',stale:true}]};P.runs.d01=[{rev:'r1',state:'completed',kind:'director',result:'one.mp4',director_stale:true},{rev:'r2',state:'completed',kind:'director',result:'two.mp4'},{rev:'r3',state:'running',kind:'director',remote_task_id:'remote',query_retry:{attempts:1,next_at:Date.now()/1000+30,started_at:Date.now()/1000,exhausted:false}}];comparisonViews.clear();v5Actions.clear();renderSteps()`);
 assert.doesNotMatch(t.$('#directorView').textContent,/上游内容改过/);assert.equal(t.run('comparisonNeedsPrompt(P.segments_plan.segments[0])'),true);
 assert.match(t.$('#directorGeneration').textContent,/正在生成视频/);assert.ok(t.$('[data-director-resume="d01"]'));
 // Historical version warnings and controls return once this segment's prompt is valid.
 t.run('P.director_status={ready:true};renderSteps()');
 assert.match(t.$('#directorGeneration').textContent,/导演方案改过，这一版是按旧说明生成的|三方渠道暂时连不上，正在自动重新查询/);
 assert.equal(t.$$('[data-compare-version]').length,3);assert.equal(t.$$('.director-version-bar').length,1);
 await t.click(t.$('[data-compare-version="r2"]'));assert.ok(t.$('[data-director-use][data-rev="r2"]'));
 await t.click(t.$('[data-compare-version="r1"]'));
 await t.click(t.$('[data-director-use="d01"][data-rev="r1"]'));assert.equal(t.requests.at(-1).body.action,'select-run');assert.equal(t.project.director.segments[0].selected_run,'r1');
 t.run(`directorDrafts.clear();P.director_status={ready:false};P.storyboard_edit_status={s01:{source_stale:true}};renderSteps()`);assert.equal(t.$('[data-comparison-batch="prompts"]'),null);
 // Error stays on the page with an explicit retry; stale responses cannot replace another project.
 t.run(`P.director_status={ready:true};P.storyboard_edit_status={};P.director.format_version=2;directorDrafts.clear();renderSteps()`);
 t.context.mockApi=async()=>{throw Error('导演方案不完整：漏格');};t.run('api=mockApi');await t.click(t.$('[data-director-preview="d01"]'));assert.match(t.$('#segmentAction').textContent,/导演方案不完整：漏格|重试/);
 t.run('P.director.format_version=3;directorDrafts.clear();renderSteps()');
 t.context.mockApi=async()=>{throw Error('本地预览打包失败');};t.run('api=mockApi');await t.click(t.$('[data-director-preview="d01"]'));
 assert.match(t.$('#segmentAction').textContent,/本地预览打包失败/);assert.ok(t.$('[data-director-preview="d01"]'),'primary action retries preparation');
 console.log('PASS SB3: director prerequisites/cards/edit/save/mode/references/rewrite/removed-merge-split/zoom; free preview/confirm/status/versions/stale/retry');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
