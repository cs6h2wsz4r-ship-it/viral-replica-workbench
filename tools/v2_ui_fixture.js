/* Shared synthetic Step 04/05 state for former sheet-edit UI regressions. */
const {setup:base}=require('./test_storyboard_sb1_ui');
function setup(){
 const t=base();
 t.run(`P.segments_plan={format_version:2,summary:'两段',why_segments:'场景变化',skip_cells:[],questions:[],segments:[0,1].map(i=>({id:'s0'+(i+1),title:i?'室内':'门廊',cells:P.storyboard.cells.slice(i*5,i*5+5).map(c=>c.id),mode:i?'image':'video',refs:['C1','product'],gen_images:[P.storyboard.cells[i*5].id,'C1','product'],source_start:i*5,source_end:i*5+5,duration:5}))};
 P.segment_boards={segments:Object.fromEntries(P.segments_plan.segments.map(s=>[s.id,{source:{id:s.id,cells:s.cells,file:s.id+'-source.jpg',cols:3,rows:2}}]))};
 renderSteps()`);
 t.project.segments_plan=JSON.parse(t.run('JSON.stringify(P.segments_plan)'));
 t.project.segment_boards=JSON.parse(t.run('JSON.stringify(P.segment_boards)'));
 return t;
}
module.exports={setup};
