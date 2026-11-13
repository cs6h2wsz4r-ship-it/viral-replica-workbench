/* Code updates use their own dialog and never submit the settings/key form. */
(() => {
  const dialog = document.querySelector('#updateDialog');
  const view = document.querySelector('#updateInfo');
  let state = {}, busy = false, polling = false;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function render() {
    const active = ['checking','downloading','installing','rolling_back','restarting'].includes(state.phase);
    view.innerHTML = `<p>当前版本 <b>${escape(state.current_version || '读取中')}</b></p>
      ${state.available ? `<p>可更新到 <b>${escape(state.available.version)}</b></p>` : ''}
      <p role="status" ${state.phase==='error'?'class="action-error"':''}>${escape(state.message || '点“检查更新”获取最新发布版本。')}</p>`;
    document.querySelector('#checkUpdateBtn').disabled = busy || active;
    const apply = document.querySelector('#applyUpdateBtn');
    apply.hidden = !state.available; apply.disabled = busy || active;
    const rollback = document.querySelector('#rollbackUpdateBtn');
    rollback.hidden = !state.can_rollback; rollback.disabled = busy || active;
    document.querySelector('#updateConfirmation').hidden = true;
    document.querySelector('#refreshUpdatedBtn').hidden = state.phase !== 'restarting';
  }
  async function read() { state = await api('GET', 'updates'); render(); }
  async function poll() {
    if(polling)return; polling=true;
    try {
      for(let i=0;i<300;i++) {
        await new Promise(resolve=>setTimeout(resolve,1000));
        const restarting = state.phase === 'restarting';
        try { await read(); } catch(error) { if(restarting)continue; throw error; }
        if(restarting && state.phase==='idle') { location.reload(); return; }
        if(!['downloading','installing','rolling_back','restarting'].includes(state.phase))return;
      }
      state.message='更新状态等待较久，请稍后刷新查看；不要重复启动更新。'; render();
    } catch(error) { state.message=error.message; render(); }
    finally { polling=false; }
  }
  async function run(action, body={}) {
    busy=true;
    if(action==='check')state={...state,phase:'checking',message:'正在检查更新…',available:null};
    render();
    try { state=await api('POST','updates/'+action,body); render(); }
    catch(error) { state={...state,phase:'error',message:error.message}; render(); }
    finally { busy=false; render(); }
    if(['downloading','installing','rolling_back','restarting'].includes(state.phase))poll();
  }
  document.querySelector('#updatesBtn').onclick = async () => {
    dialog.showModal();
    try { await read(); } catch(error) { state={phase:'error',message:error.message};render(); }
    if(['downloading','installing','rolling_back','restarting'].includes(state.phase))poll();
  };
  document.querySelector('#closeUpdateBtn').onclick = () => dialog.close();
  document.querySelector('#checkUpdateBtn').onclick = () => run('check');
  for(const [id,action,text] of [
    ['applyUpdateBtn','apply','更新前请保存页面中尚未保存的修改。更新会备份并替换程序代码，完成后自动重启；项目、素材、Key、模型和依赖都会保留。'],
    ['rollbackUpdateBtn','rollback','恢复到上次更新前的程序代码并重启。更新后创建或修改的项目和素材会保留。']]) {
    document.querySelector('#'+id).onclick = () => {
      document.querySelector('#updateConfirmText').textContent=text;
      document.querySelector('#updateConfirmation').hidden=false;
      document.querySelector('#confirmCodeUpdateBtn').onclick=()=>run(action,{sha:state.available?.sha});
    };
  }
  document.querySelector('#cancelCodeUpdateBtn').onclick=()=>{document.querySelector('#updateConfirmation').hidden=true;};
  document.querySelector('#refreshUpdatedBtn').onclick=()=>location.reload();
})();
