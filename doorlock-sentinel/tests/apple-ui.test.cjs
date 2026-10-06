const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../src/doorlock_sentinel/static/app.js"), "utf8");
function context() {
  const node = {innerHTML:""};
  const scope = {document:{querySelector:()=>node}, DoorlockTime:{dayLabel:()=>"",formatDate:()=>""}};
  vm.createContext(scope);
  vm.runInContext(source.slice(0, source.indexOf('document.addEventListener("click"')), scope);
  return {scope,node};
}
test("all categories remain ordered and unknown relationships are retained", () => {
  const {scope} = context();
  const result = vm.runInContext('peopleGroups([{id:"a",relationship:"friend"},{id:"b",relationship:"food_delivery"},{id:"c",relationship:"future"},{id:"d",relationship:"__proto__"}])',scope);
  assert.deepEqual(Array.from(result,x=>x.key),["self","family","friend","neighbor","food_delivery","courier","cleaner","visitor","stranger","other"]);
  assert.equal(result.flatMap(x=>x.people).length,4);
  assert.equal(result.find(x=>x.key==="other").people.length,2);
});
test("one entry per category, pending first, names escaped and actions retained", () => {
  const {scope,node}=context();
  vm.runInContext('state.people=[{id:"a",relationship:"friend",display_name:"<script>bad</script>",matched_events:2,distinct_days:1},{id:"b",relationship:"friend",display_name:"Synthetic B",matched_events:3,distinct_days:2}]; renderPeopleContent()',scope);
  assert.equal((node.innerHTML.match(/class="person-category"/g)||[]).length,10);
  assert.ok(node.innerHTML.indexOf('id="review-clusters"')<node.innerHTML.indexOf('id="people-heading"'));
  assert.ok(node.innerHTML.includes("&lt;script&gt;bad&lt;/script&gt;"));
  assert.ok(!node.innerHTML.includes("<script>"));
  assert.equal((node.innerHTML.match(/data-action="rename-person"/g)||[]).length,2);
  assert.equal((node.innerHTML.match(/data-action="merge-person"/g)||[]).length,2);
});
test("empty state has recovery path and CSS retains accessibility preferences", () => {
  const {scope,node}=context();
  vm.runInContext("renderPeopleContent()",scope);
  assert.ok(node.innerHTML.includes('data-action="scroll-to-clusters"'));
  const css=fs.readFileSync(require("node:path").join(__dirname,"../src/doorlock_sentinel/static/apple-design.css"),"utf8");
  for(const marker of [":focus-visible","min-width: 44px","prefers-reduced-motion","prefers-reduced-transparency","prefers-contrast","overflow-x: auto"]) assert.ok(css.includes(marker));
});
