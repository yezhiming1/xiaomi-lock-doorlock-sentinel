const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8');
test('prototype has no network, credentials or persistence', () => {
  const js = read('preview.js');
  assert.doesNotMatch(js, /\b(?:fetch|XMLHttpRequest|WebSocket|localStorage|sessionStorage)\s*(?:\(|\.)/);
  const html = read('index.html');
  assert.doesNotMatch(html, /(?:src|href)=["']https?:|type=["']password/);
  assert.match(html, /所有内容均为合成示例/);
});
test('categories keep delivery above courier without classifying unknown people', () => {
  const js = read('preview.js');
  assert.ok(js.indexOf("['外卖','外',1]") < js.indexOf("['快递','快',1]"));
  assert.match(js, /未知人物不预分类/);
  assert.match(js, /showModal\(\)/);
  assert.match(js, /opener\.focus\(\)/);
});
test('accessibility and mobile fixed navigation safeguards exist', () => {
  const css = read('preview.css');
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /prefers-reduced-transparency/);
  assert.match(css, /prefers-contrast/);
  assert.match(css, /@media\(max-width:680px\)\{aside\{backdrop-filter:none\}\}/);
  assert.match(read('index.html'), /<h1 id="title" tabindex="-1"/);
});
const luminance = hex => {
  const c = hex.match(/[a-f\d]{2}/gi).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);
  return c[0]*.2126+c[1]*.7152+c[2]*.0722;
};
test('candidate text and action colors meet 4.5:1 against their solid backgrounds', () => {
  const css=read('preview.css');
  const token = name => css.match(new RegExp('--'+name+':(#[a-f\\d]{6})'))[1];
  for(const [foreground,background] of [[token('ink'),'#ffffff'],[token('muted'),'#f5f5f7'],[token('blue'),'#ffffff'],[token('green'),'#eef7f1'],[token('amber'),'#fbf2df'],[token('danger'),'#e9f1ff']]){
    const a=luminance(foreground),b=luminance(background);
    assert.ok((Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=4.5, foreground);
  }
});
