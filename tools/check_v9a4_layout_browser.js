/* V9a4 real-browser layout and pointer audit. Existing Chrome/Playwright only.
   Every browser request is intercepted; all project/model endpoints are synthetic. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),work=path.join(root,'runtime/temp/v9a5-work/browser');
function browserLibrary(){try{return require('playwright');}catch{}const cache=path.join(require('node:os').homedir(),'.npm/_npx');for(const entry of fs.readdirSync(cache)){const p=path.join(cache,entry,'node_modules/playwright');if(fs.existsSync(p))return require(p);}throw Error('需要已有 Playwright；此脚本不会安装依赖');}
async function main(){
 fs.mkdirSync(work,{recursive:true});process.env.TMPDIR=work;
 const t=require('./test_v9a4_ui').fixture(),seed=JSON.parse(t.run('JSON.stringify(P)')),view=t.view(),items=t.items;
 const longCN='这是没有空格的极长外部文字用于验证组件边界'.repeat(4).slice(0,80),longError='HTTP 403 rejected by the safety system '+ 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.repeat(8)+' https://example.invalid/'+ 'p'.repeat(100)+' request_id=req_'+ '9'.repeat(80);
 seed.name=longCN;seed.source.name=longCN;seed.breakdown.characters[0].name=longCN;seed.breakdown.characters[0].role=longCN;
 seed.replacements.characters.forEach(c=>{c.description=longCN;});seed.replacements.product.description=longCN;
 seed.storyboard.cells.forEach(c=>{c.action=longCN;c.dialogue=longCN;c.overlay=longCN;c.product=longCN;});
 seed.breakdown.productMoments.forEach(p=>p.product=longCN);
 seed.director.segments.forEach(s=>{s.title=longCN;s.what=longCN;s.prompt=longCN;s.check=[longCN];});
 Object.values(seed.segment_boards.segments).forEach(b=>{b.plan.summary=longCN;b.plan.cells.forEach(c=>{c.changes=longCN;c.keep=longCN;});});
 seed.segments_plan.summary=longCN;seed.segments_plan.segments.forEach(s=>{s.title=longCN;s.mode_reason=longCN;});seed.assets.forEach(a=>a.label=longCN);seed.final_preview_status={ready:true,warnings:[]};
 view.columns.forEach(c=>{c.title=(c.kind==='person'?'人物 · C1 ':'商品 · ')+longCN;c.source_name=c.title;c.source_description=longCN;c.master_name=longCN;c.master_status='母版已换成：'+longCN;});items.forEach(i=>i.name=longCN);
 const browser=await browserLibrary().chromium.launch({channel:'chrome',headless:true});const results=[],requests=[],errors=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',route=>{const u=route.request().url();requests.push(u);if(u.startsWith('http://v9a4.invalid/') && /\/files\/|\/library\/.*\/file/.test(u))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1600"><rect width="900" height="1600" fill="#accbbb"/></svg>'});return route.abort();});
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'').replace('<head>','<head><base href="http://v9a4.invalid/">');
  await page.setContent(html);await page.addStyleTag({path:path.join(root,'web/style.css')});await page.addScriptTag({content:fs.readFileSync(path.join(root,'web/app.js'),'utf8').replace(/boot\(\)\.catch\([^\n]+\);/,'')});
  await page.evaluate(({seed,view,items,longError})=>{
   window.qa={seed,view,items,longError,requests:[],settings:{OPENAI_API_KEY:'configured',JIMMY_API_KEY:'configured',OPENAI_IMAGE_MODEL:'gpt-image-2',ROUTE_STORYBOARD_IMAGE:'official:gpt-image-2',ROUTE_CAMERA_IMAGE:'official:gpt-image-2',STORYBOARD_IMAGE_QUALITY:'auto',CAMERA_IMAGE_QUALITY:'auto'}};P=clone(seed);S=clone(P.script);STATUS={providers:{official:{ready:true}},routes:{}};document.querySelector('#workspace').hidden=false;document.querySelector('#emptyState').hidden=true;
   projectList=[{id:P.id,name:P.name}];renderProjectOptions();
   api=async(method,url,body)=>{qa.requests.push({method,url,body});if(url==='settings' && method==='GET')return clone(qa.settings);if(url==='library')return {items:clone(qa.items)};if(url.endsWith('/fission-draft'))return clone(qa.view);if(url==='projects')return clone(projectList);if(url.startsWith('batches/'))return clone(fissionState.batches[0]);if(method==='GET')return clone(P);throw Error('Unexpected synthetic write: '+url);};renderAll();
  },{seed,view,items,longError});
  async function check(name,width){
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const result=await page.evaluate(()=>{
    const width=document.documentElement.clientWidth,panels='.panel,.fission-panel,.fission-picker,.page-confirmation-card,.topbar,#selectedSegment,#segmentBar,.cast-card,.library-item,#storyboardLightbox,.story-preview-header,.story-preview-footer,#settingsDialog,.settings-model-card',bad=[];
    function visible(el){for(let a=el.parentElement;a;a=a.parentElement){if(a.tagName==='DETAILS' && !a.open && !a.querySelector(':scope > summary')?.contains(el))return false;}const s=getComputedStyle(el),r=el.getBoundingClientRect();return el.getClientRects().length && r.width && r.height && s.visibility!=='hidden' && Number(s.opacity)!==0;}
    for(const el of document.querySelectorAll('main *, .topbar *, #fissionPanel *, #libraryPanel *, #pageConfirmation *, #storyboardLightbox *, #settingsDialog *')){
     if(!visible(el) || el.closest('svg') || el.closest('[hidden]'))continue;
     const panel=el.parentElement?.closest(panels);if(!panel || !visible(panel))continue;
     let r=el.getBoundingClientRect(),left=r.left,right=r.right;
     for(let ancestor=el.parentElement;ancestor && ancestor!==panel;ancestor=ancestor.parentElement){const style=getComputedStyle(ancestor);if(['hidden','clip','auto','scroll'].includes(style.overflowX)){const clip=ancestor.getBoundingClientRect();left=Math.max(left,clip.left);right=Math.min(right,clip.right);}}
     const b=panel.getBoundingClientRect();if(left<b.left-2 || right>b.right+2)bad.push({tag:el.tagName,cls:el.className,id:el.id,text:el.textContent.slice(0,45),left,right,panel:panel.id||panel.className,bounds:[b.left,b.right]});
    }
    const overlaps=[];
    for(const selector of ['.topbar','#segmentBar']){const box=document.querySelector(selector);if(!box || !visible(box))continue;const buttons=[...box.querySelectorAll('button,select')].filter(visible);for(const [i,a] of buttons.entries()){const r=a.getBoundingClientRect();if(r.left<-.5 || r.right>width+.5)overlaps.push({type:'outside',text:a.textContent.slice(0,40)});for(const b of buttons.slice(i+1)){const q=b.getBoundingClientRect();if(Math.min(r.right,q.right)-Math.max(r.left,q.left)>1 && Math.min(r.bottom,q.bottom)-Math.max(r.top,q.top)>1)overlaps.push({type:'overlap',a:a.textContent.slice(0,40),b:b.textContent.slice(0,40)});}}}
    return {scrollWidth:document.documentElement.scrollWidth,clientWidth:width,bad,overlaps};
   });
   results.push({name,width,...result});fs.writeFileSync(path.join(work,'layout-results.json'),JSON.stringify(results,null,2));
   assert.ok(result.scrollWidth<=result.clientWidth,`${width} ${name}: page overflow ${result.scrollWidth}`);assert.deepEqual(result.bad,[],`${width} ${name}: panel bounds`);assert.deepEqual(result.overlaps,[],`${width} ${name}: sticky controls`);
  }
  for(const width of [1440,1280,390]){
   await page.setViewportSize({width,height:1000});
   await page.evaluate(()=>openSettings());await check('settings-four-image-options',width);
   assert.equal(await page.locator('[data-model-card="image"] option').count(),4);
   await page.selectOption('[data-model-card="image"]','official:gpt-image-2|high');await check('settings-high-quality',width);
   await page.locator('#settingsAdvanced > summary').click();await page.selectOption('[data-image-route="camera_image"]','official:gpt-image-2.6-sunburst|high');await check('settings-independent-image-routes',width);
   assert.equal(await page.locator('[name="ROUTE_STORYBOARD_IMAGE"]').inputValue(),'official:gpt-image-2');assert.equal(await page.locator('[name="ROUTE_CAMERA_IMAGE"]').inputValue(),'official:gpt-image-2.6-sunburst');
   const customModel='custom-image-'+('long-model-id-'.repeat(7));await page.locator('[name="OPENAI_IMAGE_MODEL"]').fill(customModel);await page.locator('[name="OPENAI_IMAGE_MODEL"]').dispatchEvent('change');
   assert.equal(await page.locator('[data-model-card="image"]').inputValue(),'official:'+customModel+'|high');
   assert.equal(await page.locator('[name="ROUTE_STORYBOARD_IMAGE"]').inputValue(),'official:'+customModel);assert.equal(await page.locator('[name="ROUTE_CAMERA_IMAGE"]').inputValue(),'official:gpt-image-2.6-sunburst');
   assert.match(await page.locator('[data-card-key="image"]').textContent(),/同一个 官方 OpenAI Key/);assert.equal(await page.locator('[name="_API_KEY"]').count(),0);await check('settings-custom-model-edit',width);
   await page.screenshot({path:path.join(work,`settings-${width}.png`)});await page.evaluate(()=>document.querySelector('#settingsDialog').close());
   await page.evaluate(()=>{P=clone(qa.seed);S=clone(P.script);fissionState=null;libraryState=null;v5Actions.clear();comparisonSelections.clear();for(let n=1;n<=3;n++)completedStepViews.set(`${P.id}:${n}`,true);renderAll();renderFission();renderLibrary();});
   await check('five-expanded-steps',width);
   await page.evaluate(()=>{P.derived_from={project_id:'master',master_snapshot:{name:qa.seed.name,boards:{d01:{}}},changes:[{key:'C1',to_name:qa.seed.name}]};P.master_exists=true;renderAll();});await check('fission-project-banner',width);
   await page.evaluate(()=>{P.master_exists=false;renderAll();});await check('deleted-master-banner',width);
   await page.evaluate(()=>{delete P.derived_from;delete P.master_exists;newStoryboardErrors.set(editSheetKey('d01'),qa.seed.name);renderAll();});await check('long-chinese-error',width);assert.ok(await page.locator('#segmentBar .error-details').count());
   await page.evaluate(()=>{newStoryboardErrors.clear();renderSteps();});

   await page.evaluate(()=>{segmentAdjustments.set(editSheetKey('d01'),true);renderSteps();});await check('board-advanced',width);
   await page.evaluate(()=>{P.images.failure={id:'failure',state:'failed',channel:'official',error:qa.longError,storyboard_context:{sheet_id:'d01'}};renderSteps();});await check('image-safety-failure',width);
   assert.equal(await page.locator('#segmentBar .error-details').count(),1);assert.equal(await page.locator('#segmentBar .error-details').evaluate(el=>el.open),false);
   await page.locator('#segmentBar .error-details > summary').click();await check('expanded-error-details',width);
   await page.evaluate(()=>{selectComparisonCell('c01','target');selectComparisonCell('c02','target',{shiftKey:true});const rec={id:'qa-multi',state:'pending_confirmation',channel:'official',storyboard_context:{sheet_id:'d01',selected_cells:['c01','c02'],cells_revision:{why:'调整'+qa.seed.name,cells:[{cell:'c01',changes:qa.seed.name,keep:qa.seed.name}]}}};const state={kind:'cell',cell:'c01',cells:['c01','c02'],request:'调整光线',rec,status:'confirming',binding:v5Binding('cell','d01','c01',['c01','c02'])};v5Actions.set(editSheetKey('d01'),state);renderSteps();});await check('multi-paid-confirmation',width);
   await page.evaluate(()=>{v5Actions.clear();clearComparisonCells();P.runs.d01=[{rev:'r1',state:'failed',error:qa.longError}];v5View('d01').view='video';renderComparison();});await check('video-failure',width);
   await page.evaluate(()=>{P.runs.d01=[{rev:'r1',state:'completed',result:'result.mp4'}];v5View('d01').view='video';renderComparison();});await check('video-comparison',width);
   await page.evaluate(()=>{P.runs={};P.final_preview_status={ready:true,warnings:[]};});await page.evaluate(()=>openFission());await check('fission-selection',width);
   await page.evaluate(()=>{fissionOpenPicker('C1');});await check('fission-photo-picker',width);
   await page.evaluate(()=>{fissionState.picker=null;fissionState.showProgress=true;fissionState.batches=[{id:'b',items:[{project_id:'child',row:1,status:'preparing',slots:[{key:'C1',name:qa.seed.name,source_name:'C1 '+qa.seed.name,file:'photo.png',changed:true},{key:'product',name:qa.seed.name,file:'product.png',changed:false}]}]}];renderFission();});await check('fission-created',width);
   if(width===390)await page.screenshot({path:path.join(work,'created-390.png')});
   await page.evaluate(()=>{fissionState.batches[0].items[0].status='failed';fissionState.batches[0].items[0].error=qa.longError;renderFission();});await check('fission-failed',width);
   await page.evaluate(()=>closeFission());await page.evaluate(()=>openLibrary());await check('library',width);
   await page.evaluate(()=>{libraryState.error=qa.longError;renderLibrary();});await check('upload-error',width);
   await page.evaluate(()=>{closeLibrary();finalPreviewErrors.set(P.id,qa.longError);renderSteps();});await check('final-preview-failure',width);
   await page.evaluate(()=>{pageConfirm(qa.seed.name+' '+qa.longError,'确认');});await check('confirmation-dialog',width);await page.evaluate(()=>finishPageConfirmation(false));
   // Empty user/external labels must remain valid layout.
   await page.evaluate(()=>{P.name='';P.source.name='';P.breakdown.characters[0].name='';P.segments_plan.segments.forEach(s=>{s.title='';s.mode_reason='';});renderAll();});await check('empty-labels',width);
  }
  // V9a5 fullscreen layouts use only intercepted original-resolution images.
  for(const width of [1440,1280,390]){
   await page.setViewportSize({width,height:800});
   await page.evaluate(()=>{P=clone(qa.seed);S=clone(P.script);comparisonSelections.clear();v5Actions.clear();comparisonViews.clear();renderAll();});
   const callsBefore=await page.evaluate(()=>qa.requests.length);
   const cell=page.locator('[data-compare-column="target"][data-compare-cell="c02"]');await cell.scrollIntoViewIfNeeded();
   await page.evaluate(()=>{selectComparisonCell('c01','target');selectComparisonCell('c02','target',{shiftKey:true});});
   const draft=page.locator('[data-edit-cell-request]');await draft.fill('双击前未提交的改法');
   const selectionBefore=await page.evaluate(()=>JSON.stringify(comparisonSelection()));
   await cell.dblclick();await page.locator('#storyboardLightbox').waitFor({state:'visible'});await page.locator('[data-story-preview-image]').evaluate(img=>img.decode());
   assert.equal(await page.evaluate(()=>JSON.stringify(comparisonSelection())),selectionBefore,'double click restores multi-selection');
   assert.equal(await draft.inputValue(),'双击前未提交的改法');
   await check('preview-target-fit',width);
   if(width===1280){const b=await page.locator('[data-story-preview-image]').boundingBox();assert.ok(b.height>=700 && b.height<=725,'portrait uses about 90% of 800px viewport');}
   await page.locator('[data-story-preview-mode="pair"]').click();await page.locator('[data-story-preview-image]').first().evaluate(img=>img.decode());await check('preview-paired-fit',width);
   const heights=await page.locator('[data-story-preview-image]').evaluateAll(imgs=>imgs.map(i=>i.getBoundingClientRect().height));assert.ok(Math.abs(heights[0]-heights[1])<1,'paired images have equal height');
   await page.locator('[data-story-preview-image]').last().click();assert.equal(await page.locator('#storyboardLightbox').getAttribute('data-zoom'),'actual');await check('preview-paired-100',width);
   const pane=await page.locator('[data-story-preview-pane="target"]').boundingBox();await page.mouse.move(pane.x+pane.width/2,pane.y+pane.height/2);await page.mouse.wheel(40,120);
   const moved=await page.evaluate(()=>({x:storyboardLightbox.x,y:storyboardLightbox.y}));assert.ok(moved.y!==0,'wheel pans the image');
   const transforms=await page.locator('[data-story-preview-image]').evaluateAll(imgs=>imgs.map(i=>i.style.transform));assert.equal(transforms[0],transforms[1],'paired pan remains synchronized');
   await page.mouse.down();await page.mouse.move(pane.x+pane.width/2+20,pane.y+pane.height/2+25,{steps:4});await page.mouse.up();assert.equal(await page.locator('#storyboardLightbox').getAttribute('data-zoom'),'actual','drag must not toggle zoom');const dragged=await page.evaluate(()=>({x:storyboardLightbox.x,y:storyboardLightbox.y}));assert.ok(dragged.x!==moved.x || dragged.y!==moved.y,'pointer drag pans');
   await page.keyboard.press('ArrowRight');assert.match(await page.locator('#storyboardLightboxTitle').textContent(),/第\s*3\s*镜/);
   await page.keyboard.press('Escape');assert.equal(await cell.evaluate(el=>document.activeElement===el),true,'focus returns to original grid cell');
   assert.equal(await draft.inputValue(),'双击前未提交的改法');
   await page.locator('.comparison-preview-cell [data-story-preview-cell="c04"][data-story-preview-side="source"]').click();await check('preview-source-fit',width);
   await page.keyboard.press('ArrowRight');assert.match(await page.locator('#storyboardLightboxTitle').textContent(),/第\s*2\s*段/);await page.locator('[data-story-preview-close]').click();
   await page.evaluate(()=>{P.segment_boards.segments.d02.versions=[];renderSteps();});
   await page.locator('.comparison-preview-cell [data-story-preview-cell="c05"][data-story-preview-side="source"]').click();assert.equal(await page.locator('[data-story-preview-mode="pair"]').isDisabled(),true);assert.match(await page.locator('#storyPreviewMissing').textContent(),/这一镜还没有新分镜/);await check('preview-missing-counterpart',width);
   await page.screenshot({path:path.join(work,`preview-${width}.png`)});await page.locator('[data-story-preview-close]').click();
   await page.locator('.comparison-preview-cell [data-story-preview-cell="c05"][data-story-preview-side="source"]').click();await check('preview-reopen-same-cached-image',width);
   const reopened=await page.locator('[data-story-preview-image]').boundingBox(),frame=await page.locator('[data-story-preview-pane]').boundingBox();assert.ok(reopened.height<=frame.height+1 && reopened.width<=frame.width+1,'reopening cached original still fits actual pane');await page.keyboard.press('Escape');
   await page.evaluate(()=>{P.images.busy={state:'running',storyboard_context:{sheet_id:'d01',cell_id:'c02'}};renderSteps();});
   const inspectBusy=page.locator('.comparison-preview-cell [data-story-preview-cell="c02"][data-story-preview-side="target"]');await inspectBusy.click();await page.keyboard.press('Escape');assert.equal(await inspectBusy.evaluate(el=>document.activeElement===el || el.closest('.comparison-preview-cell')===document.activeElement),true,'available busy image restores focus to a usable control');
   await page.evaluate(()=>{P.images={};renderSteps();});

   assert.equal(await page.evaluate(()=>qa.requests.length),callsBefore,'preview never requests API work');
  }
  // Real pointer behavior: left drag, 5px click threshold, same-segment limit,
  // modifiers, disabled cells, native-image drag prevention and Esc/blank clear.
  await page.setViewportSize({width:1440,height:1100});await page.evaluate(()=>{P=clone(qa.seed);P.name='框选验证';P.segments_plan.segments.forEach(s=>s.title='选镜验证');comparisonSelections.clear();v5Actions.clear();comparisonViews.clear();renderAll();});
  const target=id=>page.locator(`[data-compare-column="target"][data-compare-cell="${id}"]`);
  await target('c01').scrollIntoViewIfNeeded();
  const box=await target('c01').boundingBox();await page.mouse.move(box.x+10,box.y+10);await page.mouse.down();await page.mouse.move(box.x+12,box.y+11);await page.mouse.up();assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),['c01']);
  await target('c02').click({modifiers:['Shift']});await target('c04').click({modifiers:['Meta']});assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),['c01','c02','c04']);
  await page.keyboard.press('Escape');assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),[]);
  await target('c01').scrollIntoViewIfNeeded();const first=await target('c01').boundingBox(),second=await target('c02').boundingBox();await page.mouse.move(first.x+8,first.y+10);await page.mouse.down();await page.mouse.move(second.x+second.width-8,second.y+second.height-12,{steps:6});assert.equal(await page.locator('.comparison-marquee').count(),1);await page.mouse.up();assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),['c01','c02']);
  await page.keyboard.press('Escape');await target('c04').scrollIntoViewIfNeeded();const fourth=await target('c04').boundingBox(),fifth=await target('c05').boundingBox();await page.mouse.move(fourth.x+8,fourth.y+12);await page.mouse.down();await page.mouse.move(fifth.x+fifth.width-8,fifth.y+fifth.height-12,{steps:6});await page.mouse.up();assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),['c04']);assert.match(await page.locator('[data-compare-selection-hint]').textContent(),/一次只能选同一段/);
  await page.keyboard.press('Escape');await target('c01').scrollIntoViewIfNeeded();const escBox=await target('c01').boundingBox();await page.mouse.move(escBox.x+8,escBox.y+12);await page.mouse.down();await page.mouse.move(escBox.x+40,escBox.y+60);await page.keyboard.press('Escape');assert.equal(await page.locator('.comparison-marquee').count(),0);await page.mouse.up();assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),[]);
  assert.equal(await target('c09').isDisabled(),true);assert.equal(await target('c01').locator('img').getAttribute('draggable'),'false');
  await page.locator('#step-4 > header h2').click();assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),[]);
  await page.evaluate(()=>{P.images.busy={state:'running',storyboard_context:{sheet_id:'d01',cell_id:'c02'}};renderSteps();});assert.equal(await target('c02').isDisabled(),true);
  await page.evaluate(()=>{P.images={};P.segment_boards.segments.d02.versions=[];renderSteps();});assert.equal(await target('c05').isDisabled(),true);
  // Simulate the browser's coarse pointer media query and use real clicks.
  await page.evaluate(()=>{window.matchMedia=q=>({matches:q==='(hover: none)'});renderSteps();});await target('c01').click();await page.locator('[data-compare-touch-add]').click();await target('c02').click();assert.deepEqual(await page.evaluate(()=>comparisonSelectedCells()),['c01','c02']);
  assert.equal(await page.evaluate(()=>qa.requests.filter(r=>r.url.endsWith('/confirm')).length),0);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(work,'summary.json'),JSON.stringify({checks:results.length,widths:[1440,1280,390],pointerChecks:11,previewWidthChecks:18,requestsIntercepted:requests.length,pageErrors:errors,realModelRequests:0},null,2));
  console.log(`PASS V9a5 browser: ${results.length} layouts, three widths, pointer/multi-select/confirmation checks; ${requests.length} intercepted requests, no model calls.`);
 }finally{await browser.close();}
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});module.exports={main};
