// 模块刷新纯逻辑:从选择器行里筛出某模块(page)的 key。page 为空串不视为具体模块(返回空)。
export function keysOfModule(rows, page) {
  if (!page) return [];
  return (rows || []).filter((r) => (r.page || "") === page);
}

// 该模块 key 里被 verify 判为失效(false)的 key 名列表(供"按模块刷新"聚焦重探)。
export function staleKeysFromVerify(verifyResult, moduleKeys) {
  const v = verifyResult || {};
  const inModule = new Set((moduleKeys || []).map((r) => r.key));
  return Object.entries(v).filter(([k, ok]) => inModule.has(k) && !ok).map(([k]) => k);
}
