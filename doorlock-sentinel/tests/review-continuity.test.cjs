const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../src/doorlock_sentinel/static/app.js"), "utf8");
function setup() {
  const nodes = new Map();
  const node = () => ({innerHTML:"", hidden:true, open:false, options:[], value:"", textContent:"", setAttribute(){}, focus(){}, close(){this.open=false;}, showModal(){this.open=true;}, querySelector(){return null;}});
  const scope = {document:{querySelectorAll(){return [];},querySelector(s){if(s === "#modal-relationship" || s === "#modal-name-hint") return null; if(!nodes.has(s)) nodes.set(s,node()); return nodes.get(s);}}, location:{hash:"#people"}, setTimeout(){}, crypto:{randomUUID:()=>"synthetic-key"}, DoorlockTime:{dayLabel:()=>"",formatDate:()=>""}};
  vm.createContext(scope);
  vm.runInContext(source.slice(0,source.indexOf('document.addEventListener("click"')),scope);
  const run = code => vm.runInContext(code,scope);
  run('api=async()=>({items:[]});state.csrf="synthetic";state.route="people";state.people=[{id:"cleaner",display_name:"保洁1"},{id:"neighbor",display_name:"邻居1"}];state.clusters=[{id:"c1",tracks:[]},{id:"c2",tracks:[]}];');
  return {nodes,run};
}
test("manual open and closed choices survive refresh and leaving people", async () => {
  const {nodes,run}=setup();
  run('renderPeopleContent();renderPeople=async()=>renderPeopleContent();setHeader=()=>{};');
  const pending=run('$(".pending-disclosure")');
  pending.open=true;
  await run('loadRoute()');
  assert.ok(nodes.get("#content").innerHTML.includes('class="pending-disclosure" open'));
  pending.open=false;
  await run('loadRoute()');
  assert.ok(!nodes.get("#content").innerHTML.includes('class="pending-disclosure" open'));
  pending.open=true;
  run('location.hash="#events";renderEvents=async()=>{};');
  await run('loadRoute()');
  assert.equal(run('state.pendingOpen'),true);
});
test("successful merge remembers target; cancel and failure do not replace it", async () => {
  const {nodes,run}=setup();
  const select=run('$("#modal-target")');
  select.options=[{value:"person:cleaner"},{value:"person:neighbor"},{value:"cluster:c2"}];
  select.value="person:cleaner";
  run('mutate=async()=>{};mergeCluster("c1")');
  select.value="person:neighbor";
  await run('state.modalHandler()');
  assert.equal(run('state.lastMergeTarget'),"person:neighbor");
  select.value="person:cleaner";
  run('mergeCluster("c2")');
  assert.equal(select.value,"person:neighbor");
  select.value="person:cleaner"; // change then cancel, no submit
  run('modal.close();mergeCluster("c2")');
  assert.equal(select.value,"person:neighbor");
  select.value="person:cleaner";
  run('mutate=async()=>{throw new Error("synthetic conflict")};');
  await assert.rejects(run('state.modalHandler()'),/synthetic conflict/);
  assert.equal(run('state.lastMergeTarget'),"person:neighbor");
  run('mutate=async()=>{};mergeCluster("c1")');
  select.value="cluster:c2";
  await run('state.modalHandler()');
  assert.equal(run('state.lastMergeTarget'),"cluster:c2");
});
test("person dialogs share successful target memory; missing or excluded targets fall back", async () => {
  const {run}=setup();
  const select=run('$("#modal-target")');
  select.options=[{value:"cleaner"},{value:"neighbor"}]; select.value="cleaner";
  run('state.lastMergeTarget="person:neighbor";mergePerson("source")');
  assert.equal(select.value,"neighbor");
  run('mutate=async()=>{}');
  await run('state.modalHandler()');
  assert.equal(run('state.lastMergeTarget'),"person:neighbor");
  select.options=[{value:"cleaner"}];select.value="cleaner";
  run('mergePerson("neighbor")');
  assert.equal(select.value,"cleaner");
  run('state.lastMergeTarget="person:deleted";restoreMergeTarget("person:")');
  assert.equal(select.value,"cleaner");
});
test("logout or expiry resets preferences and expiry during refresh cannot restore target", async () => {
  const {run}=setup();
  const select=run('$("#modal-target")');
  select.options=[{value:"person:neighbor"}]; select.value="person:neighbor";
  run('state.pendingOpen=true;state.lastMergeTarget="person:neighbor";showLogin()');
  assert.equal(run('state.pendingOpen'),false);
  assert.equal(run('state.lastMergeTarget'),"");
  run('state.csrf="synthetic";mutate=async()=>showLogin();mergeCluster("c1")');
  await run('state.modalHandler()');
  assert.equal(run('state.lastMergeTarget'),"");
});

test("correction requires every explicit reason and checkbox and binds exact reviewed target", async () => {
  const {run,nodes}=setup();
  run('let captured=null;mutate=async(p,m,b)=>{captured=b};reviewMergeConflicts("c1","neighbor",{items:[{id:"conflict-1",left:{},right:{}}],review_revision:"review-hash"})');
  assert.ok(nodes.get("#modal-body").innerHTML.includes("可能不是触发冲突的同一帧"));
  assert.ok(!nodes.get("#modal-body").innerHTML.includes("checked"));
  await assert.rejects(run('state.modalHandler()'),/请先选择/);
  assert.equal(run('captured'),null);
  run('$("#conflict-reason-0").value="reflection";');
  await assert.rejects(run('state.modalHandler()'),/确认已核对/);
  run('$("#conflict-confirm-0").checked=true;');
  await run('state.modalHandler()');
  assert.equal(run('captured.target_person_id'),"neighbor");
  assert.equal(run('captured.conflict_corrections[0].id'),"conflict-1");
  assert.equal(run('captured.review_revision'),"review-hash");
});

test("cancel while conflict lookup is pending cannot reopen dialog or submit", async () => {
  const {run}=setup();
  const select=run('$("#modal-target")');
  select.options=[{value:"person:neighbor"}];select.value="person:neighbor";
  run('let resolveReview;let submitted=false;api=()=>new Promise(r=>resolveReview=r);mutate=async()=>{submitted=true};mergeCluster("c1")');
  const work=run('state.modalHandler()');
  run('modal.close();resolveReview({items:[{id:"conflict",left:{},right:{}}]})');
  await work;
  assert.equal(run('modal.open'),false);
  assert.equal(run('submitted'),false);
});

test("paged conflicts retain independent decisions and still require every reviewed item", async () => {
  const {run,nodes}=setup();
  run('let captured=null;mutate=async(p,m,b)=>{captured=b};reviewMergeConflicts("c1","neighbor",{items:[{id:"first",left:{},right:{}},{id:"second",left:{},right:{}}],review_revision:"revision"})');
  const pages=[{hidden:false,querySelector(){return {pause(){}};}},{hidden:true,querySelector(){return {pause(){}};}}];
  run('document.querySelectorAll=()=>[]');
  // Real navigation only changes hidden state; inputs remain in the same form.
  const query=run('document');
  query.querySelectorAll=selector=>selector==='.conflict-item'?pages:[];
  run('$("#conflict-reason-0").value="reflection";$("#conflict-confirm-0").checked=true;showConflict(1)');
  assert.deepEqual(pages.map(p=>p.hidden),[true,false]);
  assert.equal(nodes.get('#conflict-position').textContent,'第 2 / 2 条冲突');
  assert.equal(nodes.get('[data-action="conflict-next"]').disabled,true);
  await assert.rejects(run('state.modalHandler()'),/冲突 2/);
  assert.equal(run('captured'),null);
  run('$("#conflict-reason-1").value="duplicate_detection";$("#conflict-confirm-1").checked=true;showConflict(0)');
  assert.equal(run('$("#conflict-reason-0").value'),'reflection');
  assert.equal(run('$("#conflict-confirm-0").checked'),true);
  await run('state.modalHandler()');
  assert.deepEqual(JSON.parse(run('JSON.stringify(captured.conflict_corrections)')),[{id:'first',reason:'reflection'},{id:'second',reason:'duplicate_detection'}]);
});

test("submission locates the first incomplete conflict without sending partial corrections", async () => {
  const {run,nodes}=setup();
  run('let sent=false;mutate=async()=>{sent=true};reviewMergeConflicts("c1","neighbor",{items:[{id:"first",left:{},right:{}},{id:"second",left:{},right:{}}],review_revision:"revision"})');
  const pages=[{hidden:true,querySelector(){return null;}},{hidden:false,querySelector(){return null;}}];
  run('document').querySelectorAll=selector=>selector==='.conflict-item'?pages:[];
  run('$("#conflict-reason-1").value="reflection";$("#conflict-confirm-1").checked=true');
  await assert.rejects(run('state.modalHandler()'),/冲突 1/);
  assert.equal(run('sent'),false);
  assert.equal(pages[0].hidden,false);
  assert.equal(nodes.get('#conflict-position').textContent,'第 1 / 2 条冲突');
});
