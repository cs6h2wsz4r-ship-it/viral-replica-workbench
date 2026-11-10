/* SB1 intentionally retires scene-camera controls from step 03. Backend tests remain. */
const assert=require('node:assert/strict');
const {setup}=require('./test_storyboard_sb1_ui');
const t=setup();
for(const selector of ['[data-camera-key]','[data-camdraft]','[data-scene-camera]','[data-camera-batch-draft]','#cameraImages'])assert.equal(t.$(selector),null);
assert.doesNotMatch(t.$('#step-3').textContent,/主镜头图|分场表/);
assert.ok(t.$('[data-cast-identify]'));
console.log('PASS SB1: no scene camera, batch image or camera notice UI in step 03');
