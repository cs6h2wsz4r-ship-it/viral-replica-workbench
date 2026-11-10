/* V8c sync: actual comparison renderer/events, synthetic browser media only. */
const assert=require('node:assert/strict');
const {fixture:base}=require('./test_v8b_ui');

function media(video,{duration=4,muted=false,minRate=.0625,maxRate=16,readyState=4}={}){
 assert.ok(video,'comparison video exists');
 const listeners=new Map(),queued=[];
 let time=0,rate=1;
 video.duration=duration;video.muted=muted;video.paused=true;video.seeking=false;video.readyState=readyState;
 video.playCalls=0;video.pauseCalls=0;
 video.addEventListener=(name,fn)=>{if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);};
 video.removeEventListener=(name,fn)=>listeners.get(name)?.delete(fn);
 video.emit=name=>{for(const fn of [...(listeners.get(name)||[])])fn({type:name,target:video});};
 Object.defineProperty(video,'currentTime',{configurable:true,get:()=>time,set:value=>{
  time=Number(value);if(video.readyState===0)return;
  video.seeking=true;queued.push(()=>video.emit('seeking'),()=>{video.seeking=false;video.emit('seeked');});
 }});
 Object.defineProperty(video,'playbackRate',{configurable:true,get:()=>rate,set:value=>{
  if(value<minRate || value>maxRate)throw Error('Unsupported playback rate');rate=value;
 }});
 video.play=()=>{video.playCalls++;video.paused=false;return video.playResult?video.playResult():Promise.resolve();};
 video.pause=()=>{video.pauseCalls++;if(!video.paused){video.paused=true;queued.push(()=>video.emit('pause'));}};
 video.advance=value=>{time=value;video.emit('timeupdate');};
 video.drag=value=>{time=value;video.seeking=true;video.emit('seeking');};
 video.flush=()=>{while(queued.length)queued.shift()();};
 video.listenerCount=()=>[...listeners.values()].reduce((n,set)=>n+set.size,0);
 return video;
}
function fixture({source={},target={},state='completed',result='generated-1.mp4'}={}){
 const t=base(),timers=new Map();let nextTimer=1;
 t.context.syncState=state;t.context.syncResult=result;
 t.run(`P.segments_plan.segments[0].source_start=10;P.segments_plan.segments[0].source_end=16;P.segments_plan.segments[0].duration=8;
 P.director.segments[0]={...P.director.segments[0],source_start:10,source_end:16,duration:8};
 P.runs.d01=[{rev:'r1',state:syncState,result:syncResult,review:{status:'completed',verdict:'fix',issues:[{level:'high',start:2,end:3,what:'需核对',fix:'rewrite',how:'调整'}]}}];
 v5View('d01').view='video';renderComparison()`);
 t.context.setTimeout=(fn,ms)=>{const id=nextTimer++;timers.set(id,{fn,ms});return id;};
 t.context.clearTimeout=id=>timers.delete(id);
 t.timers=timers;t.tick=()=>{const jobs=[...timers.values()];timers.clear();jobs.forEach(job=>job.fn());};
 t.source=media(t.$('[data-video-side="source"] video'),source);
 if(t.$('[data-video-side="target"] video'))t.target=media(t.$('[data-video-side="target"] video'),target);
 t.sync=()=>t.$('[data-comparison-sync]');
 t.flush=()=>{t.source.flush();t.target?.flush();};
 t.dispose=()=>t.run('renderComparison()');
 return t;
}
const start=async t=>{await t.click(t.sync());await Promise.resolve();};
const assertStopped=t=>{assert.equal(t.source.paused,true);assert.equal(t.target.paused,true);assert.equal(t.sync().textContent,'同步播放');};

async function syncControlsAndMapping(){
 const t=fixture(),before=t.run('JSON.stringify(P)');
 assert.ok(t.sync(),'video comparison exposes a sync control');assert.equal(t.sync().disabled,false);assert.equal(t.sync().textContent,'同步播放');
 assert.ok(t.$('#comparisonView').innerHTML.indexOf('data-comparison-sync')<t.$('#comparisonView').innerHTML.indexOf('id="comparisonVideos"'),'sync control appears above both players');
 await start(t);
 assert.equal(t.source.currentTime,10);assert.equal(t.target.currentTime,0);
 assert.equal(t.source.playbackRate,1.5,'actual generated duration overrides the planned eight seconds');
 assert.equal(t.source.paused,false);assert.equal(t.target.paused,false);assert.equal(t.sync().textContent,'暂停');
 assert.equal(t.source.muted,true);assert.equal(t.target.muted,false);
 t.flush();assert.equal(t.sync().textContent,'暂停','queued initial seeking events are not user drags');
 await t.click(t.sync());assertStopped(t);assert.equal(t.source.muted,false);assert.equal(t.source.playbackRate,1);
 assert.equal(t.source.listenerCount()+t.target.listenerCount(),0);assert.equal(t.timers.size,0);
 assert.equal(t.run('JSON.stringify(P)'),before);assert.equal(t.requests.length,0);
 for(const state of ['running','failed','pending_confirmation']){
  const pending=fixture({state});assert.equal(pending.sync().disabled,true,'only a completed result may sync');await pending.click(pending.sync());assert.equal(pending.source.playCalls,0);pending.dispose();
 }
 const missing=fixture({result:null});assert.equal(missing.sync().disabled,true);missing.dispose();
 console.log('PASS V8c sync control, actual duration, muted source, async initial seeking, no API or project mutation');
}

async function syncDurationAndRateLimits(){
 for(const duration of [NaN,Infinity,0,-1]){
  const t=fixture({target:{duration}});await start(t);assert.equal(t.source.playbackRate,.75,'invalid metadata uses the planned duration');
  await t.click(t.$('[data-review-seek]'));assert.equal(t.target.currentTime,2);assert.equal(t.source.currentTime,11.5);assertStopped(t);t.dispose();
 }
 const low=fixture({target:{duration:1000},source:{minRate:.25}});await start(low);assert.ok(low.source.playbackRate>=.25 && low.source.playbackRate<.251);low.dispose();
 const high=fixture({target:{duration:.01},source:{maxRate:4}});await start(high);assert.ok(high.source.playbackRate<=4 && high.source.playbackRate>3.99);high.dispose();
 const wide=fixture({target:{duration:6/32},source:{maxRate:64}});await start(wide);assert.equal(wide.source.playbackRate,32,'a supported rate must not be artificially capped');wide.dispose();
 const update=fixture({target:{duration:NaN}});await start(update);update.target.duration=3;update.target.emit('loadedmetadata');assert.equal(update.source.playbackRate,2);update.dispose();
 const frozen=fixture();frozen.run("P.runs.d01[0].review_inputs={segment:{source_start:20,source_end:26,duration:8}};renderComparison()");
 assert.match(frozen.$('[data-video-side="source"] video').src,/#t=20,26$/,'source media fragment shares the selected version frozen range');
 frozen.source=media(frozen.$('[data-video-side="source"] video'));frozen.target=media(frozen.$('[data-video-side="target"] video'));await start(frozen);
 assert.equal(frozen.source.currentTime,20);await frozen.click(frozen.$('[data-review-seek]'));assert.equal(frozen.source.currentTime,23);frozen.dispose();
 console.log('PASS V8c duration fallback/metadata refresh and browser-supported rate boundaries');
}

async function syncBoundariesAndMute(){
 const t=fixture();await start(t);t.flush();
 t.source.muted=false;t.source.emit('volumechange');t.tick();assert.equal(t.source.muted,false,'manual unmute is respected during sync');
 t.source.advance(16.4);assert.equal(t.source.currentTime,16);assert.equal(t.source.paused,true);assert.equal(t.target.paused,false);assert.equal(t.sync().textContent,'暂停');
 t.flush();assert.equal(t.target.paused,false,'automatic source-end pause does not interrupt the generated video');
 t.target.advance(4);t.target.emit('ended');assertStopped(t);assert.equal(t.source.muted,false);assert.equal(t.timers.size,0);
 await start(t);assert.equal(t.source.currentTime,10);assert.equal(t.target.currentTime,0);t.flush();t.source.emit('ended');t.target.emit('ended');assert.equal(t.source.paused,false);assert.equal(t.target.paused,false,'old queued ended events cannot end a restart');t.dispose();
 const reverse=fixture({source:{muted:true}});await start(reverse);reverse.flush();reverse.target.advance(4);reverse.target.emit('ended');assert.equal(reverse.target.paused,true);assert.equal(reverse.source.paused,false);
 reverse.source.advance(16);assertStopped(reverse);assert.equal(reverse.source.muted,true);
 const timer=fixture();await start(timer);timer.flush();Object.defineProperty(timer.source,'currentTime',{value:17,writable:true,configurable:true});timer.tick();assert.equal(timer.source.currentTime,16);assert.equal(timer.source.paused,true);timer.dispose();
 console.log('PASS V8c independent segment-end guards, restart from beginning, manual unmute and mute restoration');
}

async function syncManualExitAndNavigation(){
 for(const side of ['source','target'])for(const kind of ['pause','seeking']){
  const t=fixture();await start(t);t.flush();
  if(kind==='pause'){t[side].pause();t[side].flush();}else t[side].drag(side==='source'?12:2);
  assertStopped(t);assert.equal(t.source.listenerCount()+t.target.listenerCount(),0);assert.equal(t.timers.size,0);t.dispose();
 }
 const endedSource=fixture();await start(endedSource);endedSource.flush();endedSource.source.advance(16.1);endedSource.flush();
 endedSource.source.drag(12);assertStopped(endedSource);endedSource.dispose();
 const cold=fixture();let coldTime=0;
 cold.context.syncNow=10000;cold.run('Date.now=()=>syncNow');
 Object.defineProperty(cold.source,'currentTime',{configurable:true,get:()=>coldTime,set:value=>{coldTime=Number(value);}});
 await start(cold);cold.target.flush();assert.equal(cold.sync().textContent,'暂停');
 cold.context.syncNow+=1500;cold.tick();cold.source.currentTime=10.2;cold.source.emit('seeking');assertStopped(cold);cold.dispose();
 const slow=fixture({source:{readyState:0},target:{readyState:0,duration:NaN}});
 slow.context.syncNow=10000;slow.run('Date.now=()=>syncNow');await start(slow);
 slow.context.syncNow+=2500;slow.tick();slow.source.readyState=1;slow.source.currentTime=10;slow.source.emit('loadedmetadata');
 slow.target.readyState=1;slow.target.duration=4;slow.target.currentTime=0;slow.target.emit('loadedmetadata');slow.flush();
 assert.equal(slow.source.paused,false);assert.equal(slow.target.paused,false);assert.equal(slow.source.currentTime,10);assert.equal(slow.target.currentTime,0);assert.equal(slow.source.playbackRate,1.5);
 slow.tick();slow.source.drag(12);assertStopped(slow);slow.dispose();
 const seek=fixture();await start(seek);seek.flush();await seek.click(seek.$('[data-review-seek]'));
 assertStopped(seek);assert.equal(seek.target.currentTime,2);assert.equal(seek.source.currentTime,13,'review seek shares actual-duration mapping');
 for(const navigation of ['board','segment','version','render']){
  const t=fixture();t.run("P.runs.d01.push({rev:'r2',state:'completed',result:'generated-2.mp4'});v5View('d01').rev='r1';renderComparison()");
  t.source=media(t.$('[data-video-side="source"] video'));t.target=media(t.$('[data-video-side="target"] video'));await start(t);t.flush();
  const old=[t.source,t.target];
  if(navigation==='board')await t.click(t.$('[data-compare-view="board"]'));
  if(navigation==='segment')await t.click(t.$('[data-compare-segment-tab="d02"]'));
  if(navigation==='version')await t.click(t.$('[data-compare-version="r2"]'));
  if(navigation==='render')t.run('renderComparison()');
  assert.ok(old.every(video=>video.paused && video.listenerCount()===0),navigation+' pauses old media and removes listeners');assert.equal(t.timers.size,0);assert.equal(t.requests.length,0);
 }
 console.log('PASS V8c either-player manual pause/seek, review jump and navigation/render cleanup');
}

async function syncPlayFailuresAndRaces(){
 for(const side of ['source','target']){
  const t=fixture();t[side].playResult=()=>Promise.reject(Error('play blocked'));await start(t);await Promise.resolve();assertStopped(t);assert.equal(t.source.muted,false);assert.equal(t.timers.size,0);t.dispose();
 }
 const failed=fixture();failed.source.playResult=()=>{throw Error('play unavailable');};await start(failed);assertStopped(failed);failed.dispose();
 const stale=fixture();let resolve;
 stale.target.playResult=()=>new Promise(done=>resolve=()=>{stale.target.paused=false;done();});
 const pending=start(stale);await Promise.resolve();await stale.click(stale.sync());assertStopped(stale);
 resolve();await pending;await Promise.resolve();assertStopped(stale);stale.dispose();
 const restarted=fixture();let reject;
 restarted.target.playResult=()=>new Promise((_,no)=>reject=no);
 const first=start(restarted);await Promise.resolve();await restarted.click(restarted.sync());
 restarted.target.playResult=()=>Promise.resolve();await start(restarted);restarted.flush();reject(Error('old play rejected'));await first;await Promise.resolve();
 assert.equal(restarted.source.paused,false);assert.equal(restarted.target.paused,false);assert.equal(restarted.sync().textContent,'暂停','an old rejection cannot stop the new session');restarted.dispose();
 const switched=fixture();let done;
 switched.target.playResult=()=>new Promise(resolve=>done=()=>{switched.target.paused=false;resolve();});
 const launching=start(switched);await Promise.resolve();await switched.click(switched.$('[data-compare-view="board"]'));done();await launching;await Promise.resolve();assertStopped(switched);assert.equal(switched.timers.size,0);
 console.log('PASS V8c synchronous/asynchronous play failures, late resolution after stop and stop/restart races');
}

const syncTests=[syncControlsAndMapping,syncDurationAndRateLimits,syncBoundariesAndMute,syncManualExitAndNavigation,syncPlayFailuresAndRaces];
async function runSyncTests(){const failures=[];for(const test of syncTests){try{await test();}catch(error){failures.push(test.name);console.error('FAIL '+test.name+'\n'+error.stack);}}assert.deepEqual(failures,[]);}
module.exports={fixture,media,syncTests,runSyncTests,main:runSyncTests,...Object.fromEntries(syncTests.map(test=>[test.name,test]))};
if(require.main===module)runSyncTests().catch(error=>{console.error(error);process.exitCode=1;});
