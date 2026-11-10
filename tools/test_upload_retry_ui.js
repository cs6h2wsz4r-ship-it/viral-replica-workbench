/* Offline UI checks: evaluate the actual renderer and click handler with mock DOM/API. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const renderer = source.slice(source.indexOf("// Generated HTML"), source.indexOf("// ---------- infra ----------")) +
  source.slice(source.indexOf("function motionCheckHtml("), source.indexOf("function replacementCheckHtml(")) +
  source.slice(source.indexOf("function renderRuns(t) {"), source.indexOf("function openImageForm(tid) {"));
const eventStart = source.indexOf('document.addEventListener("click", async (e) => {');
const events = source.slice(eventStart, source.indexOf("\n});", eventStart) + 4);
const live = { dataset: {}, innerHTML: "" };
let clickHandler;
let refreshes = 0;
const requests = [];
const context = vm.createContext({
  P: { id: "offline", runs: {}, jobs: {}, images: {} },
  $: () => live,
  esc: (value) => String(value || ""),
  fileUrl: (value) => value,
  document: { addEventListener: (event, fn) => { clickHandler = fn; } },
  api: async (...args) => { requests.push(args); return { ok: true }; },
  guard: async (fn) => fn(),
  refresh: async () => { refreshes++; },
});
vm.runInContext(renderer + source.slice(source.indexOf('// ---------- storyboard image inspection'), source.indexOf('// ---------- paired storyboard workspace')) + events, context);

function render(run) {
  context.P.runs = { t1: [{ rev: "r1", model: "mock", ...run }] };
  vm.runInContext('renderRuns({ id: "t1" })', context);
  return live.innerHTML;
}

async function main() {
  const safe = render({ state: "failed", retryable: true });
  assert.match(safe, /data-resubmit="t1"/);
  assert.match(safe, /重新提交（上次没有扣费）/);
  assert.match(safe, /成功创建任务后会正常计费/);
  for (const run of [
    { state: "completed", retryable: true },
    { state: "running", retryable: true },
    { state: "submitting", retryable: true },
    { state: "failed", retryable: false },
    { state: "unknown", retryable: false },
  ]) {
    assert.doesNotMatch(render(run), /data-resubmit=/);
  }
  const unsafe = render({ state: "failed", retryable: false, phase: "failed" });
  assert.match(unsafe, /可能已经扣费/);
  assert.doesNotMatch(unsafe, /没有产生视频费用/);
  assert.match(unsafe, /data-resume="t1"/);
  assert.match(unsafe, /data-recover="t1"/);
  for (const retryable of [false, true]) {
    for (const remote_task_id of [null, "remote-id"]) {
      const superseded = render({ state: "failed", retryable, remote_task_id, superseded_by: "r2", phase: "create_started" });
      assert.match(superseded, /已被 r2 取代，这次失败不影响成片/);
      assert.doesNotMatch(superseded, /data-(resubmit|resume|recover)=/);
      assert.doesNotMatch(superseded, /重新提交|刷新\/恢复|填写远端任务 ID 恢复|可能已经扣费|没有产生视频费用/);
    }
  }
  assert.match(render({ state: "running", phase: "create_started" }), /data-recover="t1"/);
  assert.doesNotMatch(render({ state: "running", phase: "failed", upload_retry_active: true }), /data-recover=/);
  context.P.jobs = { "gen:t1:r1": { state: "done" }, "gen:t1:r2": { state: "running" } };
  assert.match(render({ state: "running", phase: "create_started" }), /data-recover="t1"/);
  context.P.jobs["gen:t1:r1"].state = "running";
  assert.doesNotMatch(render({ state: "running", phase: "create_started" }), /data-recover=/);
  context.P.jobs["gen:t1:r1"].state = "done";
  assert.match(render({ state: "running", phase: "create_started" }), /data-recover="t1"/);
  await clickHandler({ target: { closest: selector => /story-preview|storyboardLightbox/.test(selector) ? null : ({ dataset: { resubmit: "t1", rev: "r1" } }) } });
  assert.equal(JSON.stringify(requests), JSON.stringify([["POST", "projects/offline/tasks/t1/resubmit", { rev: "r1" }]]));
  assert.equal(refreshes, 1);
  console.log("PASS: retry button visibility, superseded runs, recovery actions, fee wording and resubmit click (offline)");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
