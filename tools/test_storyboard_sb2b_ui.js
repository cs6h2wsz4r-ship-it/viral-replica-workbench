/* Production rendering/events, synthetic state and mocked HTTP only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {setup}=require('./v2_ui_fixture');
async function main(){
 const t=setup();
 t.run('P.storyboard.version=3;P.storyboard.cells.forEach((c,i)=>{c.shot_index=i+1;c.part=null;});P.storyboard.cells[0].part=1;P.storyboard.cells[1].part=2;P.storyboard.cells[1].shot_index=1;P.storyboard.cells[2].part=1;P.storyboard.cells[3].part=2;P.storyboard.cells[0].product="";P.storyboard.cells[0].product_later=true;renderStoryboard();renderNewStoryboards()');
 assert.deepEqual(t.$$('[data-story-merge]').map(e=>e.dataset.storyMerge),['c01']);
 assert.equal(t.$('[data-story-merge]').textContent,'这两格其实是同一个镜头，合回去');
 assert.match(t.$('[data-story-row="c01"]').textContent,/这一格后半段出现商品/);
 assert.doesNotMatch(t.$('[data-story-row="c01"]').textContent,/商品：/);
 t.run('P.storyboard.merges=[["c03","c04"]];renderStoryboard()');assert.ok(t.$('[data-story-action="restore"]'));
 t.run('P.storyboard.version=2;renderStoryboard()');assert.match(t.$('#storyboardView').textContent,/规则更新了/);
 const input=t.$('[data-edit-request="s01"]');assert.match(input.parentElement.textContent,/补充要求（可不填）/);
 assert.equal(input.getAttribute('placeholder'),'在这里补充这一段的画面要求');
 assert.equal(t.$('[data-edit-template]'),null);assert.ok(t.$('[data-edit-mic]'));assert.equal(t.$('[data-edit-plan="s01"]').disabled,false);
 t.context.mockApi=async(method,url,body)=>{t.requests.push({method,url,body:structuredClone(body)});const p=JSON.parse(t.run('JSON.stringify(P)'));if(body.action==='reslice'){const v=p.segment_boards.segments[body.sheet_id].versions.find(v=>v.id===body.version_id);v.slicer=2;v.resliced_at='now';}return p;};t.run('api=mockApi');
 await t.click(t.$('[data-edit-plan="s01"]'));assert.equal(t.requests.at(-1).body.request,'');assert.match(t.requests.at(-1).url,/\/plan$/);
 t.run('P.segment_boards={segments:{s01:{source:P.segment_boards.segments.s01.source,current:"v1",versions:[{id:"v1",from:"full",file:"v1.jpg",cells:{c01:"v1-c01.jpg"}}]}}};newStoryboardDrafts.clear();renderNewStoryboards()');
 assert.match(t.$('[data-edit-sheet="s01"]').textContent,/这一版切格有误，可按模型原图免费重新切。/);
 const button=t.$('[data-edit-reslice="v1"]');assert.equal(button.textContent,'重新切格（免费）');
 assert.ok(t.$('[data-edit-sheet="s01"]').innerHTML.indexOf('data-edit-reslice')<t.$('[data-edit-sheet="s01"]').innerHTML.indexOf('class="edit-versions"'));
 const before=t.$('[data-edit-sheet="s01"] .edit-versions img').src;
 const supplement=t.$('[data-edit-request="s01"]');supplement.value='未保存的补充';await t.event('input',supplement);
 await t.click(button);assert.equal(t.requests.at(-1).url,'projects/sb1-offline/storyboard-edits');
 assert.deepEqual(t.requests.at(-1).body,{action:'reslice',sheet_id:'s01',version_id:'v1'});
 assert.equal(t.$('[data-edit-reslice]'),null);assert.equal(t.$('[data-edit-request="s01"]').value,'未保存的补充');
 assert.notEqual(t.$('[data-edit-sheet="s01"] .edit-versions img').src,before,'overwritten image bypasses browser cache');
 await t.click(t.$('[data-compare-side="target"] [data-compare-cell="c01"]'));assert.ok(t.$('[data-edit-cell-detail]'));
 // A reload must not revive a paid preview using pixels from before re-slicing.
 t.run('newStoryboardInvalidPreviews.clear();P.images.old={id:"old",state:"pending_confirmation",storyboard_context:{sheet_id:"s01",cell_id:"c01",base_current:"v1",references:[]},preview:{prompt:"old wrong cell"}};renderNewStoryboards()');
 assert.equal(t.run("v5BoardPreview('s01','c01')"),undefined,'re-sliced pixels invalidate the old preview');
 // A fresh version has no resliced_at, while backend JSON uses null: it is valid.
 t.run('delete P.segment_boards.segments.s01.versions[0].resliced_at;P.images.old.storyboard_context.base_resliced_at=null;renderNewStoryboards()');
 assert.ok(t.run("v5BoardPreview('s01','c01')"),'fresh single-cell previews stay reusable by inline confirmation');
 // The real HTTP helper includes the current page build on this new endpoint.
 const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');let sent;
 t.context.fetch=async(url,options)=>{sent=options;return {ok:true,json:async()=>({})};};
 t.run(source.slice(source.indexOf('async function api('),source.indexOf('async function upload(')));
 t.run('PAGE_BUILD={server_build:"s",web_build:"w"}');
 await t.run('api("POST","projects/sb1-offline/storyboard-edits",{action:"reslice",sheet_id:"s01",version_id:"v1"})');
 assert.equal(sent.headers['X-Server-Build'],'s');assert.equal(sent.headers['X-Web-Build'],'w');
 console.log('PASS SB2b: reslice/version headers/cache/drafts; split-only merge; product later; optional supplement');
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
