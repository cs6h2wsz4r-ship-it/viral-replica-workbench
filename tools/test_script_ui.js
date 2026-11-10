/* Offline checks of the real script editor: visible labels, saved values, original overlay text. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf("// ---------- infra ----------")) +
  source.slice(source.indexOf("function segEditor(t, i) {"), source.indexOf("function readSegEditors() {")) +
  source.slice(source.indexOf("function cameraScenes()"), source.indexOf("function motionTimelineHtml(")) + "\nP = {};", context);
const axes = vm.runInContext("RECIPE_AXES", context);
let checked = 0;
for (const kind of ["speaker", "broll", "motion", "camera"]) {
  context.task = { id: "seg-01", kind, source_start: 0, source_end: 10, duration: 10,
    title: "收尾揭晓", dialogue: "", action: "Keep moving.", story: "Show the product.", direction: "Follow her.",
    recipe: {}, overlays: [{ text: "I'm obsessed", textZh: "我太喜欢了" }, { text: "太棒了", textZh: "太棒了" }],
    split_reason: "连续镜头，无需切分" };
  for (const [axis, values] of Object.entries(axes[kind] || {})) {
    for (const value of values) {
      context.task.recipe[axis] = value;
      const html = vm.runInContext("segEditor(task, 0)", context);
      const select = html.match(new RegExp(`<select data-recipe="${axis}">([\\s\\S]*?)</select>`))[1];
      assert.match(select, new RegExp(`<option value="${value}" selected>[\u3400-\u9fff]`));
      assert.doesNotMatch(select.replace(/<[^>]+>/g, ""), /[A-Za-z]/);
      checked++;
    }
  }
  const html = vm.runInContext("segEditor(task, 0)", context);
  const labels = html.replace(/<textarea[^>]*>[\s\S]*?<\/textarea>/g, "").replace(/<[^>]+>/g, "");
  assert.doesNotMatch(labels, /PERFORMANCE AND ACTION DIRECTION|MICRO-STORY|ADDITIONAL DIRECTION|hypit estimate|composition-stability|camera-motion|material-mode|normal|fast|slow/);
  assert.match(html, /我太喜欢了（原文：I&#39;m obsessed）/);
  assert.equal((html.match(/太棒了/g) || []).length, 1);
  if (kind === "speaker") {
    for (const [value, label] of [["normal", "正常"], ["fast", "快速"], ["slow", "慢速"]]) {
      assert.match(html, new RegExp(`<option value="${value}"[^>]*>${label}</option>`));
    }
    assert.match(html, /Keep moving\./);
  }
}
assert.match(source, /留空 = 自动识别（推荐）/);
console.log(`PASS: ${checked} recipe values, Chinese labels, pace values, overlay translation/original and English directions (offline)`);
