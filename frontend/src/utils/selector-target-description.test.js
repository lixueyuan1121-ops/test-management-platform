import { test } from 'node:test';
import assert from 'node:assert/strict';
import { describeSelectorTarget, selectorTargetNotes } from './selector-target-description.js';
import { collectMissingKeys } from './bulk-fix-selectors.js';
test('复制提示与复制按钮分开，并带出使提示出现的操作', () => {
 const script = [{ action: 'click', target: {key:'copyButton'}, desc:'点击历史回答下方的复制按钮' },
 {action:'wait',target:{key:'copyToast'},desc:'等待复制成功提示（待补：复制成功 toast 元素）'},
 {action:'assert_text',target:{key:'copyToast'},args:{expected:'复制成功'},desc:'断言复制成功提示出现'}];
 const notes = describeSelectorTarget('copyToast',JSON.stringify(script));
 assert.equal(notes[0].title,'复制成功 toast 元素');
 assert.equal(notes[0].step,2); assert.match(notes[0].before,/点击历史回答/);
 assert.equal(notes[1].expected,'复制成功'); assert.equal(notes.length,2);
 const data = collectMissingKeys([{selector_fix:true,selector_fix_keys:['copyToast'],script}],[]);
 assert.deepEqual(data.hints.copyToast.targets,notes);
});
test('没有说明时不根据 key 猜首页；旧链接使用该 key 专属上下文', () => {
 assert.deepEqual(describeSelectorTarget('homeModeQuickOption','invalid'),[]);
 assert.equal(describeSelectorTarget('homeModeQuickOption',[{target:{key:'homeModeQuickOption'}}])[0].title,'脚本未说明具体元素');
 assert.equal(selectorTargetNotes({},'等待复制成功提示')[0].title,'等待复制成功提示');
 assert.match(selectorTargetNotes({})[0].title,/暂无/);
 assert.equal(describeSelectorTarget('x',[{target:{key:'x'},action:'assert_text',args:{expected:'禁用',negate:true}}])[0].expected,'');
});
