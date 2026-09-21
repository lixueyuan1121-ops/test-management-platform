import test from 'node:test';
import assert from 'node:assert/strict';
import {suggestKey,elementStatus as legacyElementStatus,createElementStatusMatcher,sameFrameDomain} from './probe-batch.js';

// bug3:「加为 key-更新已有」的 frame 校验须按作用域等价,vm/shell/auto/content 归一后同域,
// 不该把 auto vs vm、shell vs vm 当成"frame 不同"误拦;深层 iframe(url:...)才真隔离。
test('sameFrameDomain treats vm/shell/auto/content as one domain, isolates deep iframes',()=>{
  assert.ok(sameFrameDomain('auto','vm'));
  assert.ok(sameFrameDomain('shell','vm'));
  assert.ok(sameFrameDomain('content','auto'));
  assert.ok(sameFrameDomain(undefined,'vm'));   // 缺省 frame 视为 auto→main
  assert.ok(sameFrameDomain('url:host/app','url:host/app'));
  assert.ok(!sameFrameDomain('url:host/app','vm'));
  assert.ok(!sameFrameDomain('url:host/a','url:host/b'));
  assert.ok(sameFrameDomain('main','vm',{main:'vm'}));   // 别名表优先
});

const el={tag:'button',text:'复制',_frameMatch:'shell',candidates:[{by:'css',value:'.message .copy-button'}]};
test('unique semantic keys without testid, bounded length',()=>{const used=new Set();const a=suggestKey(el,'对话',used),b=suggestKey(el,'对话',used);assert.match(a,/copy_button_/);assert.notEqual(a,b);assert.ok(a.length<=64);});
test('same scoped CSS is already registered, broad and repeated candidates are not',()=>{const rows=[{key:'copyButton',frame:'shell',candidates:el.candidates}];assert.equal(elementStatus(el,rows,[el]).key,'copyButton');assert.equal(elementStatus(el,rows,[el,{...el}]).type,'new');const generic={...el,candidates:[{by:'css',value:'button[type="button"]'}]};assert.equal(elementStatus(generic,[{...rows[0],candidates:generic.candidates}],[generic]).type,'new');});
test('retired candidates do not count as registered',()=>assert.equal(elementStatus(el,[{key:'copy',frame:'shell',candidates:[{...el.candidates[0],status:'retired'}]}],[el]).type,'new'));

// bug: 同一 testid 被登记成多个重复 key 时,元素仍应判「已存在」(否则一直显示"未添加"、可反复重复添加)。
test('duplicate testid across multiple keys still resolves to exists',()=>{
  const tel={tag:'button',text:'新建项目',_frameMatch:'shell',candidates:[{by:'testid',value:'project-create-btn'},{by:'css',value:'.project-sidebar__create-btn'}]};
  const one=[{key:'projectCreateBtn',frame:'vm',candidates:[{by:'testid',value:'project-create-btn'}]}];
  assert.equal(elementStatus(tel,one,[tel]).type,'exists');
  const dup=[...one,
    {key:'project_create_button_10hpap8',frame:'shell',candidates:[{by:'testid',value:'project-create-btn'}]},
    {key:'project_create_button_10hpap8_2',frame:'shell',candidates:[{by:'testid',value:'project-create-btn'}]}];
  const s=elementStatus(tel,dup,[tel]);
  assert.equal(s.type,'exists');            // 命中 3 个重复 key 仍是"已存在",不再误判 new
  assert.equal(s.keys.length,3);            // 上层可据此提示去重
});

// bug: 无 testid、与兄弟共享 class 的元素(语音/发送图标同 .chat-input-button__icon),
// css 无法区分身份→旧逻辑永远判 new→反复重复添加。应靠位置唯一 xpath 精确匹配判「已存在」。
test('shared-class element resolves to exists via unique xpath identity',()=>{
  const xp='//*[@id="app"]/div[2]/div[3]';
  // 三个兄弟共享 .chat-input-button__icon,当前元素带位置唯一 xpath
  const voice={tag:'div',text:'',_frameMatch:'shell',uniqueXPath:xp,candidates:[{by:'css',value:'.chat-input-button__icon'}]};
  const siblings=[voice,{...voice,uniqueXPath:'//*[@id="app"]/div[2]/div[4]'},{...voice,uniqueXPath:'//*[@id="app"]/div[2]/div[5]'}];
  // 无 xpath 时:共享 class 不唯一→判 new(先天无法区分)
  assert.equal(elementStatus({...voice,uniqueXPath:''},[],siblings).type,'new');
  // 已注册 key 存了同一条位置 xpath→精确命中→exists(不再重复添加)
  const rows=[{key:'yuyinbutton',frame:'shell',candidates:[{by:'xpath',value:xp},{by:'css',value:'.chat-input-button__icon'}]}];
  assert.equal(elementStatus(voice,rows,siblings).key,'yuyinbutton');
  // 另一个兄弟(不同位置 xpath)不会误命中该 key
  assert.equal(elementStatus(siblings[1],rows,siblings).type,'new');
});

test('flat page aliases match vm registry to main-frame probe',()=>{assert.equal(elementStatus(el,[{key:'copy',frame:'vm',candidates:el.candidates}],[el],{vm:'shell'}).key,'copy')});

// 注册表 frame='auto'(运行时=任意 frame 通配)应匹配任意具体 frame 的探测元素。
test('auto-frame registry matches any concrete probe frame',()=>{
  const rows=[{key:'copyButton',frame:'auto',candidates:el.candidates}];
  assert.equal(elementStatus(el,rows,[el]).key,'copyButton');
  assert.equal(elementStatus({...el,_frameMatch:'vm'},rows,[{...el,_frameMatch:'vm'}]).key,'copyButton');
});
// 探测候选带 exact:true,注册表同类候选无 exact 字段;比对不应因 exact 缺省差异而错判为「添加」。
test('exact flag difference does not break match',()=>{
  const probe={tag:'button',text:'账号登录',_frameMatch:'vm',candidates:[{by:'text',value:'账号登录',exact:true}]};
  const rows=[{key:'loginTab',frame:'auto',candidates:[{by:'text',value:'账号登录'}]}];
  assert.equal(elementStatus(probe,rows,[probe]).key,'loginTab');
});

// ---- 方案 A:testid 全局唯一,匹配无视 frame ----
// 库里 key 注册为 frame=vm,但导航按钮探测时在 shell frame;testid 相同就该判「已存在」。
test('testid matches regardless of frame (vm registry vs shell probe)',()=>{
  const rows=[{key:'messageInput',frame:'vm',candidates:[{by:'testid',value:'message-input'}]}];
  const probe={_frameMatch:'shell',candidates:[{by:'testid',value:'message-input'}]};
  assert.equal(elementStatus(probe,rows,[probe]).key,'messageInput');
});
// 探测侧 testid 以 css [data-testid=x] 形式出现时同样命中(candidateIdentity 已归一)。
test('testid in css form matches across frames',()=>{
  const rows=[{key:'navHome',frame:'vm',candidates:[{by:'testid',value:'nav-home'}]}];
  const probe={_frameMatch:'shell',candidates:[{by:'css',value:'[data-testid="nav-home"]'}]};
  assert.equal(elementStatus(probe,rows,[probe]).key,'navHome');
});
// 不同 testid 不应命中(防误判)。
test('different testid does not match',()=>{
  const rows=[{key:'navHome',frame:'vm',candidates:[{by:'testid',value:'nav-home'}]}];
  const probe={_frameMatch:'shell',candidates:[{by:'testid',value:'nav-settings'}]};
  assert.equal(elementStatus(probe,rows,[probe]).type,'new');
});

// ---- 方案 C:非 testid 候选,vm/shell/content/auto 视为同一主域(桌面版 vm↔shell 错配) ----
test('non-testid css matches across vm/shell main domain',()=>{
  const rows=[{key:'sendBtn',frame:'vm',candidates:[{by:'css',value:'.chat-editor__send'}]}];
  const probe={_frameMatch:'shell',candidates:[{by:'css',value:'.chat-editor__send'}]};
  assert.equal(elementStatus(probe,rows,[probe]).key,'sendBtn');
});
// 深层 iframe(url:...)是真正隔离域,不与主域归一。
test('deep iframe url frame stays isolated for non-testid',()=>{
  const rows=[{key:'sendBtn',frame:'vm',candidates:[{by:'css',value:'.chat-editor__send'}]}];
  const probe={_frameMatch:'url:https://x.com/a',candidates:[{by:'css',value:'.chat-editor__send'}]};
  assert.equal(elementStatus(probe,rows,[probe]).type,'new');
});
// ---- css 类:注册单类应匹配探测的类拼接(子集),不要求整串相等 ----
test('registry single class matches probe concatenated classes',()=>{
  const rows=[{key:'sendBtn',frame:'vm',candidates:[{by:'css',value:'.chat-editor__send'}]}];
  const probe={_frameMatch:'vm',candidates:[{by:'css',value:'.chat-editor.chat-editor__send'}]};
  assert.equal(elementStatus(probe,rows,[probe]).key,'sendBtn');
});

function elementStatus(el, rows, elements, aliases = {}) {
  const actual = createElementStatusMatcher(rows, elements, aliases)(el)
  assert.deepEqual(actual, legacyElementStatus(el, rows, elements, aliases))
  return actual
}

test('indexed matcher preserves candidate/frame semantics across mixed snapshots', () => {
  const pool = [
    {by:'css',value:'.a'}, {by:'css',value:'.a.b'}, {by:'css',value:'.b.c'},
    {by:'css',value:'#unique'}, {by:'testid',value:'save'},
    {by:'css',value:'[data-testid="save"]'}, {by:'testid',value:'save.icon'}, {by:'css',value:'[data-testid="save.icon"]'}, {by:'role',value:'button',name:'Save'},
    {by:'xpath',value:'/html/button[1]'}, {by:'text',value:'hello',exact:true},
    {by:'css',value:'.c',status:'retired'},
  ]
  const frames = ['auto', 'vm', 'url:other', 'alias']
  const aliases = {alias:'vm'}
  for (let seed = 1; seed < 30; seed++) {
    const elements = Array.from({length:35}, (_, i) => ({
      _frameMatch:frames[(i + seed) % frames.length],
      candidates:[pool[(i * seed) % pool.length], pool[(i + seed) % pool.length]],
      uniqueXPath:i % 7 === 0 ? '/html/button[1]' : '',
    }))
    const rows = Array.from({length:24}, (_, i) => ({key:`key${i}`,frame:frames[i % 4],candidates:[pool[(i + seed) % pool.length]]}))
    const match = createElementStatusMatcher(rows, elements, aliases)
    for (const el of elements) assert.deepEqual(match(el), legacyElementStatus(el, rows, elements, aliases))
  }
})

test('10000 elements reuse the snapshot index and cached statuses', () => {
  const elements = Array.from({length:10000}, (_, i) => ({_frameMatch:'shell',candidates:[{by:'css',value:'.shared'}, {by:'testid',value:`id-${i}`}]}))
  const rows = elements.map((el, i) => ({key:`key-${i}`,frame:'vm',candidates:[el.candidates[1]]}))
  const start = performance.now()
  const match = createElementStatusMatcher(rows, elements)
  elements.forEach((el, i) => { assert.equal(match(el).key, `key-${i}`); assert.equal(match(el), match(el)) })
  const elapsed = performance.now() - start
  console.log(`10000-element snapshot: ${elapsed.toFixed(0)}ms`)
  assert.ok(elapsed < 10000, `matching took ${elapsed}ms`)
})
test('unique controls sharing a container class use the rarest class posting', () => {
  const elements = Array.from({length:10000}, (_, i) => ({candidates:[{by:'css',value:`.shared .control-${i}`}]}))
  const start = performance.now()
  const match = createElementStatusMatcher([], elements)
  elements.forEach(el => assert.equal(match(el).type, 'new'))
  const elapsed = performance.now() - start
  console.log(`10000 compound CSS controls: ${elapsed.toFixed(0)}ms`)
  assert.ok(elapsed < 10000)
})
