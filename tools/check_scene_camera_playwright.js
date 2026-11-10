/* Optional real-browser QA. Uses installed Playwright/Chrome; no installation or workbench server. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
function browserLibrary() {
  try { return require('playwright'); } catch (_) {}
  const cache=path.join(require('node:os').homedir(),'.npm/_npx');
  for (const entry of fs.readdirSync(cache)) {
    const folder=path.join(cache,entry,'node_modules/playwright');
    if(fs.existsSync(folder)) return require(folder);
  }
  throw Error('本机没有 Playwright；不安装依赖');
}
async function main() {
  process.env.TMPDIR=path.join(root,'runtime/temp');
  const {chromium}=browserLibrary();
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    await page.route('**/*',route=>route.abort());
    const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'');
    await page.setContent(html);
    await page.addStyleTag({path:path.join(root,'web/style.css')});
    await page.addScriptTag({content:fs.readFileSync(path.join(root,'web/app.js'),'utf8').replace(/boot\(\)\.catch\([^\n]+\);/,'')});
    const project=JSON.parse(fs.readFileSync(path.join(root,'runtime/temp/scene-camera-ui-project.json'),'utf8'));
    await page.evaluate(project=>{
      P=project;S=clone(project.script);STATUS={};
      document.querySelector('#workspace').hidden=false;
      document.querySelector('#emptyState').hidden=true;
      api=async(method,url)=>{if(method==='GET')return clone(project);throw Error('仅允许 mock 读取');};
      renderReplacements();
    },project);
    assert.equal(await page.locator('.scene-camera').count(),2);
    for(const width of [1280,900,625]) {
      await page.setViewportSize({width,height:900});
      const widths=await page.locator('.camera-pair img').evaluateAll(imgs=>imgs.map(img=>img.getBoundingClientRect().width));
      assert.ok(widths.length>=2 && widths.every(w=>w>=240),JSON.stringify(widths));
      console.log(`PASS ${width}px viewport: comparison image widths ${widths.join(', ')}`);
    }
    await page.locator('[data-camera-key="C1:S2"] details').first().evaluate(el=>el.open=true);
    await page.locator('.camera-pair [data-camera-zoom]').first().click();
    assert.equal(await page.locator('#cameraLightbox').isVisible(),true);
    await page.evaluate(()=>{for(let i=0;i<3;i++){renderReplacements();renderCameraImages();}});
    assert.equal(await page.locator('[data-camera-key="C1:S2"] details').first().evaluate(el=>el.open),true);
    assert.equal(await page.locator('#cameraLightbox').isVisible(),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#cameraLightbox').isVisible(),false);
    await page.locator('.camera-pair [data-camera-zoom]').first().click();
    await page.locator('#cameraLightbox').click({position:{x:5,y:5}});
    assert.equal(await page.locator('#cameraLightbox').isVisible(),false);
    fs.mkdirSync(path.join(root,'output/playwright'),{recursive:true});
    await page.setViewportSize({width:1280,height:900});
    await page.locator('.rep-camera').screenshot({path:path.join(root,'output/playwright/scene-camera-cards.png')});
    console.log('PASS: actual DOM lightbox/Escape/backdrop and unchanged polling; screenshot output/playwright/scene-camera-cards.png');
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
