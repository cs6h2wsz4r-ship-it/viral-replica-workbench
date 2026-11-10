/* 24.10: caption explanations, per-row warnings, live refresh and opt-out. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const description='提示词已要求新视频不出现字幕。只有生成的视频里出现了原片字幕时，再勾选这里重新生成';
const warning='这段字幕很大（覆盖画面高度 36–84%），这段时间里下半身动作的参考会变弱';
function setup(){
  const document=createDocument(),events={};
  // The shared minimal DOM lacks the browser's outerHTML replacement API.
  Object.defineProperty(Node.prototype,'outerHTML',{configurable:true,set(html){
    const template=document.createElement('template');template.innerHTML=html;
    this.replaceWith(template.content.firstChild);
  }});
  document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
  document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
  const context=vm.createContext({document,console,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
  const run=code=>vm.runInContext(code,context),$=s=>document.querySelector(s);
  run(source.slice(0,source.indexOf('// ---------- settings ----------')));
  const start=source.indexOf('document.addEventListener("click", async (e) => {');
  run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
  run(`P={id:'offline24-10',assets:[],images:{},runs:{},breakdown:{},script:{tasks:[]},replacement_checks:{}};
    S={tasks:[{id:'seg-01',title:'字幕测试',kind:'speaker',mode:'video',blank_captions:true,image_ids:[],source_start:0,source_end:10.052,duration:10,ratio:'9:16'}]};
    P.script=clone(S);renderTasks(true)`);
  const masks=[{bbox:[0,.55,.23,.72],start:0,end:2.93},
    {bbox:[.17,.36,.63,.84],start:3,end:4,motion_warning:warning},
    {bbox:[.17,.36,.63,.84],start:6,end:7,motion_warning:warning}];
  context.warning=warning;
  function check(masks){context.checks={'seg-01':{errors:[],warnings:[],issues:[],masks}};run('applyReplacementChecks(checks)');}
  async function toggle(value){const input=$('[data-m="blank_captions"]');input.checked=value;for(const fn of events.change || [])await fn({target:input,preventDefault(){}});}
  return {run,$,check,toggle,masks,context};
}
(async()=>{
  const t=setup();
  assert.ok(t.$('[data-task]').textContent.includes(description),'plain explanation appears beside the checkbox');
  assert.equal(t.$('[data-m="blank_captions"]').checked,true);
  assert.doesNotMatch(t.$('[data-caption-warning]').textContent,/下半身|条带/,'no global motion warning');
  t.check(t.masks);
  let warnings=t.$('[data-caption-warning]').querySelectorAll('.caption-warning');
  assert.equal(warnings.length,2,'only large masks warn, separately even with the same warning text');
  assert.ok(warnings[0].textContent.includes('3.00–4.00 秒'));assert.ok(warnings[1].textContent.includes('6.00–7.00 秒'));
  assert.ok(warnings.every(p=>p.textContent.includes(warning)));
  t.run('renderTasks(true)');
  assert.equal(t.$('[data-caption-warning]').querySelectorAll('.caption-warning').length,2,'redraw preserves per-row warnings');
  t.check([t.masks[0]]);
  assert.equal(t.$('[data-caption-warning]').textContent,'','small masks clear warnings on refresh');
  t.check([{...t.masks[0],motion_warning:'遮挡条带覆盖画面高度 48.5–78.5%，下半身动作的参考会变弱'}]);
  assert.equal(t.$('[data-caption-warning]').textContent,'','legacy small-band warnings are no longer shown');
  t.check(t.masks);
  await t.toggle(false);
  assert.equal(t.run('S.tasks[0].blank_captions'),false,'opt-out is saved to editable task');
  assert.equal(t.$('[data-m="blank_captions"]').checked,false);
  assert.equal(t.$('[data-caption-warning]').textContent,'');
  assert.doesNotMatch(t.$('[data-task]').textContent,/字幕可能被复制进成片|下半身动作|遮挡条带/);
  t.check(t.masks);
  assert.equal(t.$('[data-caption-warning]').textContent,'','stale check cannot reintroduce warnings when opted out');
  await t.toggle(true);assert.equal(t.run('S.tasks[0].blank_captions'),true);
  assert.ok(t.$('[data-task]').textContent.includes(description));
  // Pending previews use the same timed, per-mask warnings and escape text.
  t.context.masks=t.masks;t.run(`P.runs['seg-01']=[{rev:'r1',state:'pending_confirmation',job_sha256:'offline',
    action_comparison:{status:'completed',provider:'mock',model:'offline'},clip_processing:{muted:true,masks},
    preview:{tasks:[{images:[],reference_videos:[],prompt:'offline'}]}}];renderRuns(S.tasks[0])`);
  warnings=t.$('[data-live]').querySelectorAll('.caption-warning');assert.equal(warnings.length,2);
  assert.match(warnings[0].textContent,/3\.00–4\.00 秒/);assert.match(warnings[1].textContent,/6\.00–7\.00 秒/);
  t.check([{...t.masks[1],motion_warning:'大框 <script> & warning'}]);
  assert.equal(t.$('[data-caption-warning]').querySelector('script'),null);
  assert.match(t.$('[data-caption-warning]').textContent,/<script> & warning/);
  console.log('PASS: 24.10 caption explanation, per-row timed warnings, live refresh, redraw, escaping and opt-out');
})().catch(error=>{console.error(error);process.exitCode=1;});
