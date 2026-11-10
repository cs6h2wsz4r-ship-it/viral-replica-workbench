/* Real-browser V6 QA. Installed Playwright/Chrome only, all traffic intercepted. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),work=path.join(root,'runtime/temp/v6-work');
function library(){
 try{return require('playwright');}catch{}
 const cache=path.join(require('node:os').homedir(),'.npm/_npx');
 for(const entry of fs.readdirSync(cache)){const p=path.join(cache,entry,'node_modules/playwright');if(fs.existsSync(p))return require(p);}
 throw Error('未安装 Playwright；不会安装依赖');
}
function frame(index,target=false){
 const coat=target?'#346e5d':'#b68b6c',x=95+(index%3)*22;
 return `<svg xmlns="http://www.w3.org/2000/svg" width="270" height="480" viewBox="0 0 270 480"><rect width="270" height="480" fill="${target?'#d7e7df':'#ece1d2'}"/><rect x="15" y="38" width="90" height="176" rx="4" fill="#fff9"/><path d="M60 38V214M15 126H105" stroke="#b6c5bb" stroke-width="5"/><path d="M0 340H270V480H0Z" fill="${target?'#b7ccc0':'#d0bda6'}"/><ellipse cx="145" cy="432" rx="75" ry="13" fill="#0001"/><path d="M${x-25} 320l-15 106m50-106 17 106" stroke="#485655" stroke-width="22" stroke-linecap="round"/><rect x="${x-42}" y="182" width="84" height="152" rx="29" fill="${coat}"/><circle cx="${x}" cy="151" r="33" fill="#d9b597"/><path d="M${x-33} 149q0-53 61-18l4 18q-22-23-65 0" fill="${target?'#303d38':'#746d61'}"/><path d="M${x-31} 218l-32 61m94-61 35 55" stroke="${coat}" stroke-width="20" stroke-linecap="round"/><rect x="${x+45}" y="255" width="25" height="47" rx="6" fill="${target?'#244c3c':'#797969'}"/><text x="16" y="459" font-family="sans-serif" font-size="13" fill="#394a40">${target?'TARGET':'SOURCE'} / ${String(index).padStart(2,'0')}</text></svg>`;
}
async function main(){
 fs.mkdirSync(work,{recursive:true});process.env.TMPDIR=work;
 const t=require('./test_v6_ui').fixture();
 const project=JSON.parse(t.run('JSON.stringify(P)'));
 project.id='v6-synthetic';project.name='视频检查 · 合成数据';project.source.name='reference.mp4';
 project.segments_plan.segments.forEach((s,i)=>s.title=i?'衔接与收尾':'入镜与展示');
 project.director.segments.forEach((s,i)=>{s.title=project.segments_plan.segments[i].title;s.what='人物进入画面，展示目标商品，镜头保持连贯。';});
 const browser=await library().chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const url=route.request().url();requests.push(url);
   if(url.includes('/files/') && /\.(jpg|png|svg)(\?|$)/.test(url))return route.fulfill({contentType:'image/svg+xml',body:frame(Number(url.match(/(?:cell-|c)(\d+)/)?.[1]||1),/new|v1|dad|watch/.test(url))});
   return route.abort();
  });
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'').replace('<head>','<head><base href="http://v6.invalid/">');
  await page.setContent(html);await page.addStyleTag({path:path.join(root,'web/style.css')});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'web/app.js'),'utf8').replace(/boot\(\)\.catch\([^\n]+\);/,'')});
  await page.evaluate(project=>{P=project;S=clone(P.script);STATUS={providers:{}};document.querySelector('#workspace').hidden=false;document.querySelector('#emptyState').hidden=true;api=async()=>{throw Error('离线截图禁止请求');};renderAll();},project);
  const decorate=async()=>{await page.evaluate(posters=>{document.querySelectorAll('video').forEach(v=>v.poster=v.closest('[data-video-side="source"]')?posters[0]:posters[1]);},[false,true].map(target=>'data:image/svg+xml;base64,'+Buffer.from(frame(1,target)).toString('base64')));};
  // Freeze sticky chrome only for full-section capture; browser QA uses live CSS.
  const snap=async(name,selector='#step-4')=>{await decorate();const captureStyle=await page.addStyleTag({content:'.topbar{visibility:hidden!important}#segmentBar{position:static!important}'});await page.locator(selector).screenshot({path:path.join(work,name+'.png')});await captureStyle.evaluate(el=>el.remove());};
  await page.evaluate(()=>{delete P.runs.d01[0].review;renderSteps();});await snap('unchecked');
  await page.evaluate(review=>{P.runs.d01[0].review=review;renderSteps();},project.runs.d01[0].review);await snap('issues');
  await page.locator('[data-review-note]').fill('商品切换后仍需保持外观一致。');
  assert.equal(await page.locator('[data-review-note]').inputValue(),'商品切换后仍需保持外观一致。');
  await page.evaluate(()=>{const r=P.runs.d01[0],d=reviewDraft('d01',r);d.invalid=false;r.fix_plan={id:'synthetic-plan',status:'ready',issues:[0],note:d.note,redraw:[{cell:'c02',why:'这一镜中目标人物没有完整入镜。',request:'让目标人物明确入镜，保留商品、构图和动作。'}],prompt:'目标人物完整入镜。保留动作和机位。',why:'把缺失的目标外观补进分镜，再调整对应句子。',prompt_changes:[{before:'人物局部入镜。',after:'目标人物完整入镜，服装与参考照片一致。'}],base_board:editSheetState('d01').current,expected_board:editSheetState('d01').current,video_seconds:12,completed_cells:[]};renderSteps();});await snap('fix-plan');
  assert.equal(await page.locator('[data-fix-plan-view] .fix-step').count(),3);
  await page.locator('[data-fix-apply]').click();assert.match(await page.locator('#segmentBar').innerText(),/按方案重画第 2 镜：1 张图/);
  await snap('redraw-confirm');await page.locator('[data-fix-cancel]').click();
  await page.evaluate(()=>{P.runs.d01[0].fix_plan.status='ready_video';P.runs.d01[0].fix_plan.completed_cells=['c02'];v5View('d01').view='board';renderSteps();});await snap('redrawn');
  await page.evaluate(()=>{P.runs.d01[0].review.accepted={at:'synthetic'};P.runs.d02=[{rev:'r1',state:'completed',result:'second.mp4'}];P.director.segments[1].selected_run='r1';P.final_preview={file:'synthetic-final.mp4',duration:12,segments:[{id:'d01',rev:'r1'},{id:'d02',rev:'r1'}],check:{status:'completed',verdict:'fix',summary:'一处接缝需要处理',issues:[{start:5.1,end:6.1,level:'high',segment:'d02',what:'前一段商品处于关闭状态，下一段突然打开，缺少动作衔接。'}]}};renderSteps();});await snap('final-check','#step-5');
  for(const width of [1440,760,390]){await page.setViewportSize({width,height:1100});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow at '+width);}
  await snap('mobile-final','#step-5');assert.deepEqual(errors,[]);
  assert.ok(requests.every(url=>url.startsWith('http://v6.invalid/')));
  fs.writeFileSync(path.join(work,'browser-results.json'),JSON.stringify({pageErrors:errors,requestsIntercepted:requests.length,checks:['manual cards','typing retained','three plan steps','inline redraw confirmation','redrawn pause','final card','1440/760/390 no horizontal overflow'],screenshots:['unchecked','issues','fix-plan','redraw-confirm','redrawn','final-check','mobile-final']},null,2));
  console.log('PASS V6 synthetic browser QA and screenshots');
 }finally{await browser.close();}
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
