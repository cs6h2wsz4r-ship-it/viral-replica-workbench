/* 24.7: real DOM rendering/events, mock API, Python/JS counting parity. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const {createDocument}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(),events={},requests=[];
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
const texts=['Excuse me, your dress is beautiful.','Oh, thank you so much.',"It's so flowy and comfortable.",'I love the open back.','The fabric is perfect for this weather.'];
const proposal={lines:texts.map((original,i)=>({start:i*3,end:i*3+[1.82,2,1.36,.82,2.5][i],original,
  proposed:['Excuse me, your outfit is beautiful.',texts[1],"It's knit and comfortable.",'I love the knit top.',texts[4]][i],
  speaker:i?'C1':'C2',changed:![1,4].includes(i),restored:i===4,too_long:i===2,
  reason:i===2?'把 flowy 换成 knit，依据新商品针织材质，长度6≤max7，mustKeep=false，maxUnits=7':'模型原因不该出现在未改句'}))};
const project={id:'offline-24-7',assets:[],images:{},breakdown:{characters:[{id:'C1'},{id:'C2'}],overview:{language:'en'}},
  script:{tasks:[{id:'seg-01',kind:'speaker',language:'en',duration:15,source_start:0,source_end:15,dialogue:texts.join('\n'),dialogue_proposal:proposal}]}};
const context=vm.createContext({document,console,project,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},confirm:()=>true});
const run=s=>vm.runInContext(s,context),$=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);
run(source.slice(0,source.indexOf('// ---------- settings ----------')));
const start=source.indexOf('document.addEventListener("click", async (e) => {');
run(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)));
context.mockApi=async(method,url,body)=>{
  requests.push({method,url,body:structuredClone(body)});
  if(url.endsWith('/dialogue-adopt'))return structuredClone(project);
  if(url.endsWith('/dialogue-proposal'))return structuredClone(proposal);
  if(url.endsWith('/replacement-check'))return {};
  throw Error('Unexpected request '+url);
};
const fire=async(name,el)=>{assert.ok(el);for(const fn of events[name] || [])await fn({target:el,preventDefault(){}});};
const box=()=>$('#scriptView [data-dialogue-box]');
const warning='这句比原句长不少，生成后注意听是否说得太快';
async function main(){
  run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={};renderScript(true);renderTasks(true)');
  assert.equal(box().querySelector('[data-dialogue-adopt]').hasAttribute('disabled'),false,'screenshot is directly adoptable despite stale too_long');
  assert.equal(box().querySelectorAll('.dialogue-too-long').length,0);
  assert.equal(box().querySelectorAll('[aria-invalid="true"]').length,0);
  assert.equal(box().querySelector('[data-dialogue-length]'),null);
  assert.equal(box().querySelector('[data-dialogue-length-warning]'),null);
  assert.doesNotMatch(box().textContent,/估算 .*音节|最多 .*音节|让模型改短/);
  assert.equal(box().querySelector('[data-dialogue-shorten]'),null);
  for(const i of [1,4]){
    const reason=box().querySelector(`[data-dialogue-line="${i}"]`).closest('tr').querySelector('[data-dialogue-reason]');
    assert.equal(reason.textContent,'不涉及新人物或新商品，保留原句');
  }
  const reason=box().querySelector('[data-dialogue-line="2"]').closest('tr').querySelector('[data-dialogue-reason]');
  assert.equal(reason.textContent,'把 flowy 换成 knit，依据新商品针织材质');
  for(const sample of ['换成针织，max 7','换成针织，MAX7','换成针织（mustKeep=false）','换成针织，长度6≤7','换成针织，maxUnits=7']){
    context.sample=sample;assert.equal(run('dialogueReason({changed:true,reason:sample})'),'换成针织');
  }
  for(const sample of [
    '把 flowy 换成 knit。依据新商品针织材质。长度6≤max7',
    '把 flowy 换成 knit\n依据新商品针织材质\nmaxUnits=7',
    '把 flowy 换成 knit（mustKeep=false）。依据新商品针织材质(max7)',
  ]){
    context.sample=sample;
    assert.equal(run('dialogueReason({changed:true,reason:sample})'),'把 flowy 换成 knit，依据新商品针织材质');
  }
  context.sample='把 flowy 换成 knit。依据新商品针织材质；保持原语气。';
  assert.equal(run('dialogueReason({changed:true,reason:sample})'),context.sample,'ordinary model reasons keep their exact punctuation');
  await fire('click',box().querySelector('[data-dialogue-adopt]'));
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-adopt')).length,1);
  assert.equal(requests[0].body.lines[2].proposed,"It's knit and comfortable.");
  requests.length=0;
  const edit=box().querySelector('[data-dialogue-line="2"]');edit.focus();
  edit.value='It is so wonderfully beautiful and comfortable for every single day.';await fire('input',edit);
  assert.equal(document.activeElement,edit);
  for(const b of $$('[data-dialogue-box]')){
    const hint=b.querySelector('[data-dialogue-length-warning]');assert.ok(hint);assert.equal(hint.textContent,warning);
    assert.match(hint.getAttribute('class'),/hint/);
    assert.equal(b.querySelector('[data-dialogue-adopt]').hasAttribute('disabled'),false);
    assert.equal(b.querySelectorAll('[aria-invalid="true"]').length,0);
  }
  assert.equal(requests.length,0,'local edits never call a model');
  run('renderScript(true);renderTasks(true)');assert.ok(box().querySelector('[data-dialogue-length-warning]'));
  await fire('click',box().querySelector('[data-dialogue-adopt]'));
  assert.equal(requests.filter(r=>r.url.endsWith('/dialogue-adopt')).length,1,'very long line still adopts');
  const input=box().querySelector('[data-dialogue-line="2"]');input.value=texts[2];await fire('input',input);
  for(const b of $$('[data-dialogue-box]')){
    assert.equal(b.querySelector('[data-dialogue-length-warning]'),null);
    assert.equal(b.querySelector('[data-dialogue-line="2"]').closest('tr').querySelector('[data-dialogue-reason]').textContent,'不涉及新人物或新商品，保留原句');
  }
  input.value='';await fire('input',input);assert.ok(box().querySelector('[data-dialogue-adopt]').hasAttribute('disabled'),'empty-line validation remains');
  // Exactly 1.3 is quiet; timing and stale length flags do not affect the warning.
  assert.equal(run('dialogueLineMetrics({}, {original:"a ".repeat(10),proposed:"a ".repeat(13),start:0,end:.01}).length_warning'),false);
  assert.equal(run('dialogueLineMetrics({}, {original:"a ".repeat(10),proposed:"a ".repeat(14),start:0,end:100}).length_warning'),true);
  const counts={"It's knit and comfortable.":7,"It's so flowy and comfortable.":9,"don't":1,"I'm":1,"we're":1,"you'll":1,"isn't":1,'make love table coffee rhythm':7,'中文 １２٣4':6};
  for(const [text,count] of Object.entries({...counts}))counts[text.replaceAll("'",'’')]=count;
  const samples=Object.keys(counts);
  const python=JSON.parse(execFileSync(path.join(root,'runtime/venv/bin/python'),['-B','-c','import json,sys;from console.evidence import speech_units;print(json.dumps([speech_units(t,"en") for t in json.loads(sys.stdin.read())]))'],{cwd:root,input:JSON.stringify(samples),encoding:'utf8'}));
  samples.forEach((text,i)=>{context.sample=text;assert.equal(run('speechUnits(sample)'),counts[text]);assert.equal(python[i],counts[text]);});
  assert.doesNotMatch(source,/dialogueShorten|data-dialogue-shorten|让模型改短|正在改短/);
  const style=fs.readFileSync(path.join(root,'web/style.css'),'utf8');
  assert.match(style,/\[data-dialogue-length-warning\][^}]*color:\s*#6b7280/);
  assert.doesNotMatch(style,/dialogue-too-long/);
  console.log('Round 24.7 UI: screenshot adoption / gentle live warning / plain reasons / contraction parity passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
