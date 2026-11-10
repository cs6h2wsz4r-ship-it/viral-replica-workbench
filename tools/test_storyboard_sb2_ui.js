/* Real SB2 renderers/events with synthetic state, HTTP and MediaRecorder mocks. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {setup}=require('./v2_ui_fixture');
async function main(){
 const first=setup();first.run('P.replacements={characters:[],product:{}};newStoryboardDrafts.clear();renderNewStoryboards();P.replacements={characters:[{source_id:"C1",action:"replace",asset_id:"dad",description:"白发父亲"}],product:{action:"replace",asset_id:"watch",description:"黑色手表"}};renderNewStoryboards()');
 assert.ok(first.$('[data-edit-material="C1"]').checked,'a photo selected after first render defaults on');
 assert.equal(first.$('[data-edit-template]'),null);assert.equal(first.$('[data-edit-request="s01"]').value,'');
 const reset=setup();reset.run('editDraft("s01").request="旧原片需求";const savedBoards=P.segment_boards;P.segment_boards=null;renderNewStoryboards();P.segment_boards=savedBoards;renderNewStoryboards()');
 assert.equal(reset.$('[data-edit-request="s01"]').value,'','clearing source also clears the old local draft');
 const t=setup();
 t.run('P.storyboard.version=2;P.storyboard.cells[0].action_full="动作全文。后续";renderNewStoryboards()');
 assert.match(t.$('#step-4').textContent,/点右边某一镜，只改这一镜/);
 assert.equal(t.$$('[data-edit-sheet]').length,1);
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c06"]'));assert.ok(t.$('[data-edit-sheet="s02"]'));
 await t.click(t.$('[data-compare-side="source"] [data-compare-cell="c01"]'));assert.ok(t.$('[data-edit-material="C1"]'));assert.ok(t.$('[data-edit-material="product"]'));
 assert.match(t.$('#step-4').textContent,/第 1 段 · 门廊|待生成/);
 const original=t.context.mockApi;t.context.mockApi=async(method,url,body)=>{
  if(!url.includes('/storyboard-edits/'))return original(method,url,body);
  t.requests.push({method,url,body:structuredClone(body)});
  const p=JSON.parse(t.run('JSON.stringify(P)'));p.segment_boards ||= {segments:{}};
  const state=p.segment_boards.segments.s01 ||= {materials:{C1:true,product:true},versions:[]};
  if(url.endsWith('/preview'))return {id:'img-sb2',for_task:body.cell_id?'storyboard-cell:'+body.cell_id:'storyboard:s01',state:'pending_confirmation',channel:'official',preview:{prompt:'COMPLETE PROMPT Panel 1 no change',images:[{path:'/fake/source.png'}]},storyboard_context:{sheet_id:'s01',plan_sig:'new',references:[{name:'原片拼图',path:'/fake/source.png'}]},warnings:[]};
  if(body.materials)state.materials=body.materials;if(body.request!==undefined)state.request=body.request;if(body.plan)state.plan=body.plan;
  if(url.endsWith('/plan'))state.plan={summary:'换父亲和手表',cells:p.segments_plan.segments[0].cells.map(cell=>({cell,changes:cell==='c01'?'换父亲':'',keep:'机位和构图'})),remove_text:true,questions:['第二格是谁？'],sig:'new',generated_at:'now',edited:false};
  if(url.endsWith('/use'))state.current=body.version;
  t.context.nextProject=p;t.run('P=nextProject');return p;
 };t.run('api=mockApi');
 await t.click(t.$('[data-edit-plan="s01"]'));assert.equal(t.requests.filter(r=>r.url.endsWith('/plan')).length,1);assert.equal(t.requests.at(-1).body.request,'');
 assert.equal(t.$('[data-edit-template]'),null);assert.match(t.$('#step-4').textContent,/补充要求（可不填）/);
 await t.click(t.$('[data-edit-plan="s01"]'));assert.equal(t.$$('[data-edit-change]').length,5);assert.equal(t.$('.edit-plan').open,false);t.$('.edit-plan').open=true;assert.match(t.$('#step-4').textContent,/可以在对应格补充说明：第二格是谁？/);
 const input=t.$('[data-edit-change="c01"]');input.value='换成父亲，保留灰衣';await t.event('input',input);
 await t.click(t.$('[data-edit-save="s01"]'));assert.equal(t.requests.at(-1).body.plan.cells[0].changes,'换成父亲，保留灰衣');assert.equal(t.requests.at(-1).body.plan.edited,true);
 await t.click(t.$('[data-edit-preview="s01"]'));assert.ok(t.requests.at(-1).url.endsWith('/preview'));assert.equal(t.requests.filter(r=>r.url.endsWith('/confirm')).length,0);assert.match(t.$('#step-4').textContent,/COMPLETE PROMPT|原片拼图/);
 // An edit after preview must remove its payment button until a new preview exists.
 const revised=t.$('[data-edit-change="c01"]');revised.value='预览以后改了';await t.event('input',revised);t.run('renderNewStoryboards()');
 assert.equal(t.$('[data-v5-confirm="s01"]'),null);
 await t.click(t.$('[data-edit-preview="s01"]'));
 await t.click(t.$('[data-v5-confirm="s01"]'));assert.ok(t.requests.some(r=>r.url.endsWith('/images/img-sb2/confirm')));
 t.run('P.segment_boards={segments:{s01:{source:P.segment_boards.segments.s01.source,materials:{C1:true,product:true},plan:{summary:"方案",sig:"new",cells:P.segments_plan.segments[0].cells.map(cell=>({cell,changes:"",keep:"构图"})),remove_text:true,questions:[]},current:"v2",versions:[{id:"v1",from:"full",file:"v1.jpg",plan_sig:"old",cells:{c01:"v1-c01.jpg"}},{id:"v2",from:"cell:c05",file:"v2.jpg",plan_sig:"old",cells:{c01:"v2-c01.jpg"}}]}}};P.images={};v5Actions.clear();newStoryboardDrafts.clear();renderNewStoryboards()');
 assert.match(t.$('#step-4').textContent,/第 1 版 · 整张|第 2 版 · 重画第 5 格|在用/);
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c01"]'));assert.match(t.$('[data-edit-cell-detail]').textContent,/第 1 镜要改什么/);assert.match(t.$('#segmentBar').textContent,/正在改第 1 镜/);
 const change=t.$('[data-edit-cell-request="s01"]');change.value='只改这一格';await t.event('input',change);await t.click(t.$('[data-edit-cell-preview="s01"]'));
 assert.equal(t.requests.at(-1).body.cell_id,'c01');
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c01"]'));assert.equal(t.$('[data-edit-cell-detail]'),null);
 const keepRequest=t.$('[data-edit-request="s01"]');keepRequest.value='切版本前刚写的需求';await t.event('input',keepRequest);
 t.$('.edit-plan').open=true;const keepPlan=t.$('[data-edit-change="c01"]');keepPlan.value='切版本前刚改的方案';await t.event('input',keepPlan);
 await t.click(t.$('[data-edit-use="v1"]'));assert.equal(t.requests.at(-1).body.version,'v1');
 assert.equal(t.$('[data-edit-request="s01"]').value,'切版本前刚写的需求');assert.equal(t.$('[data-edit-change="c01"]').value,'切版本前刚改的方案');
 for(const warning of ['选角或商品改过，改图方案可能要重新生成','改图方案改过，当前分镜还是按旧方案生成的','原片分镜变了，这里的改图结果对不上格号，需要重新生成方案']){
  t.context.warning=warning;t.run('P.segment_board_status={s01:{warnings:[warning],source_stale:warning.startsWith("原片分镜")}};renderNewStoryboards()');assert.doesNotMatch(t.$('#step-4').textContent,new RegExp(warning));assert.equal(t.run('comparisonNeedsBoard(P.segments_plan.segments[0])'),true);
 }
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c01"]'));assert.equal(t.run('comparisonSelection().cell'),null);
 assert.equal(t.$('[data-edit-cell-preview]'),null);assert.equal(t.$('[data-edit-cell-detail]'),null);
 t.run('P.storyboard.version=1;renderStoryboard()');assert.match(t.$('#storyboardView').textContent,/原片分镜的规则更新了，重新生成会按截帧那一刻判断商品，并避开字幕切换的画面/);assert.match(t.$('#storyboardView').textContent,/重新生成（免费）/);
 const css=fs.readFileSync(path.join(__dirname,'../web/style.css'),'utf8');assert.match(css,/max-width:\s*360px/);assert.match(css,/max-width:\s*560px/);assert.match(css,/@media\s*\(max-width:\s*899px\)/);assert.ok(t.$('.storyboard-columns'));
 // Microphone failures stay beside the control and do not make a remote model call.
 t.context.navigator={mediaDevices:{getUserMedia:async()=>{throw Error('denied');}}};t.context.MediaRecorder=function(){};
 await t.click(t.$('[data-edit-mic="s01"]'));assert.match(t.$('#step-4').textContent,/麦克风/);
 const voice=setup();let stopped=0,finished;
 voice.context.Blob=Blob;voice.context.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){stopped++;}}]})}};
 voice.context.MediaRecorder=class {constructor(){this.mimeType='audio/webm';}start(){}stop(){this.ondataavailable({data:new Blob(['fake audio'])});finished=this.onstop();}};
 voice.context.mockApi=async(method,url,body)=>{assert.match(url,/voice-to-text$/);assert.ok(body instanceof Blob);return {text:'再换成黑色手表'};};voice.run('api=mockApi;editDraft("s01").request="原有需求";renderNewStoryboards()');
 await voice.click(voice.$('[data-edit-mic="s01"]'));assert.match(voice.$('[data-edit-mic="s01"]').textContent,/结束录音/);
 await voice.click(voice.$('[data-edit-mic="s01"]'));await finished;
 assert.equal(stopped,1);assert.equal(voice.$('[data-edit-request="s01"]').value,'原有需求\n再换成黑色手表');
 // Exercise the production HTTP helper: recording bytes must not be JSON-stringified.
 const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
 t.context.Blob=Blob;t.context.audioBlob=new Blob(['recording bytes'],{type:'audio/webm'});
 let sent;t.context.fetch=async(url,options)=>{sent={url,options};return {ok:true,json:async()=>({text:'语音文字'})};};
 t.run(source.slice(source.indexOf('async function api('),source.indexOf('async function upload(')));
 await t.run('api("POST",`projects/${P.id}/voice-to-text`,audioBlob)');assert.equal(sent.options.body,t.context.audioBlob);assert.equal(sent.options.headers['Content-Type'],'audio/webm');
 t.run('P.segment_boards.segments.s03={current:"v1",versions:[{id:"v1",from:"full",file:"old-sheet.jpg"}]};renderNewStoryboards()');
 assert.equal(t.$('[data-edit-archive="s03"]'),null,'old whole-film sheets are no longer displayed');assert.ok(t.run('P.segment_boards.segments.s03.versions.length'),'stored history is not deleted');
 t.run('P.segments_plan=null;renderNewStoryboards()');assert.match(t.$('#step-4').textContent,/先在第 04 步生成分段方案/);
 console.log('PASS SB2: materials/optional supplement/empty plan/edit/save/preview before confirm/versions/cell detail/warnings/upgrade/layout/mic');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
