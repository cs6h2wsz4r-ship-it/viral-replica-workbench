/* V3 real DOM renderers/events, synthetic state and mocked HTTP only. */
const assert=require('node:assert/strict');
const {setup}=require('./v2_ui_fixture');
const {seed}=require('./test_v2_ui');
function fixture(){
 const t=setup();seed(t);
 t.run(`P.director.format_version=3;P.director.segments.forEach(s=>{s.gen_images=[s.cells[0],...s.refs];s.images=[...s.gen_images];s.prompt='图1 锁定目标画面；图2 锁定人物。'+(s.mode==='video'?'视频1 提供动作。':'');delete s.brief;});
 P.director_status.segments=P.director.segments.map(s=>({id:s.id,generation_materials:[{key:s.cells[0],type:'image',label:'图1',description:'第 1 格的新画面（替换后的状态）',file:s.cells[0]+'-new.jpg'},{key:'C1',type:'image',label:'图2',description:'PERSON C1 的人物照',file:'person.jpg'},...(s.mode==='video'?[{key:'source_video',type:'video',label:'视频1',description:'原片 0–5.4 秒，均匀拉伸到 6 秒',file:'source.mp4',source_start:0,source_end:5.4}]:[])]}));directorDrafts.clear();renderSteps()`);
 return t;
}
async function main(){
 const t=fixture(),card=t.$('[data-director-card="d01"]');
 assert.equal(t.$('[data-segment-generate]').textContent,'重新生成分段方案');
 assert.equal(t.$$('[data-gen-images]').length,0);
 assert.ok(card,'V3 director cards must render');
 const materials=card.querySelector('[data-director-materials]');assert.ok(materials);
 assert.equal(materials.querySelectorAll('img').length,2);assert.equal(materials.querySelectorAll('video').length,1);
 assert.match(materials.textContent,/图1.*第 1 格的新画面.*图2.*PERSON C1 的人物照.*视频1.*均匀拉伸/s);
 assert.match(materials.querySelector('img').src,/c01-new\.jpg/);assert.doesNotMatch(materials.innerHTML,/storyboard\.jpg/);
 t.$('[data-detail-key="director-prompt:d01"]').open=true;
 const prompt=t.$('[data-director-prompt="d01"]');assert.ok(prompt);assert.match(prompt.value,/图1.*图2.*视频1/);
 assert.equal(t.$$('[data-director-field]').length,0);
 prompt.value='图1 新画面，图2 人物照，视频1 动作。😀';await t.event('input',prompt);
 assert.match(t.$('[data-director-count="d01"]').textContent,new RegExp(Array.from(prompt.value).length+' / 5000'));
 assert.match(t.$('[data-director-card="d01"]').textContent,/这一段的提示词你改过；重新生成会覆盖你的修改。/);
 t.context.mockApi=async(method,url,body)=>{t.requests.push({method,url,body:structuredClone(body)});const p=JSON.parse(t.run('JSON.stringify(P)'));if(body.action==='save')p.director=structuredClone(body.director);if(body.action==='rewrite'){p.director.segments[0].prompt='图1 新画面；图2 人物照；视频1 动作按验收修正。';p.director.segments[0].edited=false;}return p;};t.run('api=mockApi');
 await t.click(t.$('[data-director-save]'));
 assert.equal(t.requests.at(-1).body.director.segments[0].prompt,prompt.value);assert.equal(t.requests.at(-1).body.director.segments[0].edited,true);
 t.run(`P.runs.d01=[{rev:'r1',state:'completed',result:'result.mp4',review:{status:'completed',verdict:'fix',summary:'需要处理',issues:[{start:0,end:1,level:'high',what:'目标外观不一致',fix:'image_mode',how:'回第 04 步调整'},{start:1,end:2,level:'high',what:'目标画面不完整',fix:'redraw_cell',cell:'c01',how:'回第 05 步重画'}]}}];renderSteps()`);
 const result=t.$('[data-review-card]');
 assert.match(result.textContent,/目标外观不一致.*目标画面不完整/s);
 assert.equal(t.$('[data-director-review-rewrite]'),null,'old rewrite entry is replaced by a problem-scoped plan');
 const plan=result.querySelector('[data-fix-plan]');assert.ok(plan);assert.equal(plan.textContent,'针对勾选的 2 个问题出修改方案');
 const before=t.requests.length;await t.click(plan);
 assert.equal(t.requests.length,before+1);assert.deepEqual(t.requests.at(-1),{method:'POST',url:'projects/sb1-offline/runs/d01/r1/fix-plan',body:{issues:[0,1],note:''}});
 assert.ok(t.requests.every(r=>!r.url.includes('/confirm')&&!r.url.includes('/preview')));
 t.run(`P.runs.d01[0].review={status:'completed',verdict:'pass',issues:[]};renderSteps()`);assert.equal(t.$('[data-director-review-rewrite]'),null);
 // A persisted running review after restart must allow an explicit manual check.
 t.run(`P.jobs={};P.runs.d01[0].review={status:'running'};renderSteps()`);
 const interrupted=t.$('[data-review-card]');
 assert.match(interrupted.textContent,/没检查完.*上次检查中断.*检查视频/);
 assert.equal(interrupted.querySelector('[data-director-review]').disabled,false);
 t.context.mockApi=async(method,url,body)=>{t.requests.push({method,url,body});const p=JSON.parse(t.run('JSON.stringify(P)'));p.jobs={'review:d01:r1':{state:'running'}};return p;};t.run('api=mockApi');
 await t.click(interrupted.querySelector('[data-director-review]'));
 assert.match(t.requests.at(-1).url,/runs\/d01\/r1\/review$/);
 assert.equal(t.$('[data-review-card] [data-director-review]').disabled,true);
 console.log('PASS V3 UI: ordered material thumbnails, full prompt edit/count, review rewrite warning and no automatic paid submission');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});module.exports={main};
