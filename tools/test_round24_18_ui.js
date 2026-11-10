/* 24.18: real DOM/events with synthetic server responses, no real projects/network. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_17_ui.js'),'utf8');
const base=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('const tests=[];'))+'\nreturn setup;')(require,__dirname);
function setup(){
  const t=base();
  t.run(`globalThis.confirmations=[];confirm=text=>{confirmations.push(text);return !globalThis.cancelEdit;};
    globalThis.editCalls=[];globalThis.savedScenes=null;
    const previousApi=api;
    api=async(method,url,body)=>{
      if(!url.endsWith('/scene-edits'))return previousApi(method,url,body);
      editCalls.push(clone(body));if(globalThis.holdEdit)await holdEdit;
      if(globalThis.failEdit)throw Error('这次没保存成功，请重试');
      const next=clone(project);next.replacements=clone(P.replacements);
      next.scene_editor_scenes=clone(P.scene_editor_scenes || P.breakdown.scene);
      const i=next.scene_editor_scenes.findIndex(s=>s.id===body.scene_id),row=next.scene_editor_scenes[i];
      if(body.action==='merge'){
        savedScenes=clone(next);const other=next.scene_editor_scenes[i+1];
        row.name+=' + '+other.name;row.timeRanges=[[row.timeRanges[0][0],other.timeRanges.at(-1)[1]]];row.merged_from=[...(row.merged_from || [row.id]),...(other.merged_from || [other.id])];
        next.scene_editor_scenes.splice(i+1,1);next.scene_cast[row.id].name=row.name;next.scene_cast[row.id].time_ranges=row.timeRanges;next.scene_cast[row.id].merged_from=row.merged_from;
        next.scene_cast[row.id].camera_asset=null;next.scene_cast[row.id].camera_candidates=[];delete next.scene_cast[other.id];
        next.replacements.scene_edits={merges:[row.merged_from],dropped:[]};
      }else if(body.action==='split'){
        next.scene_editor_scenes=clone(savedScenes.scene_editor_scenes);next.scene_cast=clone(savedScenes.scene_cast);next.replacements=clone(savedScenes.replacements);
      }else{
        row.dropped=body.action==='drop';next.replacements.scene_edits={merges:[],dropped:next.scene_editor_scenes.filter(s=>s.dropped).map(s=>s.id)};
      }
      next.effective_scenes=next.scene_editor_scenes.filter(s=>!s.dropped);
      Object.assign(project,clone(next));return next;
    };`);
  return t;
}
const tests=[],test=(name,fn)=>tests.push([name,fn]);
const card=(t,sid)=>t.$('[data-scene-camera="'+sid+'"]');
test('SB1 不再显示分场合并和剪掉，旧脚本过期仍在旧流程提示',()=>{
 const t=setup();assert.equal(t.$('[data-scene-merge]'),null);assert.equal(t.$('[data-scene-drop]'),null);
 t.run('P.scene_edits_script_stale=true;renderFinal()');assert.match(t.$('#finalView').textContent,/替换设定在生成脚本之后改过/);
});
(async()=>{let failed=0;for(const [name,fn] of tests){try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}}assert.equal(failed,0,failed+' round24.18 UI scenarios failed');console.log('Round 24.18 UI: '+tests.length+' scenarios passed');})().catch(e=>{console.error(e);process.exitCode=1;});
