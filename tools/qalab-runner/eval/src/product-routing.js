'use strict';
// 未识别的产品不能落入纳米Work执行器。
function partitionProducts(items) {
  const out = { namiwork: [], workbuddy: [], qwork: [], unsupported: [] };
  for (const item of items) {
    const engine = String(item.target_engine || 'namiwork').toLowerCase();
    (['namiwork', 'workbuddy', 'qwork'].includes(engine) ? out[engine] : out.unsupported).push(item);
  }
  return out;
}
module.exports = { partitionProducts };
