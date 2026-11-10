/* Actual renderer/events with synthetic scene data; no network/browser calls. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const fixture=fs.readFileSync(path.join(__dirname,'test_round24_17_ui.js'),'utf8');
const setup=new Function('require','__dirname',fixture.slice(0,fixture.indexOf('const tests=[];'))+'\nreturn setup;')(require,__dirname);
(async()=>{
 const t=setup();
 t.run(`P.assets.push({id:'b036',type:'image',file:'both.jpg',roles:['target_state'],camera_scene:'S4'},
   {id:'7819',type:'image',file:'early.jpg',roles:['target_state'],camera_scene:'S4'});
 P.images.both={id:'both',asset_id:'b036',for_task:'scene-camera:S4',state:'completed'};
 P.images.early={id:'early',asset_id:'7819',for_task:'scene-camera:S4',state:'completed'};
 P.scene_cast.S4.camera_asset='7819';P.scene_cast.S4.camera_candidates=['b036','7819'];
 P.scene_cast.S4.frame={at:11.44,frame_mode:'auto',characters:['C1','C2'],reason:'儿子 10.58 秒后进画面，已选父子都在画面里的一帧'};
 P.scene_cast.S4.camera_frame_notice='在用的这张主镜头图用的是 9.65 秒的画面（儿子还没进门）。想要父子同框，可以改用之前的版本，或按 11.44 秒重新生成';renderReplacements(true)`);
 assert.equal(t.$('[data-scene-camera="S4"]'),null);
 assert.doesNotMatch(t.$('#step-3').textContent,/9\.65|主镜头图|分场表/);
 t.run(`P.replacement_checks={'seg-03':{errors:[],warnings:[],issues:[]}};
 S={tasks:[{id:'seg-03',order:1,kind:'speaker',mode:'video',dialogue:'Thank you, son.',source_start:9,source_end:12.3,duration:4,ratio:'9:16',image_ids:['b036','dad','son','watch']}]};
 P.script=clone(S);P.runs={'seg-03':[{rev:'r2',state:'completed',result:'r2.mp4'}]};renderTasks(true)`);
 assert.equal(t.$('[data-preview="seg-03"]').disabled,false);
 // Exercise the actual preview handler, then render the next completed version.
 t.run(`saveScript=async()=>{};api=async(method,url)=>{if(url.endsWith('/preview')){
 P.runs['seg-03'].push({rev:'r3',state:'pending_confirmation',preview:{tasks:[]}});return {};}
 if(method==='GET')return clone(P);throw Error('unexpected '+url);};renderAll=()=>{renderTasks(true);}`);
 await t.click(t.$('[data-preview="seg-03"]'));
 assert.equal(t.run(`P.runs['seg-03'].at(-1).rev`),'r3');
 t.run(`P.runs['seg-03'].at(-1).state='completed';P.runs['seg-03'].at(-1).result='r3.mp4';renderTasks(true)`);
 assert.ok(t.$('[data-choose="seg-03"][data-rev="r3"]'));
 assert.equal(t.$('[data-preview="seg-03"]').disabled,false);
 console.log('24.22 UI: neutral frame notice, previous-version selection and r2→r3 preview passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
