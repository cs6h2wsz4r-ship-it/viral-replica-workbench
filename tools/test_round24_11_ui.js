/* 24.11: exercise actual delegated DOM events, without a server or network. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(),events={};
Object.defineProperty(Node.prototype,'outerHTML',{configurable:true,set(html){const tmp=document.createElement('template');tmp.innerHTML=html;this.replaceWith(tmp.firstChild);}});
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
const context=vm.createContext({document,console,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},fetch(){throw Error('禁止网络');}});
const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s);
run(source.slice(0,source.indexOf('// ---------- settings ----------')));
const start=source.indexOf('document.addEventListener("click", async (e) => {');
run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
run(`P={id:'offline24-11',assets:[],images:{},runs:{},breakdown:{},replacement_checks:{},script:{tasks:[]}};
 S={tasks:[{id:'seg-01',kind:'speaker',mode:'video',source_start:0,source_end:10,duration:10,ratio:'9:16',image_ids:[],
 dialogue_lines:[{start:5.201,end:6.56,text:"It's knit and comfortable."}]}]};P.script=clone(S);renderTasks(true)`);
async function event(name,target,extra={}){for(const fn of events[name] || [])await fn({target,preventDefault(){},...extra});}
function checkData(){run(`applyReplacementChecks({'seg-01':{errors:[],issues:[],warnings:['旧遮挡条带提示','旧去字幕残留提示'],masks:[{bbox:[0,.3,.5,.9],start:1,end:2,motion_warning:'这段字幕很大，下半身动作的参考会变弱'}]}})`);}
function setRun(check){context.check=check;run(`P.runs['seg-01']=[{rev:'r2',state:'completed',mode:'video',result:'result.mp4',motion_check:check}];renderRuns(S.tasks[0])`);}
(async()=>{
 assert.equal($('[data-m="blank_captions"]').checked,false,'missing flag must be unchecked');
 assert.match($('[data-task]').textContent,/去掉参考片段里的原片字幕/);
 assert.match($('[data-task]').textContent,/提示词已要求新视频不出现字幕。只有生成的视频里出现了原片字幕时，再勾选这里重新生成/);
 checkData();assert.equal($('[data-caption-warning]').textContent,'');
 assert.doesNotMatch($('[data-task]').textContent,/旧遮挡条带|旧去字幕残留/,'stale replacement checks cannot show mask hints while unchecked');
 const input=$('[data-m="blank_captions"]');input.checked=true;await event('change',input);
 assert.match($('[data-caption-warning]').textContent,/1\.00–2\.00 秒/);
 run('renderTasks(true)');assert.equal($('[data-m="blank_captions"]').checked,true,'explicit true survives redraw');
 $('[data-m="blank_captions"]').checked=false;await event('change',$('[data-m="blank_captions"]'));
 checkData();assert.equal($('[data-caption-warning]').textContent,'');
 run(`S.tasks[0].blank_captions=true;P.runs['seg-01']=[{rev:'image-preview',state:'pending_confirmation',job_sha256:'offline',
 preview:{tasks:[{images:[],reference_videos:[],prompt:'offline'}]}}];renderRuns(S.tasks[0]);S.tasks[0].blank_captions=false`);
 run(`P.runs['seg-01']=[{rev:'r1',state:'pending_confirmation',job_sha256:'offline',
 clip_processing:{blank_captions:false,muted:true,masks:[{bbox:[0,.3,.5,.9],start:1,end:2,motion_warning:'下半身动作参考变弱'}]},
 caption_residue:{status:'checked',baseline_interval:[1,2],suspected:[1],warnings:['旧去字幕残留警告']},
 preview:{tasks:[{images:[],reference_videos:[],prompt:'offline'}]}}];renderRuns(S.tasks[0])`);
 assert.doesNotMatch($('[data-live]').textContent,/字幕区域处理|字幕处理区域|下半身|残留|无字幕基准|复制进成片/);
 assert.equal($('[data-confirm]').hasAttribute('disabled'),false,'checks cannot block paid confirmation');
 for(const boundary of [-2,2,-1.9,1.9]){
   const check={status:'misaligned',motion_lag_seconds:boundary,motion_correlation:.7,sample_fps:10,
     speech_lags:[{text:"It's so flowy and comfortable.",source_start:5.201,lag_seconds:-1.03}],
     warnings:[`成片运动节奏比原片${boundary<0?'提前':'延后'}约 ${Math.abs(boundary).toFixed(2)} 秒`,"成片未可靠匹配台词：It's so flowy and comfortable.",
       "“It's so flowy and comfortable.” 提前 1.03 秒"]};
   setRun(check);const before=JSON.stringify(check);
   assert.equal($('[data-live]').querySelector('.caption-warning'),null,'no conclusion panel in finished card');
   assert.doesNotMatch($('[data-live]').textContent,/动作对齐检查|有偏差|无法判断|flowy/);
   const button=$('[data-motion-check]');assert.equal(button.textContent,'查看自动检查（仅供参考）');
   await event('click',button);const modal=$('#motionCheckModal');assert.equal(modal.hidden,false);
   assert.match(modal.textContent,/这是程序按画面变化和语音时间自动算的参考，不代表视频好不好。请以你自己看视频的感受为准。/);
   assert.match(modal.textContent,/画面变化曲线在 ±2 秒内没有找到可靠的对应位置[\s\S]*这一项不作判断/);
   assert.match(modal.textContent,/无法判断/);assert.match(modal.textContent,/实际台词[\s\S]*knit/);
   assert.doesNotMatch(modal.textContent,/整体运动节奏提前|整体运动节奏延后|成片未可靠匹配台词：/);
   assert.ok(!modal.textContent.includes("“It's so flowy and comfortable.” 提前 1.03 秒"),'invalidated original-text matches must also invalidate their timing warning');
   assert.match(modal.textContent,/判断标准/);assert.match(modal.textContent,/相关系数 ≥ 0.45[\s\S]*偏移 ≤ 0.5 秒/);
   assert.match(modal.textContent,/本地语音识别[\s\S]*差 ≤ 0.5 秒/);
   assert.equal(JSON.stringify(check),before,'legacy reinterpretation never mutates the report');
   await event('keydown',modal,{key:'Escape'});assert.equal(modal.hidden,true);
   await event('click',button);await event('click',modal);assert.equal(modal.hidden,true);
   await event('click',button);await event('click',modal.querySelector('[data-motion-close]'));assert.equal(modal.hidden,true);
 }
 setRun({status:'aligned',speech_reference:'dialogue_lines',motion_status:'aligned',motion_lag_seconds:-.3,motion_correlation:.8,
   speech_lags:[{text:"It's knit and comfortable.",lag_seconds:-.1}],warnings:[]});
 await event('click',$('[data-motion-check]'));assert.match($('#motionCheckModal').textContent,/对齐[\s\S]*提前 0.30 秒/);
 await event('click',$('#motionCheckModal').querySelector('.motion-check-content'));assert.equal($('#motionCheckModal').hidden,false,'inside click stays open');
 await event('click',$('[data-motion-close]'));
 setRun({status:'running'});assert.equal($('[data-motion-check]').textContent,'自动检查中…');
 await event('click',$('[data-motion-check]'));assert.match($('#motionCheckModal').textContent,/自动检查中/);
 setRun({status:'misaligned',speech_reference:'dialogue_lines',motion_lag_seconds:-2,motion_correlation:.7,
   speech_lags:[{text:'Oh, thank you so much.',lag_seconds:-.622}],warnings:['“Oh, thank you so much.” 提前 0.62 秒']});
 await event('click',$('[data-motion-check]'));assert.match($('#motionCheckModal').textContent,/有偏差[\s\S]*0.62 秒/);
 assert.equal($('[data-choose]').hasAttribute('disabled'),false,'advisory never blocks selection');
 const frozen=JSON.parse(fs.readFileSync(path.join(root,'tools/fixtures/round24_11/r2-motion-report.json'),'utf8'));
 context.frozen=frozen;
 run(`S.tasks[0]=clone(frozen.task);P.runs['seg-01']=[{...clone(frozen.run),state:'completed',result:'result.mp4'}];renderTasks(true)`);
 const saved=run('JSON.stringify(P.runs)');await event('click',$('[data-motion-check]'));
 assert.match($('#motionCheckModal').textContent,/结论：有偏差/);
 assert.match($('#motionCheckModal').textContent,/Oh, thank you so much.[\s\S]*提前 0.62 秒/);
 assert.match($('#motionCheckModal').textContent,/实际台词为“It.s knit and comfortable.”；这一句无法判断/);
 assert.match($('#motionCheckModal').textContent,/实际台词为“I love the skirt.”；这一句无法判断/);
 assert.doesNotMatch($('#motionCheckModal').textContent,/成片未可靠匹配台词：|整体运动节奏提前 2/);
 assert.equal(run('JSON.stringify(P.runs)'),saved,'actual cached r2 remains unchanged');
 const guide=run('JSON.stringify(workflowState())');
 run(`P.runs['seg-01'][0].motion_check={status:'aligned'}`);
 assert.equal(run('JSON.stringify(workflowState())'),guide,'automatic check never changes wizard decisions');
 console.log('PASS: 24.11 caption defaults, opt-in warnings, advisory button/modal/standards/close paths, legacy reports and nonblocking controls');
})().catch(error=>{console.error(error);process.exitCode=1;});
