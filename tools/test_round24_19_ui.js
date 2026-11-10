/* 24.19: actual DOM and delegated events, synthetic projects and mock HTTP only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_18_ui.js'),'utf8');
const base=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('\nconst tests=[]'))+'\nreturn setup;')(require,__dirname);
function setup(){
  const t=base();
  t.run(`globalThis.optionCalls=[];const priorApi=api;
    api=async(method,url,body)=>{
      if(url.endsWith('/assembly-options')){
        optionCalls.push(clone(body));if(globalThis.holdOptions)await holdOptions;
        if(globalThis.failOptions)throw Error('这次没保存成功，请重试');
        project.assembly_options={...body};return clone(project);
      }
      if(url.endsWith('/assemble')){globalThis.assembled=true;return {};}
      return priorApi(method,url,body);
    };renderFinal();`);
  return t;
}
const tests=[],test=(name,fn)=>tests.push([name,fn]);
test('第六步裁剪选项默认勾选并位于拼接按钮上方',()=>{
  const t=setup(),check=t.$('#trimToSource');assert.ok(check);assert.equal(check.checked,true);
  const html=t.$('#finalView').innerHTML;
  assert.match(html,/按原片时长裁剪每段（保持原片节奏）/);
  assert.ok(html.indexOf('trimToSource')<html.indexOf('assembleBtn'));
});
test('取消和重新勾选保存项目，刷新保留选择',async()=>{
  const t=setup();let check=t.$('#trimToSource');check.checked=false;await t.event('change',check);
  assert.equal(t.run('optionCalls.at(-1).trim_to_source'),false);
  assert.equal(t.run('P.assembly_options.trim_to_source'),false);
  t.run('P=clone(project);renderFinal()');assert.equal(t.$('#trimToSource').checked,false);
  check=t.$('#trimToSource');check.checked=true;await t.event('change',check);
  assert.equal(t.run('optionCalls.at(-1).trim_to_source'),true);assert.equal(t.$('#trimToSource').checked,true);
});
test('保存失败恢复原勾选并显示白话错误，保存中不启动拼接',async()=>{
  const t=setup();t.run(`P.script.tasks=[{id:'seg-01',title:'段落'}];S=clone(P.script);renderFinal();
    globalThis.holdOptions=new Promise(resolve=>globalThis.releaseOptions=resolve);globalThis.failOptions=true;`);
  const check=t.$('#trimToSource');check.checked=false;const pending=t.event('change',check);
  await Promise.resolve();assert.equal(t.$('#assembleBtn').disabled,true);
  t.run('releaseOptions()');await pending;
  assert.equal(t.$('#trimToSource').checked,true);assert.match(t.$('#finalView').textContent,/这次没保存成功，请重试/);
  assert.equal(t.$('#assembleBtn').disabled,false);
});
test('第五步仅在目标短于请求时长时显示成片取秒数',()=>{
  const t=setup();t.run(`S.tasks=[{id:'seg-01',title:'短镜头',kind:'broll',mode:'video',source_start:2.75,source_end:3.83,duration:4,target_seconds:1.08,image_ids:[],ratio:'9:16'}];renderTasks(true);`);
  assert.match(t.$('#taskCards .head').textContent,/成片取 1\.08 秒/);
  t.run('S.tasks[0].target_seconds=4;renderTasks(true)');assert.doesNotMatch(t.$('#taskCards .head').textContent,/成片取/);
});
test('旧脚本显示按原片推算的目标，speaker 长台词使用 estimate',()=>{
  const t=setup();t.run(`S.tasks=[{id:'seg-01',title:'旧段落',kind:'speaker',mode:'video',source_start:0,source_end:1.08,duration:4,estimate:2.5,image_ids:[],ratio:'9:16'}];renderTasks(true);`);
  assert.match(t.$('#taskCards .head').textContent,/成片取 2\.50 秒/);
  t.run('S.tasks[0].estimate=2;renderTasks(true)');assert.match(t.$('#taskCards .head').textContent,/成片取 1\.08 秒/);
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}}assert.equal(failed,0,failed+' round24.19 UI scenarios failed');console.log('Round 24.19 UI: '+tests.length+' scenarios passed');})().catch(e=>{console.error(e);process.exitCode=1;});
