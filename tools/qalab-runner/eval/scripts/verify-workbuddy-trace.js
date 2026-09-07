// scripts/verify-workbuddy-trace.js —— 纯函数离线测（不需真机）
const assert = require('assert');
const { parseFooter, parseDuration } = require('../src/workbuddy-dom-trace');

// spike/侦察实测 footer 文本：「腾讯元宝提供搜索技术支持\n来源\n共消耗\n2.43\n均衡 (MiniMax-M3)\n15:26」
const r = parseFooter('腾讯元宝提供搜索技术支持\n来源\n共消耗\n2.43\n均衡 (MiniMax-M3)\n15:26');
assert.strictEqual(r.beanCost, '2.43', `beanCost 应 2.43，实得 ${r.beanCost}`);
assert.strictEqual(r.model, '均衡 (MiniMax-M3)', `model 应 均衡(MiniMax-M3)，实得 ${r.model}`);

// 另一档模型样本（spike 首轮）：均衡 (Deepseek-V4-Pro)
const r2 = parseFooter('共消耗\n8.39\n均衡 (Deepseek-V4-Pro)\n14:11');
assert.strictEqual(r2.beanCost, '8.39', `beanCost 应 8.39，实得 ${r2.beanCost}`);
assert.strictEqual(r2.model, '均衡 (Deepseek-V4-Pro)', `model 应 均衡(Deepseek-V4-Pro)，实得 ${r2.model}`);

// 耗时：思考折叠头「已完成 17s」
assert.strictEqual(parseDuration('已完成 17s'), '17', `duration 应 17，实得 ${parseDuration('已完成 17s')}`);
assert.strictEqual(parseDuration('已完成 3s'), '3', `duration 应 3，实得 ${parseDuration('已完成 3s')}`);
assert.strictEqual(parseDuration('思考中'), '', `无完成标记应空，实得 ${parseDuration('思考中')}`);

console.log('PASS: parseFooter + parseDuration');
