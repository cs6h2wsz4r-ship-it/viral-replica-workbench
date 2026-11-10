/* V6a: real rendering and delegated events, synthetic state and mocked HTTP. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {fixture}=require('./test_v6_ui');
const {click}=require('./test_v5_ui');
function seed(t){
 t.run(`P.breakdown.characters=[{id:'C1',name:'男人'},{id:'C2',name:'女孩'},{id:'C3',name:'未命名'},{id:'C4',name:'儿童'},{id:'C5',name:'幼童'}];
 P.replacements.characters=[{source_id:'C1',action:'replace',asset_id:'new-person',description:'短发蓝色上衣黑色长裤'},{source_id:'C2',action:'keep',asset_id:'unused-photo'},{source_id:'C3',action:'keep'},{source_id:'N1',action:'add',name:'新来者',asset_id:'added-person'}];
 P.assets.push({id:'new-person',type:'image',file:'new-person.jpg'},{id:'added-person',type:'image',file:'added-person.jpg'},{id:'original-C2',type:'image',frame_character:'C2',file:'source-C2.jpg'},{id:'unused-photo',type:'image',file:'wrong.jpg'});
 P.director.segments[0].what='THE MAN单手提着THE PRODUCT，THE GIRL接过，PERSON A看着PERSON B。';
 P.director.segments[0].prompt='图1 THE MAN和THE GIRL。THE PRODUCT保持。';
 P.runs.d01[0].review.issues=[{start:4,end:5,level:'high',what:'THE MAN末段问题。'},{start:1,end:3,level:'high',what:'THE GIRL较长问题！'},{start:1,end:2,level:'low',what:'PERSON A较短问题？'},{start:1,end:2,level:'high',what:'THE CHILD C5同时间问题；'}];
 reviewDrafts.clear();renderSteps()`);
}
async function sortingAndSubmission(){
 const t=fixture();seed(t);
 const before=t.run('JSON.stringify(P.runs.d01[0].review.issues)'),marks=t.run("reviewMarks('d01',P.runs.d01[0])");
 assert.deepEqual(t.$$('[data-review-issue]').map(x=>x.dataset.reviewIssue),['2','3','1','0']);
 assert.deepEqual(t.$$('[data-review-issue]').map(x=>x.checked),[false,true,true,true]);
 const check=t.$('[data-review-issue="2"]');check.checked=true;await t.event('change',check);
 const late=t.$('[data-review-issue="0"]');late.checked=false;await t.event('change',late);
 t.run('renderSteps()');assert.equal(t.$('[data-review-issue="2"]').checked,true);
 await t.click(t.$('[data-fix-plan]'));
 assert.deepEqual(t.requests.find(r=>r.method==='POST'&&r.url.endsWith('/fix-plan')).body,{issues:[1,2,3],note:''});
 assert.equal(t.run('JSON.stringify(P.runs.d01[0].review.issues)'),before);
 assert.equal(t.run("reviewMarks('d01',P.runs.d01[0])"),marks,'orange marks keep original order and values');
}
async function identityDisplay(){
 const t=fixture();seed(t);const before=t.run('JSON.stringify(P)');
 const foot=t.$('#segmentFoot');assert.ok(foot);
 assert.match(foot.textContent,/人物C1（短发蓝色上衣黑色）单手提着商品，人物C2接过，人物C3看着人物N1/);
 assert.deepEqual(t.$$('#segmentFoot img').map(x=>x.getAttribute('src').split('/').at(-1)),['new-person.jpg','source-C2.jpg','added-person.jpg']);
 for(const img of t.$$('#segmentFoot img')){assert.equal(img.getAttribute('width'),'16');assert.equal(img.getAttribute('height'),'16');}
 assert.doesNotMatch(t.$('[data-review-card]').textContent,/THE MAN|THE GIRL|PERSON A|THE CHILD/);
 assert.match(t.$('[data-review-card]').textContent,/人物C5同时间/);
 assert.equal(t.run('JSON.stringify(P)'),before,'rendering never writes back substituted text');
 t.run(`P.assets=P.assets.filter(a=>a.id!=='original-C2');P.scene_cast={s1:{subjects:[{key:'C2',cast_original_file:'existing-avatar.jpg'}]}};renderSteps()`);
 assert.ok(t.$$('#segmentFoot img').some(x=>x.getAttribute('src').endsWith('existing-avatar.jpg')));
 t.run(`P.scene_cast.s1.subjects=[{key:'C2',cast_original_url:'/api/projects/'+P.id+'/thumb?at=2'}];renderSteps()`);
 assert.ok(t.$$('#segmentFoot img').every(x=>!x.getAttribute('src').includes('/thumb?')),'display must not trigger on-demand frame creation');
 t.run(`P.assets=P.assets.filter(a=>a.id!=='new-person');renderSteps()`);
 assert.match(t.$('#segmentFoot').textContent,/人物C1/);assert.ok(!t.$$('#segmentFoot img').some(x=>x.getAttribute('src').endsWith('new-person.jpg')));
 const css=fs.readFileSync('web/style.css','utf8');assert.match(css,/\.display-person-avatar\s*\{[^}]*border-radius:\s*50%/);
}
async function planAndPromptExclusions(){
 const t=fixture();seed(t);await t.click(t.$('[data-fix-plan]'));
 t.run(`Object.assign(P.runs.d01[0].fix_plan,{why:'THE MAN和THE PRODUCT要一致',redraw:[{cell:'c02',why:'THE GIRL少了',request:'THE MAN与PERSON A同框'}],prompt_changes:[{before:'THE MAN拿THE PRODUCT。',after:'THE GIRL拿THE PRODUCT。'}]});renderSteps()`);
 const plan=t.$('[data-fix-plan-view]');assert.match(plan.textContent,/人物C1.*和商品要一致/);assert.match(plan.textContent,/原因：人物C2少了/);assert.match(plan.textContent,/怎么改：人物C1.*与人物C3同框/);
 assert.equal(t.$('[data-prompt-change] del').textContent,'THE MAN拿THE PRODUCT。');
 assert.equal(t.$('[data-prompt-change] ins').textContent,'THE GIRL拿THE PRODUCT。');
 assert.equal(t.$$('[data-prompt-change] img').length,0);
 await click(t,'调整这一段');assert.equal(t.$('[data-director-prompt]').value,'图1 THE MAN和THE GIRL。THE PRODUCT保持。');
 await click(t,'按方案修改');await t.click(t.$('[data-fix-confirm]'));
 assert.equal(t.requests.find(r=>r.url.includes('/storyboard-edits/')&&r.url.endsWith('/preview')).body.request,'THE MAN与PERSON A同框');
}
async function finalOrderingAndHandoff(){
 const t=fixture();seed(t);
 t.run(`P.final_preview={file:'final.mp4',check:{status:'completed',verdict:'fix',issues:[{start:8,end:9,segment:'d01',level:'high',what:'THE MAN晚问题'},{start:2,end:4,segment:'d02',level:'high',what:'THE GIRL较长问题'},{start:2,end:3,segment:'d01',level:'high',what:'PERSON A较短问题'}]}};renderSteps()`);
 const original=t.run('JSON.stringify(P.final_preview.check.issues)');
 assert.deepEqual(t.$$('[data-final-issue-segment]').map(x=>x.dataset.issue),['2','1','0']);
 assert.doesNotMatch(t.$('[data-final-check-card]').textContent,/THE MAN|THE GIRL|PERSON A/);
 const video=t.$('#finalPreviewView video');video.pause=()=>{};await t.click(t.$('[data-final-seek]'));assert.equal(video.currentTime,2);
 await t.click(t.$('[data-final-issue-segment]'));assert.equal(t.$('[data-review-note]').value,'PERSON A较短问题','model-bound note keeps original text');
 assert.equal(t.run('JSON.stringify(P.final_preview.check.issues)'),original);
 assert.ok(t.requests.every(r=>r.method!=='POST'),'handoff never sends a model request');
}
async function warningsAndPunctuation(){
 const t=fixture();seed(t);
 const warning=t.$$('#step-5 .caption-warning').find(x=>x.textContent.includes('检查有问题')).textContent;
 assert.match(warning,/人物C5同时间问题；人物C2较长问题；人物C1（短发蓝色上衣黑色）末段问题，/);
 assert.equal((warning.match(/。/g)||[]).length,1);assert.doesNotMatch(warning,/[。；！？.!?;][；，]/);
 assert.ok(t.$$('#step-5 .caption-warning img').length);
 t.run(`P.final_preview_status={ready:true,review_warnings:['THE MAN提示。；THE GIRL提示！，处理。'],warnings:[]};renderSteps()`);
 assert.doesNotMatch(t.$('#step-5').textContent,/THE MAN|THE GIRL|。；|！，/);
 t.run(`P.breakdown.characters[0].appearance='短发。';P.breakdown.characters[0].outfit='蓝衣！';renderReplacements(true);P.segment_boards.segments.d01.plan.questions=['补全人物。','保持场景？'];renderSteps()`);
 assert.match(t.$('#replacementForm').textContent,/短发；蓝衣/);assert.doesNotMatch(t.$('#replacementForm').textContent,/。；/);
 assert.equal(t.run("directorReviewRewriteWarning({issues:[{fix:'redraw_cell',what:'问题甲。'},{fix:'image_mode',what:'问题乙！'}]})"),'有的问题要回第 04 步（对照和修改）处理，只改提示词可能修不好：问题甲；问题乙');
 assert.equal(t.run("sceneOriginalDescription('C1')"),'短发，蓝衣');
 const scene=t.run("scenePlanCard({scene_id:'S1',subjects:[{key:'C1',name:'人物C1',kind:'character',on:false,original_description:'短发。，蓝衣。'}],time_ranges:[],frame_choices:[]})");
 assert.match(scene,/title="原片：短发，蓝衣。"/);
}
async function safeMatching(){
 const t=fixture();seed(t);
 assert.equal(t.run('JSON.stringify(issuesByTime(null))'),'[]','legacy empty issue lists stay renderable');
 t.run(`P.director.segments[0].what='THE MANAGER、PERSON AB、THE CHILD C50；THE CHILD C4和THE CHILD C5；<script>THE MAN</script>';P.replacements.characters[0].description='<img src=x onerror=bad>';renderSteps()`);
 assert.match(t.$('#segmentFoot').textContent,/THE MANAGER、PERSON AB、THE CHILD C50/);
 assert.match(t.$('#segmentFoot').textContent,/人物C4和人物C5/);assert.equal(t.$('#segmentFoot script'),null);
 assert.equal(t.$('#segmentFoot [onerror]'),null);assert.match(t.$('#segmentFoot').textContent,/<script>人物C1/);
 // Generic numbering must stay in step with scene_prompt.role_names after Z.
 t.run(`P.breakdown.characters=Array.from({length:27},(_,i)=>({id:'C'+(i+1),name:''}));P.replacements.characters=[];P.director.segments[0].what='PERSON AA遇到PERSON A';renderSteps()`);
 assert.match(t.$('#segmentFoot').textContent,/人物C27遇到人物C1/);
 t.run(`P.breakdown.characters=[{id:'C1',name:'man甲'},{id:'C2',name:'未命名'}];P.director.segments[0].what='PERSON A站在PERSON B旁边';renderSteps()`);
 assert.match(t.$('#segmentFoot').textContent,/人物C1站在人物C2旁边/,'English name detection matches Python Unicode boundaries');
}
async function main(){
 let failures=0;
 for(const test of [sortingAndSubmission,identityDisplay,planAndPromptExclusions,finalOrderingAndHandoff,warningsAndPunctuation,safeMatching]){
  try{await test();console.log('PASS V6a '+test.name);}catch(e){failures++;console.error('FAIL V6a '+test.name,e);}
 }
 assert.equal(failures,0);
}
module.exports={main,seed};if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
