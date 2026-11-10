/* Synthetic scene editor, real handlers/DOM patching, no browser/network service. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_17_ui.js'),'utf8');
const setup=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('const tests=[];'))+'\nreturn setup;')(require,__dirname);
const tests=[],test=(name,fn)=>tests.push([name,fn]);
const card=t=>t.$('[data-scene-camera="S4"]');
function viewport(t){
  t.run(`globalThis.window={scrollX:0,scrollY:2100,innerWidth:1400,innerHeight:900,
    scrollTo({left,top}){this.scrollX=left;this.scrollY=top;}};
    document.anchor=document.querySelector('[data-scene-camera="S4"]');
    Object.getPrototypeOf(document.body).getBoundingClientRect=function(){
      let docTop=100;
      const card=this.closest('[data-scene-camera]');
      if(this.closest('.scene-photo-picker') || this.closest('#cameraLightbox'))docTop=window.scrollY+180;
      else if(card){
        const cards=[...document.querySelectorAll('[data-scene-camera]')];docTop=400;
        for(const prior of cards){if(prior===card)break;docTop+=500+prior.querySelectorAll('img').length*40;}
        if(this!==card)docTop+=120;
      }else if(this.closest('.scene-casting'))docTop=200;
      return {top:docTop-window.scrollY,bottom:docTop-window.scrollY+180,height:180};
    };`);
}
test('统一用量只显示本次总 token，缺 total 时兼容两种输入输出字段',()=>{
  const t=setup();for(const usage of [{input_tokens:4380,output_tokens:158,total_tokens:4538},{input_tokens:4380,output_tokens:158},{prompt_tokens:4380,completion_tokens:158}]){
    assert.match(t.run(`imageUsage({usage:${JSON.stringify(usage)}})`),/本次用了约 4538 个 token/);
    assert.doesNotMatch(t.run(`imageUsage({usage:${JSON.stringify(usage)}})`),/input_tokens|output_tokens|total_tokens|用量：/);
  }
  assert.match(t.run('imageUsage({usage:{total_tokens:0}})'),/约 0 个 token/);
  assert.match(t.run('imageUsage({usage:{}})'),/未提供/);
});
test('脚本时间也显示两位，未修改的精确原片边界不因显示舍入而被改写',()=>{
  const t=setup();t.run(`S.tasks=[{id:'seg-01',kind:'broll',mode:'video',source_start:5.026,source_end:9.999,duration:5,padding:0.123,image_ids:[],story:'保留动作'}];
    document.querySelector('#scriptView').innerHTML=segEditor(S.tasks[0],0);readSegEditors();`);
  assert.equal(t.$('[data-k="source_start"]').value,'5.03');
  assert.equal(t.run('S.tasks[0].source_start'),5.026);
  assert.equal(t.run('S.tasks[0].source_end'),9.999);
  const input=t.$('[data-k="source_start"]');input.value='5.74999999999999';t.run('readSegEditors()');assert.equal(t.run('S.tasks[0].source_start'),5.75);
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}}assert.equal(failed,0,`${failed} round24.17c scenarios failed`);console.log(`Round 24.17c UI: ${tests.length} scenarios passed`);})().catch(e=>{console.error(e);process.exitCode=1;});
