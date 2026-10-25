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

test("owner is direct and food delivery groups preserve separate identities without numbered names", () => {
  const {scope,node}=context();
  vm.runInContext('state.ownerName="Synthetic Owner";state.people=[{id:"owner",relationship:"self",display_name:"我 1",matched_events:2,distinct_days:1},{id:"delivery-a",relationship:"food_delivery",display_name:"外卖 1",matched_events:3,distinct_days:2},{id:"delivery-b",relationship:"food_delivery",display_name:"外卖 2",matched_events:1,distinct_days:1}];renderPeopleContent()',scope);
  assert.ok(node.innerHTML.includes('owner-entry'));
  assert.ok(!node.innerHTML.includes('data-category="self"'));
  assert.equal((node.innerHTML.match(/data-category="food_delivery"/g)||[]).length,1);
  assert.ok(node.innerHTML.includes('Synthetic Owner'));
  assert.ok(!node.innerHTML.includes('外卖 1')&&!node.innerHTML.includes('外卖 2'));
  assert.ok(node.innerHTML.includes('data-id="delivery-a"')&&node.innerHTML.includes('data-id="delivery-b"'));
  assert.ok(!node.innerHTML.includes('data-action="merge-person"'));
  assert.ok(vm.runInContext('relationshipSelect("self")',scope).includes('Synthetic Owner'));
});
test("one entry per category, pending first, names escaped and actions retained", () => {
  const {scope,node}=context();
  vm.runInContext('state.people=[{id:"a",relationship:"friend",display_name:"<script>bad</script>",matched_events:2,distinct_days:1},{id:"b",relationship:"friend",display_name:"Synthetic B",matched_events:3,distinct_days:2}]; renderPeopleContent()',scope);
  assert.equal((node.innerHTML.match(/class="person-category(?:"| )/g)||[]).length,10);
  assert.ok(node.innerHTML.indexOf('id="review-clusters"')<node.innerHTML.indexOf('id="people-heading"'));
  assert.ok(node.innerHTML.includes("&lt;script&gt;bad&lt;/script&gt;"));
  assert.ok(!node.innerHTML.includes("<script>"));
  assert.equal((node.innerHTML.match(/data-action="rename-person"/g)||[]).length,2);
  assert.equal((node.innerHTML.match(/data-action="merge-person"/g)||[]).length,2);
});

test("grouped categories have one visible identity-free entry and keep all separate records", () => {
  const {scope}=context();
  for (const [key,label] of [["food_delivery","外卖"],["cleaner","保洁"],["stranger","陌生人"]]) {
    const result=vm.runInContext(`personGroup({key:${JSON.stringify(key)},label:${JSON.stringify(label)},people:[{id:"first",relationship:${JSON.stringify(key)},display_name:"old 1"},{id:"second",relationship:${JSON.stringify(key)},display_name:"old 2"}]})`,scope);
    const primary=result.split('<details class="category-maintenance">')[0];
    assert.equal((primary.match(/<article /g)||[]).length,1);
    assert.equal((primary.match(/<h3>/g)||[]).length,1);
    assert.ok(primary.includes(`<h3>${label}</h3>`));
    assert.ok(!result.includes('old 1')&&!result.includes('old 2'));
    assert.ok(result.includes('data-id="first"')&&result.includes('data-id="second"'));
    assert.ok(!result.includes('data-action="merge-person"'));
    const empty=vm.runInContext(`personGroup({key:${JSON.stringify(key)},label:${JSON.stringify(label)},people:[]})`,scope);
    assert.ok(empty.includes('暂无已确认样本'));
    assert.ok(!empty.includes('data-action="view-category"'));
  }
});

test("category gallery navigates only its own independent records and enforces boundaries", () => {
  const nodes=new Map();
  const node=()=>({textContent:"",hidden:false,open:false,setAttribute(){},replaceChildren(){},append(){},showModal(){this.open=true;}});
  const scope={document:{querySelector(s){if(!nodes.has(s))nodes.set(s,node());return nodes.get(s);},createElement:node},DoorlockTime:{dayLabel:()=>"",formatDate:()=>""}};
  vm.createContext(scope);vm.runInContext(source.slice(0,source.indexOf('document.addEventListener("click"')),scope);
  vm.runInContext('state.people=[{id:"a",relationship:"stranger",face_url:"/first"},{id:"other",relationship:"friend"},{id:"b",relationship:"stranger",preview_url:"/last"}];viewCategory("stranger")',scope);
  assert.equal(nodes.get('#person-viewer-position').textContent,'样本 1 / 2');
  assert.equal(nodes.get('[data-action="previous-person-sample"]').disabled,true);
  vm.runInContext('showViewerPerson(1)',scope);
  assert.equal(vm.runInContext('state.viewedPerson.id',scope),'b');
  assert.equal(vm.runInContext('state.viewerKind',scope),'scene');
  assert.equal(nodes.get('[data-action="next-person-sample"]').disabled,true);
  vm.runInContext('showViewerPerson(2);showViewerPerson(-1)',scope);
  assert.equal(vm.runInContext('state.viewedPerson.id',scope),'b');
  vm.runInContext('viewPerson("a")',scope);
  assert.equal(nodes.get('#person-viewer-pagination').hidden,true);
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
