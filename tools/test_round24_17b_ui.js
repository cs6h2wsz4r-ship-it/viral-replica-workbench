/* Reuse the self-made 24.17 HTTP/DOM fixture; run the actual editor handlers. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_17_ui.js'),'utf8');
const setup=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('const tests=[];'))+'\nreturn setup;')(require,__dirname);
const tests=[];
(() => {
  const t=setup();
  assert.equal(t.$('[data-scene-camera]'),null);
  assert.equal(t.$('[data-camera-key]'),null);
  assert.equal(t.$('[data-camdraft]'),null);
  assert.doesNotMatch(t.$('#step-3').textContent,/分场表|主镜头图/);
  assert.ok(t.$('[data-cast-card]'));
  console.log('PASS SB1: 第03步只做选角和商品设定，旧分场与主镜头图不再出现');
})();
