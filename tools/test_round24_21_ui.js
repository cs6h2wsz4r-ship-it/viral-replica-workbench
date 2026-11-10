/* Actual UI renderer, synthetic scene/run data, no browser/network required. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_17_ui.js'),'utf8');
const setup=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('const tests=[];'))+'\nreturn setup;')(require,__dirname);
const t=setup();
assert.equal(t.$('[data-scene-camera="S4"]'),null);
assert.doesNotMatch(t.$('#step-3').textContent,/自动选帧|主镜头图/);
console.log('PASS SB1: 自动取帧入口已从第03步移除');
