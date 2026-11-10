/* 24.20: actual DOM/render/events; self-made task and mock API only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
require('./ui_test_dom').Node.prototype.scrollIntoView=function(){};
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_17_ui.js'),'utf8');
const base=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('const tests=[];'))+'\nreturn setup;')(require,__dirname);
const tests=[],test=(name,fn)=>tests.push([name,fn]);
function setup(){
 const t=base();
 t.run(`S={tasks:[{id:'seg-01',kind:'speaker',mode:'video',dialogue:'Hello son.',action:'A calm expression.',source_start:9,source_end:12.3,duration:4,image_ids:['dad'],ratio:'9:16'}]};P.script=clone(S);
 P.replacement_checks={'seg-01':{errors:[],issues:[{code:'motion_additions',step:'04',severity:'warning',details:['时机不一致；原句：A calm expression.']}],warnings:[],action_comparison:{status:'completed',model:'mock'},motion_warning:{fingerprint:'same-script-task',details:['时机不一致；原句：A calm expression.'],dismissed:false}}};
 P.runs={'seg-01':[{rev:'r1',state:'pending_confirmation',job_sha256:'mock-sha',billing_seconds:4,preview:{tasks:[{duration:4,ratio:'9:16',images:[],reference_videos:[],prompt:'mock'}]}}]};
 globalThis.warningCalls=[];const oldApi=api;api=async(method,url,body)=>{if(url.endsWith('/dismiss-motion-warning')){warningCalls.push(body);return {ok:true};}if(url.endsWith('/resume')){warningCalls.push({url,body});return {ok:true};}return oldApi(method,url,body);};renderScript(true);renderTasks(true);`);
 return t;
}
test('动作不一致可预览、可确认，黄色提醒逐条显示原因原句和两按钮',()=>{
 const t=setup();assert.equal(t.$('[data-preview]').disabled,false);assert.equal(t.$('[data-confirm]').disabled,false);
 assert.equal(t.$('.fix-wizard'),null);const box=t.$('[data-motion-warning]');assert.ok(box);assert.match(box.getAttribute('class'),/caption-warning/);
 assert.match(box.textContent,/动作对比提醒：这一段的表演说明里有 1 处和原片时机不一致，生成的视频可能和原片动作对不上。/);
 assert.match(box.textContent,/时机不一致/);assert.match(box.querySelector('details').textContent,/A calm expression/);
 assert.equal(t.$('[data-motion-edit]').textContent,'改这句');assert.equal(t.$('[data-motion-dismiss]').textContent,'不用改，继续');
});
test('改这句定位第04步本段表演方向并聚焦',async()=>{
 const t=setup();await t.click(t.$('[data-motion-edit]'));assert.equal(t.$('[data-seg-key="seg-01"] [data-k="action"]'),t.$('[data-seg-key="seg-01"] [data-k="action"]').closest('body').querySelector('.fix-highlight'));
 assert.equal(t.run('document.activeElement.dataset.k'),'action');
});
test('不用改继续只关闭本段提醒，保存指纹；改段后重新出现',async()=>{
 const t=setup();await t.click(t.$('[data-motion-dismiss]'));assert.equal(t.$('[data-motion-warning]'),null);
 assert.equal(t.run('warningCalls.length'),1);assert.equal(t.run('warningCalls[0].fingerprint'),'same-script-task');
 t.run('renderTasks(true)');assert.equal(t.$('[data-motion-warning]'),null);
 t.run(`S.tasks[0].action='A changed expression.';renderTasks(true)`);assert.ok(t.$('[data-motion-warning]'));
});
test('其他拦截类问题依旧禁用预览',()=>{
 const t=setup();t.run(`P.replacement_checks['seg-01'].errors=['参考图缺失'];P.replacement_checks['seg-01'].issues.push({code:'invalid_image',step:'05',details:['参考图缺失']});renderTasks(true)`);
 assert.equal(t.$('[data-preview]').disabled,true);assert.ok(t.$('.fix-wizard'));
});
test('查询期间文案、非失败样式；超时显示再查一次，同号恢复入口',async()=>{
 const t=setup();t.run(`P.runs['seg-01']=[{rev:'r2',state:'running',remote_status:'failed',remote_task_id:'same-remote',query_retry:{attempts:3}}];P.jobs={'gen:seg-01:r2':{state:'error',error:'旧查询错误'}};renderRuns(S.tasks[0])`);
 const live=t.$('[data-live]');assert.match(live.textContent,/生成中 · 三方渠道暂时连不上，正在自动重新查询（已重试 3 次）/);
 assert.doesNotMatch(live.textContent,/上次执行出错|失败/);assert.equal(t.$('.state.failed'),null);
 t.run(`P.runs['seg-01'][0].state='query_pending';P.runs['seg-01'][0].query_retry.exhausted=true;renderRuns(S.tasks[0])`);
 assert.match(live.textContent,/三方渠道一直查不到结果，任务可能还在生成/);assert.equal(t.$('[data-resume]').textContent,'再查一次');
 await t.click(t.$('[data-resume]'));assert.equal(t.run('warningCalls.at(-1).body.rev'),'r2');
});
test('图片查询失败也显示自动重查或再查一次',()=>{
 const t=setup();t.run(`P.images={img:{id:'img',for_task:'seg-01',state:'query_pending',query_retry:{attempts:4,exhausted:true}}};renderRuns(S.tasks[0])`);
 assert.match(t.$('[data-image-key="img"]').textContent,/三方渠道一直查不到结果，任务可能还在生成/);
 assert.equal(t.$('[data-imgresume="img"]').textContent,'再查一次');
});
(async()=>{for(const [name,fn]of tests){await fn();console.log('PASS',name);}console.log('24.20 UI:',tests.length,'passed');})().catch(e=>{console.error(e);process.exitCode=1;});
