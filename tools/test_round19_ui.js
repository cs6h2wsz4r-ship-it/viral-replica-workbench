/* Round 19 offline DOM and event regression; no network or project writes. */
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const {createDocument,Node}=require('./ui_test_dom');
const root=path.resolve(__dirname,'..'), source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const document=createDocument(), requests=[], events={}, scrolls=[];
Node.prototype.scrollIntoView=function(){scrolls.push(this);};
document.body.innerHTML=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<!doctype[^>]*>/gi,'').replace(/<script[\s\S]*?<\/script>/g,'');
document.addEventListener=(name,fn)=>(events[name] ||= []).push(fn);
const project={id:'round19-ui',name:'离线测试',assets:[
 {id:'product',type:'image',roles:['product_truth'],label:'商品',file:'p.jpg'},
 {id:'unused',type:'image',roles:[],label:'备用',file:'u.jpg'},
 {id:'person',type:'image',roles:['person_identity'],label:'人物',file:'a.jpg'},
 {id:'cam',type:'image',roles:['target_state'],camera_character:'C1',camera_scene:'S1',label:'主镜头',file:'c.jpg',frame_at:0.5}],
 breakdown:{characters:[{id:'C1',name:'女主'},{id:'C2',name:'提问者',appearance:'画外，不出镜'}],scene:[{id:'S1',name:'客厅',timeRanges:[[0,8]]}],shots:[{start:0,end:8,characters:['C1']}],dialogue:[{time:[0,1],speaker:'C2',onScreen:false,text:'Nice outfit',evidence:'画外男声'},{time:[1,3],speaker:'unknown',onScreen:null,text:'Thank you',evidence:'嘴型不清楚'},{time:[9,10],speaker:'C1',text:'Excluded'}]},
 replacements:{characters:[{source_id:'C1',action:'replace',asset_id:'person',camera_assets:{S1:'cam'}}],product:{action:'replace',asset_id:'product',description:'上衣'}},images:{old:{id:'old',for_task:'camera:C1:S1',state:'completed',asset_id:'cam',frame_at:0.5}},
 script:{dialogue_mode:'keep',tasks:[{id:'seg-01',kind:'speaker',source_start:0,source_end:8,duration:8,ratio:'9:16',mode:'video',image_ids:['cam','person','product'],dialogue:'Nice outfit  Thank you',source_motion_timeline:[{start:0,end:1,action:'背对镜头 back view'},{start:1,end:8,action:'转身正面'}]}]},replacement_checks:{'seg-01':{errors:[],warnings:['台词里有「dress」和新商品可能不符'],issues:[]}}};
const context=vm.createContext({document,console,project,structuredClone,setTimeout:()=>1,clearTimeout(){},localStorage:{getItem(){},setItem(){}},confirm:()=>true});
vm.runInContext(source.slice(0,source.indexOf('// ---------- settings ----------')),context);
const start=source.indexOf('document.addEventListener("click", async (e) => {');
vm.runInContext(source.slice(start,source.indexOf('\n$("#newProjectBtn").onclick',start)),context);
context.mockApi=async(method,url,body)=>{requests.push({method,url,body:structuredClone(body)});if(method==='GET')return structuredClone(project);if(url.endsWith('/camera-image/draft'))return {prompt:'离线提示词',for_task:'camera:C1:S1',image_ids:['person','product','frame'],frame_at:body.at ?? 4.2,frame_reason_code:body.at === undefined ? 'front_product' : 'manual'};if(url.endsWith('/images/preview'))return {};if(url.endsWith('/dialogue-adopt'))return structuredClone(project);if(method==='PUT'&&url.endsWith('/script')){project.script=structuredClone(body.script);return {script:project.script};}throw Error('Unexpected request '+method+' '+url);};
const run=s=>vm.runInContext(s,context), $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s);
const click=async el=>{assert.ok(el);for(const fn of events.click||[])await fn({target:el,preventDefault(){}});};
async function main(){
 run('api=mockApi;P=clone(project);S=clone(P.script);STATUS={};renderSteps();renderScript();renderReplacements()');
 assert.equal($$('[data-speaker-line]').length,2,'fallback source lines overlap segment');
 assert.equal($('[data-speaker="0"]').value,'C2');
 assert.match($('[data-speaker="0"]').textContent,/提问者.*画外/);
 assert.match($('[data-speaker-line="0"]').textContent,/画外男声/);
 assert.equal($('[data-speaker="1"]').value,'unknown');
 assert.match($('#scriptView').textContent,/有 1 句台词还没确定是谁说的，请在第 04 步确认/);
 assert.ok(!$('[data-preview]').hasAttribute('disabled'),'unknown speaker does not block preview');
 assert.equal($('#steps'),null,'single workflow navigation only');
 assert.equal($$('[data-flow-index]').length,5);
 for(const a of $$('[data-flow-index]')){assert.ok(a.getAttribute('title'));await click(a);}
 assert.equal(scrolls.length,5,'all workflow links scroll');
 assert.deepEqual($$('.chips [data-chip]').map(el=>el.dataset.chip),['cam','person','product','unused']);
 assert.equal($('[data-camera-key]'),null);
 assert.ok($('#scriptView [data-dialogue-propose]'),'new conflict warning opens step 04 proposal');
 assert.equal($('[data-task] [data-dialogue-propose]'),null,'step 05 only links to the proposal');
 assert.doesNotMatch($('[data-task] [data-replacement-check]').textContent,/改为 rewrite/);
 run('P.replacement_checks["seg-01"].dialogue_conflicts=[{title:"台词里有「dress」和新商品可能不符",fix:"请查看改写方案",details:["原句：Your dress is beautiful"]}];renderTasks(true)');
 const warningBox=$('[data-task] [data-replacement-check]');
 assert.match(warningBox.textContent,/原句：Your dress is beautiful/);
 assert.equal((warningBox.textContent.match(/台词里有「dress」和新商品可能不符/g)||[]).length,1,'conflict title shown once');
 const field=$('[data-speaker="1"]');field.value='C1';
 for(const fn of events.change||[])fn({target:field});
 await click($('#saveScript'));
 const save=requests.find(r=>r.method==='PUT');assert.ok(save);
 assert.equal(save.body.script.tasks[0].dialogue,'Nice outfit  Thank you','speaker-only edits preserve dialogue byte for byte');
 assert.deepEqual(save.body.script.tasks[0].dialogue_lines.map(l=>[l.start,l.end,l.speaker,l.onScreen,l.evidence]),[[0,1,'C2',false,'画外男声'],[1,3,'C1',true,'嘴型不清楚']]);
 assert.ok(!requests.some(r=>r.method==='POST'),'speaker edit uses only local script save');
 assert.doesNotMatch($('#scriptView').textContent,/有 1 句台词/);
 run('P.script.tasks[0].dialogue_proposal={lines:[{start:0,end:1,original:"Nice outfit",proposed:"Nice top",speaker:"C2",onScreen:false,evidence:"画外男声",changed:true,reason:"替换商品"}]};renderScript(true)');
 assert.match($('.dialogue-proposal table').textContent,/说话人/);assert.match($('.dialogue-proposal table').textContent,/提问者.*画外/);
 const proposalLines=run('JSON.stringify(S.tasks[0].dialogue_proposal.lines)');
 await click($('#scriptView [data-dialogue-adopt]'));
 const adopted=requests.find(r=>r.url.endsWith('/dialogue-adopt'));
 assert.equal(JSON.stringify(adopted.body.lines),proposalLines,'proposal adoption preserves speaker, visibility and evidence');
 run('P.script.tasks[0].dialogue_lines=[{start:2,end:3,text:"Explicit task line",speaker:"C2",onScreen:false,evidence:"任务中的证据",confidence:0.8}];renderScript(true)');
 assert.equal($$('[data-speaker-line]').length,1,'task lines take precedence over breakdown');
 assert.match($('[data-speaker-line]').textContent,/Explicit task line.*任务中的证据/);
 assert.equal($('#scriptView [data-k="dialogue"]'),null,'per-line editor replaces contradictory whole-dialogue field');
 $('[data-dialogue-text="0"]').value='Edited task line';
 await click($('#saveScript'));
 assert.equal(requests.filter(r=>r.method==='PUT').at(-1).body.script.tasks[0].dialogue_lines[0].text,'Edited task line');
 assert.equal(requests.filter(r=>r.method==='PUT').at(-1).body.script.tasks[0].dialogue,'Edited task line');
 assert.equal(requests.filter(r=>r.method==='PUT').at(-1).body.script.tasks[0].dialogue_lines[0].confidence,0.8,'extra line metadata retained');
 // SB1 reference-frame UI is retired; speaker/script behavior above remains.
 const nodes=[$('#scriptView .seg'),$('[data-speaker="0"]'),$('.scene-camera'),$('#workflow').firstChild,$('[data-task]')];
 for(let i=0;i<3;i++)run('renderSteps();renderReplacements();renderScript();renderTasks()');
 assert.deepEqual([$('#scriptView .seg'),$('[data-speaker="0"]'),$('.scene-camera'),$('#workflow').firstChild,$('[data-task]')],nodes,'three unchanged polls preserve nodes');
 console.log('Round 19 UI: speaker attribution/save, frame reasons, single navigation, chip order and stable polls passed');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
