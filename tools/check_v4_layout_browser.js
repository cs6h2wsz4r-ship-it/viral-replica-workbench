/* Real-browser V4 QA. Installed Playwright/Chrome only, all traffic intercepted. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),work=path.join(root,'runtime/temp/v4-work');
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
 const {fixture}=require('./test_v4_layout_ui'),t=fixture();
 t.run(`P.id='v4-synthetic';P.name='分镜对照 · 离线示例';P.source.name='参考片.mp4';P.source.probe.duration=9;
 P.segments_plan.summary='保留镜头顺序，按场景变化分成两段；片尾账号页不复刻。';
 P.segments_plan.segments.forEach((s,i)=>{s.title=i?'收尾与反应':'进入场景并展示';s.mode_reason=i?'目标画面已能说明动作':'保留原片的动作节奏和机位';});
 P.breakdown.characters=[{id:'C1',name:'人物 A',appearance:'短发',outfit:'原片服装'}];P.breakdown.productMoments=[{product:'参考商品'}];
 P.replacements.characters[0].description='目标人物参考';P.replacements.product.description='目标商品参考';
 P.assets.forEach(a=>a.label=a.id==='dad'?'人物照片':'商品照片');
 P.segment_boards.segments.d01.plan={sig:'current',summary:'按选角和商品设定替换，保持机位与动作。',cells:P.segments_plan.segments[0].cells.map(cell=>({cell,changes:'替换人物和商品',keep:'机位与场景'})),questions:[]};
 P.segment_boards.segments.d01.versions[0].plan_sig='current';
 P.director.segments.forEach(s=>{const plan=P.segments_plan.segments.find(row=>row.id===s.id);s.title=plan.title;s.mode_reason=plan.mode_reason;s.what='保留镜头动作，呈现目标人物与商品。';s.prompt='图1 是替换后的目标画面，图2 锁定人物外观。视频1 只提供场景、机位与动作节奏。镜头按原顺序衔接，人物和商品外观保持一致。';s.check=['人物外观一致','商品形态正确'];});renderSteps()`);
 const project=JSON.parse(t.run('JSON.stringify(P)'));
 fs.writeFileSync(path.join(work,'synthetic-project.json'),JSON.stringify(project,null,2));
 for(let i=1;i<=9;i++)for(const target of [false,true])fs.writeFileSync(path.join(work,`${target?'target':'source'}-${i}.svg`),frame(i,target));
 const browser=await library().chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const url=route.request().url();requests.push(url);
   if(url.includes('/files/') && /\.(jpg|png|svg)(\?|$)/.test(url)){
    const index=Number(url.match(/(?:cell-|c)(\d+)/)?.[1] || 1),target=/new|v1|dad|watch/.test(url);
    return route.fulfill({contentType:'image/svg+xml',body:frame(index,target)});
   }
   return route.abort();
  });
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'').replace('<head>','<head><base href="http://v4.invalid/">');
  await page.setContent(html);await page.addStyleTag({path:path.join(root,'web/style.css')});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'web/app.js'),'utf8').replace(/boot\(\)\.catch\([^\n]+\);/,'')});
  await page.evaluate(project=>{P=project;S=clone(P.script);STATUS={providers:{}};document.querySelector('#workspace').hidden=false;document.querySelector('#emptyState').hidden=true;api=async(method)=>{if(method!=='GET')throw Error('离线浏览器检查禁止真实变更');return clone(P);};renderAll();},project);
  assert.equal(await page.locator('[data-flow-index]').count(),5);
  assert.equal(await page.locator('#breakdownDetails').evaluate(el=>el.open),false);
  assert.equal(await page.locator('[data-detail-key="source-storyboard"]').evaluate(el=>el.open),false);
  assert.equal(await page.locator('[data-detail-key="edit-plan:d01"]').evaluate(el=>el.open),false);
  const first=page.locator('[data-compare-side="source"] [data-compare-cell="c05"]');
  await first.focus();await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>comparisonSelection().sid),'d02');
  const cell=page.locator('[data-compare-side="target"] [data-compare-cell="c06"]');
  await cell.focus();await page.keyboard.press('Space');assert.equal(await page.evaluate(()=>comparisonSelection().cell),'c06');
  await page.evaluate(()=>{for(let i=0;i<3;i++){P=clone(P);preserveView(()=>renderSteps());}});
  assert.equal(await page.evaluate(()=>comparisonSelection().cell),'c06');
  assert.equal(await cell.evaluate(el=>el===document.activeElement),true);
  await page.keyboard.press('Space');assert.equal(await page.evaluate(()=>comparisonSelection().cell),null);
  await page.locator('[data-compare-side="source"] [data-compare-cell="c01"]').click();
  for(const width of [1440,760]){
   await page.setViewportSize({width,height:1100});
   const boxes=await page.locator('[data-compare-side]').evaluateAll(els=>els.map(el=>({x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y,width:el.getBoundingClientRect().width})));
   if(width>=900){assert.equal(boxes[0].y,boxes[1].y);assert.ok(boxes[1].x>boxes[0].x);}else{assert.equal(boxes[0].x,boxes[1].x);assert.ok(boxes[1].y>boxes[0].y);}
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow');
   await page.evaluate(()=>window.scrollTo(0,0));
   await page.screenshot({path:path.join(work,width>=900?'wide.png':'narrow.png'),fullPage:true});
   await page.locator('.topbar').evaluate(el=>el.style.visibility='hidden');
   await page.locator('#step-4').screenshot({path:path.join(work,width>=900?'wide-comparison.png':'narrow-comparison.png')});
   await page.locator('.topbar').evaluate(el=>el.style.visibility='');
  }
  await page.setViewportSize({width:1440,height:1100});
  await page.locator('[data-compare-side="source"] [data-compare-cell="c05"]').hover();
  assert.equal(await page.locator('[data-segment-outline="d02"].is-hovered').count(),2);
  await page.evaluate(()=>{window.confirmed=null;pageConfirm('共 2 段，合计计费约 16 秒。','批量确认').then(value=>window.confirmed=value);});
  assert.equal(await page.locator('#pageConfirmation').isVisible(),true);
  assert.equal(await page.locator('#app').evaluate(el=>el.inert),true);
  await page.keyboard.press('Tab');assert.equal(await page.locator('[data-page-confirm="accept"]').evaluate(el=>el===document.activeElement),true);
  await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>window.confirmed),false);
  assert.equal(await page.locator('#app').evaluate(el=>el.inert),false);
  assert.deepEqual(errors,[]);
  assert.ok(requests.every(url=>url.startsWith('http://v4.invalid/')),'only synthetic intercepted origins');
  fs.writeFileSync(path.join(work,'browser-results.json'),JSON.stringify({checks:['five steps','collapsed analysis/source/plan/prompt','keyboard Enter/Space','focus and selection preserved on refresh','cross-column hover','1440px two columns','760px stacked','no horizontal overflow','modal focus/Tab/Escape/inert'],requestsIntercepted:requests.length,pageErrors:errors},null,2));
  console.log('PASS V4 browser: keyboard, focus, preserved selection, responsive layout, hover, confirmation; full-page screenshots runtime/temp/v4-work/{wide,narrow}.png');
 }finally{await browser.close();}
}
if(require.main===module)main().catch(e=>{console.error(e);process.exitCode=1;});
module.exports={main};
