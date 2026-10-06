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

test("confirmed avatars are accessible buttons with escaped identifiers", () => {
  const {scope}=context();
  const html=vm.runInContext('personCard({id:"x\\\"<",display_name:"<名字>",face_url:"/api/artifacts/face",matched_events:1,distinct_days:1})',scope);
  assert.ok(html.includes('data-action="view-person"'));
  assert.ok(html.includes('aria-label="查看&lt;名字&gt;的大图"'));
  assert.ok(html.includes('data-id="x&quot;&lt;"'));
  assert.ok(!html.includes('data-id="x"<"'));
});

test("viewer loads matching images, handles failure, absence and stale callbacks", () => {
  const nodes=new Map(), images=[];
  function node() { return {textContent:"",hidden:false,attrs:{},children:[],setAttribute(k,v){this.attrs[k]=v;},replaceChildren(){this.children=[];},append(img){this.children.push(img);},showModal(){this.open=true;}}; }
  const scope={document:{querySelector(selector){if(!nodes.has(selector)) nodes.set(selector,node());return nodes.get(selector);},createElement(){const img=node();images.push(img);return img;}},DoorlockTime:{dayLabel:()=>"",formatDate:()=>""}};
  vm.createContext(scope);
  vm.runInContext(source.slice(0,source.indexOf('document.addEventListener("click"')),scope);
  vm.runInContext('state.people=[{id:"a",display_name:"合成人物",face_url:"/api/artifacts/face",preview_url:"/api/artifacts/scene"}]; viewPerson("a")',scope);
  assert.equal(images[0].src,"/api/artifacts/face");
  assert.equal(nodes.get("#person-viewer-media").attrs["aria-busy"],"true");
  vm.runInContext('state.viewerKind="scene";loadPersonImage()',scope);
  assert.equal(images[1].src,"/api/artifacts/scene");
  images[0].onerror();
  assert.equal(nodes.get('[data-action="retry-person-viewer"]').hidden,true);
  images[1].onerror();
  assert.equal(nodes.get('[data-action="retry-person-viewer"]').hidden,false);
  vm.runInContext("loadPersonImage()",scope);
  images[2].onload();
  assert.equal(images[2].hidden,false);
  assert.equal(nodes.get("#person-viewer-media").attrs["aria-busy"],"false");
  vm.runInContext('state.viewedPerson={};loadPersonImage()',scope);
  assert.equal(images.length,3);
  assert.ok(nodes.get("#person-viewer-status").textContent.includes("暂无"));
});
