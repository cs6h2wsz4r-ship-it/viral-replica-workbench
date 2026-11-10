/* Real Chromium layout and clicks, static page and mocked API only; no workbench server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
function browserLibrary() {
  if (process.env.PLAYWRIGHT_MODULE) return require(process.env.PLAYWRIGHT_MODULE);
  try { return require('playwright'); } catch (_) {}
  const cache = path.join(require('node:os').homedir(), '.npm/_npx');
  for (const entry of fs.readdirSync(cache)) {
    const candidate = path.join(cache, entry, 'node_modules/playwright');
    if (fs.existsSync(candidate)) return require(candidate);
  }
  throw new Error('需要本机已有的 Playwright；本测试不会安装依赖。');
}
async function main() {
  execFileSync(path.join(root, 'runtime/venv/bin/python'), [path.join(root, 'tools/test_fix_wizard.py')], { cwd: root });
  const project = JSON.parse(fs.readFileSync(path.join(root, 'runtime/temp/fix-wizard-project.json')));
  process.env.TMPDIR = path.join(root, 'runtime/temp');
  const { chromium } = browserLibrary();
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    await page.route('**/*', route => route.abort());
    const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8').replace(/<script[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link[^>]*>/g, '');
    await page.setContent(html);
    await page.addStyleTag({ path: path.join(root, 'web/style.css') });
    const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8').replace(/boot\(\)\.catch\([^\n]+\);/, '');
    await page.addScriptTag({ content: source });
    await page.evaluate(project => {
      window.savedBodies = [];
      window.fetch = async (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : null;
        if (url.endsWith('/replacement-check')) return { ok: true, json: async () => P.replacement_checks };
        if (url.endsWith('/preview')) return { ok: false, status: 400, json: async () => ({ error: '原错误'.repeat(200), issues: window.blockedIssues, errors: ['原错误'] }) };
        if (url.endsWith('/script')) {
          savedBodies.push(structuredClone(body));
          // Server rebase is separately exercised by test_fix_wizard.py.
          const task = body.script.tasks[0], old = P.script.tasks[0];
          const order = body.reference_orders?.[task.id];
          for (const key of ['action', 'story', 'direction']) if (order || task[key] === old[key]) {
            task[key] = (task[key] || '').replace(/@image(\d+)\b/g, (_, n) => '@image' + (task.image_ids.indexOf((order || old.image_ids)[n - 1]) + 1));
          }
          return { ok: true, json: async () => ({ script: body.script }) };
        }
        throw new Error('Unexpected API: ' + url);
      };
      P = project; S = clone(project.script);
      STATUS = { default_llm: 'mock', ffmpeg: true, asr: true, asr_engines: ['whisperx', 'faster-whisper'], jimmy: true,
        providers: { jimmy: { ready: true, label: '吉米 · GPT-5.6 Sol' }, doubao: { ready: true, label: '豆包 Seed 2.0 Pro' },
          deepseek: { ready: true, label: 'DeepSeek V4.1 Flash' }, custom: { ready: true, label: '自定义' } },
        runtime: { running_in_venv: true, runtime_mb: 5630 } };
      document.querySelector('#workspace').hidden = false;
      document.querySelector('#projectSelect').innerHTML = '<option>旧项目回归测试</option>';
      renderStatus(); renderAll();
    }, project);
    await page.waitForTimeout(350);
    const wizard = page.locator('[data-task="seg-01"] .fix-wizard');
    await assert.equal(await wizard.count(), 1, 'segment needs a repair wizard');
    assert.match(await wizard.innerText(), /预览前还差 3 步/);
    assert.equal(await wizard.getByText('下一步', { exact: true }).count(), 1);
    assert.equal(await wizard.locator('details[open]').count(), 0);
    assert.match(await page.locator('[data-preview-reason="seg-01"]').innerText(), /还差 3 步：第 02 步 拆解/);
    assert.equal(await page.locator('#legacyProjectBanner').isVisible(), true);
    for (const width of [900, 1280, 625]) {
      await page.setViewportSize({ width, height: width === 625 ? 384 : 800 });
      const height = await page.locator('.topbar').evaluate(el => el.getBoundingClientRect().height);
      assert.ok(height <= (width === 1280 ? 110 : 120), `${width}px header ${height}px`);
      console.log(`HEADER ${width}: ${height}px`);
      if (width <= 1100) {
        assert.equal(await page.locator('#statusPills').isVisible(), false);
        await page.locator('#statusToggle').click();
        assert.equal(await page.locator('#statusPills').isVisible(), true);
        assert.equal(await page.locator('.topbar').evaluate(el => el.getBoundingClientRect().height), height);
        await page.locator('#statusToggle').click();
      }
      for (const [step, target] of [['02', '#analyzeBtn'], ['03', '#replacementForm [data-r="action"]'], ['04', '#scriptBtn']]) {
        await page.locator(`[data-fix-step="${step}"]`).first().click();
        await page.waitForTimeout(450);
        assert.equal(await page.locator(target).first().evaluate(el => el.classList.contains('fix-highlight')), true);
        const rect = await page.locator(target).first().boundingBox();
        assert.ok(rect.y >= height, `${width}px step ${step} hidden: y=${rect.y}, bar=${height}`);
      }
      await page.evaluate(() => { location.hash = ''; location.hash = 'step-2'; });
      await page.waitForTimeout(100);
      const target = await page.locator('#step-2').boundingBox();
      assert.ok(target.y >= height, `${width}px native hash target hidden`);
    }
    await page.evaluate(() => showPreviewBlocked('seg-01', P.replacement_checks['seg-01']));
    assert.equal(await page.locator('#toast').innerText(), '预览前还有 3 步要做，已在 seg-01 下方列出');
    await page.evaluate(() => toast('长错误内容'.repeat(80), true));
    assert.ok((await page.locator('#toast').innerText()).length < 140);
    await page.locator('#toast summary').click();
    assert.match(await page.locator('#toast').innerText(), new RegExp('长错误内容'.repeat(80)));
    await page.evaluate(() => {
      const t = S.tasks[0];
      t.image_ids.reverse();
      t.action = 'Smile at @image1 beside @image2.';
      t.story = '@image2 and @image1'; t.direction = '@image1 beside @image2';
      P.script = clone(S);
      P.replacement_checks[t.id] = { errors: ['图1不是人物'], issues: [{ code: 'image1_not_person', step: '05', title: '口播段落的图1必须是出镜人物', fix: '把人物图放到图1' }] };
      seen.script = ''; renderScript(); renderTasks(true);
    });
    await page.getByRole('button', { name: '把人物/主镜头图放到图1' }).click();
    await page.waitForTimeout(100);
    const repaired = await page.evaluate(() => ({ task: S.tasks[0], bodies: savedBodies }));
    assert.deepEqual(repaired.task.image_ids, project.script.tasks[0].image_ids);
    assert.equal(repaired.task.action, 'Smile at @image2 beside @image1.');
    assert.equal(repaired.task.story, '@image1 and @image2');
    assert.equal(repaired.task.direction, '@image2 beside @image1');
    assert.equal(repaired.bodies.length, 1);
    // Exercise the actual HTTP rejection path after a successful save.
    await page.evaluate(() => {
      blockedIssues = [{ code: 'motion_additions', step: '04', title: '动作不一致', fix: '重新生成新脚本即可' }];
      P.replacement_checks['seg-01'] = { errors: [], issues: [] }; renderTasks(true);
    });
    await page.locator('[data-preview="seg-01"]').click();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#toast').innerText(), '预览前还有 1 步要做，已在 seg-01 下方列出');
    assert.match(await wizard.innerText(), /动作不一致/);
    await page.evaluate(() => {
      S.tasks[0].image_ids = [P.assets.find(a => a.roles?.includes('product_truth')).id]; renderTasks(true);
    });
    assert.match(await page.locator('[data-task="seg-01"] [data-image-order-warning]').innerText(), /图1 应该是出镜人物或主镜头图/);
    console.log('PASS: wizard, grouping, targets/highlights, summary, details, ordering/remapping/save, legacy notice, compact topbar and native anchors (offline Chromium)');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
