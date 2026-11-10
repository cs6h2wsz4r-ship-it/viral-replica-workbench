/* Build a local, self-testing browser page. No server, network, or package installs.
 * Open runtime/temp/fix-wizard-browser.html; all viewports run in fixed-size frames. */
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const project = JSON.parse(fs.readFileSync(path.join(root, 'runtime/temp/fix-wizard-project.json')));
const playwrightTest = fs.readFileSync(path.join(__dirname, 'check_fix_wizard_playwright.js'), 'utf8');
const start = playwrightTest.indexOf('    await page.evaluate(project => {') + '    await page.evaluate(project => {'.length;
const setup = playwrightTest.slice(start, playwrightTest.indexOf('\n    }, project);', start));
const app = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8').replace(/boot\(\)\.catch\([^\n]+\);/, '');
const css = fs.readFileSync(path.join(root, 'web/style.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link[^>]*>/g, '');
const tests = async function(width) {
  const results = [];
  const check = (ok, label) => { if (!ok) throw Error(label); results.push(label); };
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const node = s => document.querySelector(s);
  try {
    await wait(400);
    const wizard = node('[data-task="seg-01"] .fix-wizard');
    check(wizard?.innerText.includes('预览前还差 3 步'), '向导 3 步');
    check(wizard.innerText.includes('下一步'), '下一步标记');
    check(!wizard.querySelector('details[open]'), '明细默认折叠');
    check(node('[data-preview-reason="seg-01"]').innerText.includes('还差 3 步'), '预览旁原因');
    check(!node('#legacyProjectBanner').hidden, '旧项目蓝色提示');
    const height = node('.topbar').getBoundingClientRect().height;
    check(height <= (width === 1280 ? 110 : 120), `顶部栏 ${height}px（${width}px 窗口）`);
    if (width <= 1100) {
      check(getComputedStyle(node('#statusPills')).display === 'none', '状态默认收起');
      node('#statusToggle').click();
      check(getComputedStyle(node('#statusPills')).display === 'flex', '状态可展开');
      check(node('.topbar').getBoundingClientRect().height === height, '展开不增高固定栏');
      node('#statusToggle').click();
    }
    for (const [step, target] of [['02', '#analyzeBtn'], ['03', '#replacementForm [data-r="action"]'], ['04', '#scriptBtn']]) {
      node(`[data-fix-step="${step}"]`).click();
      await wait(700);
      check(node(target).classList.contains('fix-highlight'), `${step} 控件高亮`);
      const top = node(target).getBoundingClientRect().top;
      check(top >= height, `${step} 控件顶部 ${Math.round(top)}px ≥ 固定栏 ${height}px`);
    }
    location.hash = 'step-2'; await wait(150);
    check(node('#step-2').getBoundingClientRect().top >= height, '浏览器原生锚点不遮挡');
    const guideText = wizard.innerText;
    showPreviewBlocked('seg-01', P.replacement_checks['seg-01']);
    check(node('#toast').innerText === '预览前还有 3 步要做，已在 seg-01 下方列出', '拦截仅显示摘要');
    toast('长错误内容'.repeat(80), true);
    check(node('#toast').innerText.length < 140, '长错误截短');
    node('#toast summary').click();
    check(node('#toast').innerText.includes('长错误内容'.repeat(80)), '长错误可展开');
    node('#toast').className = 'toast';
    const t = S.tasks[0], original = [...t.image_ids];
    t.image_ids.reverse();
    t.action = 'Smile at @image1 beside @image2.';
    t.story = '@image2 and @image1'; t.direction = '@image1 beside @image2';
    P.script = clone(S);
    P.replacement_checks[t.id] = {errors:['图1不是人物'], issues:[{code:'image1_not_person',step:'05',title:'口播段落的图1必须是出镜人物',fix:'调整图号'}]};
    seen.script = ''; renderScript(); renderTasks(true);
    node('[data-fix-step="05"]').click(); await wait(700);
    check(node('[data-task="seg-01"] .chips').getBoundingClientRect().top >= height, '05 参考图定位不遮挡');
    node('[data-fix-image1]').click(); await wait(150);
    check(JSON.stringify(S.tasks[0].image_ids) === JSON.stringify(original), '人物图移到图1');
    check(S.tasks[0].action === 'Smile at @image2 beside @image1.', 'action 图号同步');
    check(S.tasks[0].story === '@image1 and @image2', 'story 图号同步');
    check(S.tasks[0].direction === '@image2 beside @image1', 'direction 图号同步');
    check(savedBodies.length === 1, '脚本已请求保存');
    window.blockedIssues = [{code:'motion_additions',step:'04',title:'动作不一致',fix:'重新生成新脚本即可'}];
    P.replacement_checks['seg-01'] = {errors:[],issues:[]}; renderTasks(true);
    node('[data-preview="seg-01"]').click(); await wait(100);
    check(node('#toast').innerText === '预览前还有 1 步要做，已在 seg-01 下方列出', 'HTTP 400 仅显示摘要');
    check(node('.fix-wizard').innerText.includes('动作不一致'), 'HTTP issues 更新向导');
    S.tasks[0].image_ids = [P.assets.find(a => a.roles?.includes('product_truth')).id]; renderTasks(true);
    check(node('[data-image-order-warning]').innerText.includes('图1 应该是出镜人物或主镜头图'), '选图即时提示');
    parent.postMessage({ width, pass: true, results, guideText }, '*');
  } catch (error) { parent.postMessage({ width, pass: false, results, error: error.stack }, '*'); }
};
const script = content => '<script>' + content.replace(/<\/script/gi, '<\\/script') + '</script>';
const frames = [900, 1280, 625].map(width => {
  const inner = html.replace('</head>', `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;"><style>${css}</style></head>`)
    .replace('</body>', script(app + '\nconst project = ' + JSON.stringify(project) + ';\n' + setup + '\n(' + tests + ')(' + width + ');') + '</body>');
  const escaped = inner.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  return `<h2>${width}px</h2><pre id="result-${width}">验证中…</pre><iframe title="${width}px 离线回归" style="width:${width}px;height:${width === 625 ? 384 : 800}px" srcdoc="${escaped}"></iframe>`;
});
const output = '<!doctype html><meta charset="utf-8"><title>第15轮离线浏览器回归</title><h1>第15轮离线浏览器回归</h1>' + frames.join('\n') + script(`window.addEventListener('message', e => {
  if (![900,1280,625].includes(e.data?.width)) return;
  const r=e.data; document.querySelector('#result-'+r.width).textContent = (r.pass?'PASS':'FAIL')+' '+r.width+'px\\n'+r.results.join('\\n')+(r.error?'\\n'+r.error:'');
  if(r.width===900 && r.guideText) {const pre=document.createElement('pre'); pre.id='rendered-guide'; pre.textContent=r.guideText; document.body.append(pre);}
});`);
fs.writeFileSync(path.join(root, 'runtime/temp/fix-wizard-browser.html'), output);
console.log('Built runtime/temp/fix-wizard-browser.html (mock API, CSP blocks external resources)');
