/* V9c: actual settings/events and frozen image records; all transport is synthetic. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {fixture:base}=require('./test_v5_ui');
const source=fs.readFileSync('web/app.js','utf8');
const choices=[
 ['official:gpt-image-2|auto','official:gpt-image-2','auto','官方 · gpt-image-2 · 自动画质（一张约 $0.05）'],
 ['official:gpt-image-2|high','official:gpt-image-2','high','官方 · gpt-image-2 · 高画质（一张约 $0.20，细节更好，更慢）'],
 ['official:gpt-image-2.6-sunburst|high','official:gpt-image-2.6-sunburst','high','官方 · gpt-image-2.6 sunburst · 高画质（一张约 $0.08）'],
 ['third_party:gpt-image-2|auto','third_party:gpt-image-2','auto','三方渠道 · gpt-image-2'],
];
function fixture(overrides={}){
 const t=base();t.run(source.slice(source.indexOf('// ---------- settings ----------'),source.indexOf('// ---------- events ----------')));
 t.context.FormData=class {constructor(form){this.form=form;}entries(){return t.$$('#settingsForm [name]').filter(e=>!e.disabled).map(e=>[e.getAttribute('name'),e.value]);}};
 t.$('#settingsDialog').showModal=()=>{};t.$('#settingsDialog').close=()=>{};
 t.settings={OPENAI_API_KEY:'configured',JIMMY_API_KEY:'configured',OPENAI_IMAGE_MODEL:'gpt-image-2',ROUTE_STORYBOARD_IMAGE:'official:gpt-image-2',ROUTE_CAMERA_IMAGE:'official:gpt-image-2',STORYBOARD_IMAGE_QUALITY:'auto',CAMERA_IMAGE_QUALITY:'auto',...overrides};
 t.context.mockSettings=async(method,url,body)=>{t.requests.push({method,url,body:structuredClone(body)});if(url==='settings'){if(method==='POST')Object.assign(t.settings,body);return structuredClone(t.settings);}if(url==='status')return {providers:{},routes:{}};throw Error('Unexpected mocked request: '+url);};
 t.run('api=mockSettings;P=null;STATUS={providers:{},routes:{}}');
 t.open=()=>t.run('openSettings()');t.save=async()=>{await t.run('saveSettings({preventDefault(){}})');return t.requests.filter(r=>r.method==='POST').at(-1).body;};
 return t;
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
test('four ordered choices round-trip route and quality for both image steps',async()=>{
 for(const [value,route,quality,label] of choices){const t=fixture();await t.open();const card=t.$('[data-model-card="image"]');
  assert.deepEqual(card.querySelectorAll('option').slice(0,4).map(o=>o.value),choices.map(c=>c[0]));assert.match(card.textContent,new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  assert.match(t.$('#settingsFields').textContent,/生成新分镜图，包括按格改图和批量裂变。费用按实测估算，参考图越多越贵。/);
  card.value=value;await t.event('change',card);const body=await t.save();for(const step of ['STORYBOARD','CAMERA']){assert.equal(body['ROUTE_'+step+'_IMAGE'],route);assert.equal(body[step+'_IMAGE_QUALITY'],quality);}
  await t.open();assert.equal(t.$('[data-model-card="image"]').value,value);assert.doesNotMatch(t.$('#settingsFields').textContent,/免费/);
 }
});
test('advanced selectors override independently and unrelated saves preserve custom models',async()=>{
 const t=fixture({OPENAI_IMAGE_MODEL:'custom-configured',ROUTE_STORYBOARD_IMAGE:'official:custom-selected',STORYBOARD_IMAGE_QUALITY:'high'});await t.open();
 assert.match(t.$('[data-model-card="image"]').textContent,/官方 · custom-configured · 自动画质/);
 const first=await t.save();assert.equal(first.OPENAI_IMAGE_MODEL,'custom-configured');assert.equal(first.ROUTE_STORYBOARD_IMAGE,'official:custom-selected');assert.equal(first.STORYBOARD_IMAGE_QUALITY,'high');
 const advanced=t.$('[data-image-route="camera_image"]');assert.ok(advanced);assert.equal(advanced.querySelectorAll('option').length>=4,true);advanced.value=choices[2][0];await t.event('change',advanced);
 const second=await t.save();assert.equal(second.ROUTE_CAMERA_IMAGE,choices[2][1]);assert.equal(second.CAMERA_IMAGE_QUALITY,'high');assert.equal(second.ROUTE_STORYBOARD_IMAGE,'official:custom-selected');
});
test('frozen previews drive known single prices, unknown fallback, and mixed totals',()=>{
 const t=base(),recs=[{channel:'official',preview:{model:'gpt-image-2',quality:'auto'}},{channel:'official',preview:{model:'gpt-image-2',quality:'high'}},{channel:'official',preview:{model:'gpt-image-2.6-sunburst',quality:'high'}},{channel:'third_party',preview:{model:'gpt-image-2',quality:'high'}}];t.context.records=recs;
 for(const [i,price] of ['0.05','0.20','0.08'].entries())assert.ok(t.run(`imageFee(records[${i}])`).includes('$'+price));
 assert.match(t.run('imageFee(records[3])'),/0.02 点/);
 for(const preview of [{model:'custom',quality:'high'},{model:'gpt-image-2'},{model:'gpt-image-2.6-sunburst',quality:'auto'}]){t.context.unknown={channel:'official',preview};assert.match(t.run('imageFee(unknown)'),/按 OpenAI 计费/);assert.doesNotMatch(t.run('imageFee(unknown)'),/\$/);}
 t.run('STATUS.routes={storyboard_image:{channel:"third_party",model:"other",quality:"auto"}}');assert.match(t.run('imageFee(records[1])'),/gpt-image-2.*高画质.*\$0.20/);
 const mixed=t.run('imageBatchFee(records.concat(records[0]))');assert.match(mixed,/2 张.*gpt-image-2.*自动画质.*\$0.10/);assert.match(mixed,/\$0.38/);assert.match(mixed,/0.02 点/);
 assert.match(t.run(`imageBatchFee([{channel:'third_party',preview:{model:'gpt-image-2',quality:'auto'}},{channel:'third_party',preview:{model:'gpt-image-2',quality:'high'}}])`),/2 张三方渠道，约 0.04 点/);
 const state={kind:'cell',cells:['c01','c02'],rec:recs[1]};t.context.action=state;assert.match(t.run('v5ConfirmationText("d01",action)'),/\$0.20/);
 for(const kind of ['board','cell']){state.kind=kind;t.context.action=state;assert.match(t.run('v5ConfirmationText("d01",action)'),/高画质.*\$0.20/);}
});
test('legacy nonempty image records without channel keep third-party pricing',()=>{
 const t=base();
 assert.equal(t.run(`imageFee({id:'legacy',preview:{model:'gpt-image-2'}})`),'三方渠道，约 0.02 点');
 assert.equal(t.run(`imageBatchFee([{id:'old-one'},{id:'old-two',preview:{model:'gpt-image-2'}}])`),'2 张三方渠道，约 0.04 点');
 assert.equal(t.run('imageFee(null)'),'生成预览后显示费用');
 assert.equal(t.run('imageFee({})'),'生成预览后显示费用');
});
test('batch fission fees use flattened frozen model and quality per project',()=>{
 const t=base();t.context.pending={project_count:2,image_count:3,token:'frozen',items:[{project_id:'a',name:'A',channel:'official',model:'gpt-image-2',quality:'high'},{project_id:'b',name:'B',channel:'official',model:'gpt-image-2.6-sunburst',quality:'high'},{project_id:'b',name:'B',channel:'third_party',model:'gpt-image-2',quality:'auto'}]};
 const text=t.run('batchConfirmationHtml("images",pending,{hidden:{},masterId:"master"})');assert.match(text,/\$0.28/);assert.match(text,/A.*\$0.20.*B.*\$0.08/s);assert.match(text,/0.02 点/);
});
test('camera previews stay confirmable and keep frozen fees after global settings change',()=>{
 const t=base();t.run(`P.images.camera={id:'camera',state:'pending_confirmation',for_task:'scene-camera:S1',channel:'official',preview:{model:'gpt-image-2',quality:'high'}};STATUS.routes={camera_image:{channel:'third_party',model:'different-current-model'}}`);
 assert.equal(t.run('sceneConfirmReason("S1",P.images.camera)'),'', 'global channel changes cannot invalidate an existing preview');
 assert.match(t.run('imageRecordCard(P.images.camera)'),/gpt-image-2.*高画质.*\$0.20/s);
 t.run(`cameraChannels.set(cameraDraftKey('scene','S1'),'third_party');cameraRouteSelected.add(cameraDraftKey('scene','S1'))`);assert.match(t.run('sceneConfirmReason("S1",P.images.camera)'),/渠道已切换/);
 t.run(`delete P.scene_cast;delete P.replacements.scene_plan;P.images.camera.for_task='camera:C1:S1'`);assert.equal(t.run('cameraConfirmReason("C1","S1",P.images.camera)'),'');
 t.run(`cameraChannels.set(cameraDraftKey('C1','S1'),'third_party');cameraRouteSelected.add(cameraDraftKey('C1','S1'))`);assert.match(t.run('cameraConfirmReason("C1","S1",P.images.camera)'),/渠道已切换/);
});
test('plan redraw prepares a frozen image, one confirmation covers the plan, later same-setting images auto-confirm',async()=>{
 const t=require('./test_v6_ui').fixture(),normal=t.context.mockApi;
 t.context.mockApi=async(method,url,body)=>{const result=await normal(method,url,body);if(url.endsWith('/preview')&&url.includes('/storyboard-edits/')){result.channel='third_party';result.preview={model:'gpt-image-2',quality:'auto'};}return result;};t.run('api=mockApi');
 await t.click(t.$('[data-fix-plan]'));t.run(`P.runs.d01[0].fix_plan.redraw.push({cell:'c03',request:'下一个镜头'});renderSteps()`);await t.click(t.$('[data-fix-apply]'));
 const previews=()=>t.requests.filter(r=>r.url.includes('/storyboard-edits/')&&r.url.endsWith('/preview')),confirms=()=>t.requests.filter(r=>r.url.includes('/images/')&&r.url.endsWith('/confirm'));
 assert.equal(previews().length,1,'prepare current image before paid confirmation');assert.equal(confirms().length,0);assert.match(t.$('#segmentBar').textContent,/2 张图，2 张三方渠道，约 0\.04 点/);
 await t.click(t.$('[data-fix-confirm]'));assert.equal(previews().length,1,'confirmation must reuse the frozen record');assert.equal(confirms().length,1);
 t.run(`P.images['image-d01'].state='completed';P.runs.d01[0].fix_plan.completed_cells=['c02'];renderSteps()`);await require('./test_v5_ui').until(()=>previews().length===2);
 await require('./test_v5_ui').until(()=>confirms().length===2);assert.equal(t.$('[data-fix-confirm]'),null,'same frozen setting needs no second confirmation');
});
test('history, cell and whole-board lightboxes show only frozen version metadata',async()=>{
 const t=base();t.run(`P.images.frozen={id:'frozen',channel:'official',preview:{model:'gpt-image-2',quality:'high'}};P.segment_boards.segments.d01.versions[0].image_id='frozen';STATUS.openai_image_model='other-current-model';segmentAdjustments.set(editSheetKey('d01'),true);renderSteps()`);
 assert.match(t.$('[data-detail-key="edit-history:d01"]').textContent,/gpt-image-2 · 高画质/);
 await t.click(t.$('[data-story-preview-cell="c01"][data-story-preview-side="target"]'));assert.match(t.$('.story-preview-header').textContent,/gpt-image-2 · 高画质/);await t.click(t.$('[data-story-preview-close]'));
 await t.click(t.$('[data-story-preview-version]'));assert.match(t.$('.story-preview-header').textContent,/gpt-image-2 · 高画质/);await t.click(t.$('[data-story-preview-close]'));
 t.run(`delete P.images.frozen.preview.quality;renderSteps()`);assert.match(t.$('[data-detail-key="edit-history:d01"]').textContent,/gpt-image-2/);assert.doesNotMatch(t.$('[data-detail-key="edit-history:d01"]').textContent,/自动画质|高画质|other-current-model/);
 t.run(`batchBoardState={data:{master:{id:'m'},batches:[{items:[{project_id:'child',name:'裂变',boards:[{file:'frozen.jpg',model:'gpt-image-2.6-sunburst',quality:'high',channel:'official'}]}]}]}};batchZoom('child',0,null)`);assert.match(t.$('.story-preview-header').textContent,/gpt-image-2.6 sunburst · 高画质/);
});
test('partial redraw cell previews keep each cell original frozen model and quality',async()=>{
 const t=base();t.run(`const original=P.segment_boards.segments.d01.versions[0];P.images={full:{id:'full',channel:'official',preview:{model:'gpt-image-2',quality:'auto'}},single:{id:'single',channel:'official',preview:{model:'gpt-image-2.6-sunburst',quality:'high'},storyboard_context:{cell_id:'c01',base_current:'v1'}},multi:{id:'multi',channel:'official',preview:{model:'gpt-image-2',quality:'high'},storyboard_context:{selected_cells:['c02','c03'],base_current:'v2'}}};
 Object.assign(original,{id:'v1',image_id:'full',from:'full'});P.segment_boards.segments.d01.versions=[original,{...clone(original),id:'v2',image_id:'single',from:'cell:c01',base_version:'v1'},{...clone(original),id:'v3',image_id:'multi',from:'cells:c02,c03',base_version:'v2'}];P.segment_boards.segments.d01.current='v3';renderSteps()`);
 for(const [cell,record] of [['c01','single'],['c02','multi'],['c03','multi'],['c04','full']])assert.equal(t.run(`storyboardPreviewCell('${cell}','target').imageRecord?.id`),record);
 await t.click(t.$('[data-story-preview-cell="c04"][data-story-preview-side="target"]'));assert.match(t.$('.story-preview-header').textContent,/gpt-image-2 · 自动画质/);assert.doesNotMatch(t.$('.story-preview-header').textContent,/高画质/);await t.click(t.$('[data-story-preview-close]'));
 assert.equal(t.run(`storyboardVersionRecord(P.segment_boards.segments.d01.versions[2]).id`),'multi','whole-board and history keep latest edit metadata');
 t.run(`delete P.segment_boards.segments.d01.versions[2].base_version;delete P.images.multi.storyboard_context.base_current`);assert.equal(t.run(`storyboardPreviewCell('c04','target').imageRecord?.id`),'full','legacy versions without a base use the previous version');
 t.run(`P.segment_boards.segments.d01.versions[2].base_version='missing'`);assert.equal(t.run(`storyboardPreviewCell('c04','target').imageRecord`),undefined,'missing explicit parent cannot label the wrong image');
 t.run(`P.segment_boards.segments.d01.versions[2].base_version='v2';P.segment_boards.segments.d01.versions[1].base_version='v3'`);assert.equal(t.run(`storyboardPreviewCell('c04','target').imageRecord`),undefined,'cycles stop without a misleading label');
});
async function main(){let failed=0;for(const [name,fn] of tests)try{await fn();console.log('PASS '+name);}catch(error){failed++;console.error('FAIL '+name+'\n'+error.stack);}assert.equal(failed,0,failed+' V9c UI scenarios failed');}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});module.exports={fixture,main};
