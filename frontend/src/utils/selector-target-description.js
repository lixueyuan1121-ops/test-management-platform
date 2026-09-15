// Only use evidence attached to this key; never guess an element from its English name.
export function describeSelectorTarget(key, script) {
  try { if (typeof script === 'string') script = JSON.parse(script); } catch { script = []; }
  const steps = Array.isArray(script) ? script : [];
  return steps.flatMap((step, index) => {
    if (step?.target?.key !== key) return [];
    const description = typeof step.desc === 'string' ? step.desc.trim() : '';
    const placeholder = description.match(/[（(]待补[：:]\s*([^）)]+)[）)]/);
    const expected = step.action === 'assert_text' && !step.args?.negate && typeof step.args?.expected === 'string' ? step.args.expected.trim() : '';
    const title = placeholder?.[1]?.trim() || description || (expected ? `显示“${expected}”的元素` : '脚本未说明具体元素');
    const previous = steps.slice(0, index).reverse().find(s => s?.target?.key !== key && typeof s?.desc === 'string' && s.desc.trim());
    return [{ title, description, step: index + 1, before: previous?.desc?.trim() || '', expected }];
  });
}

export function selectorTargetNotes(hint, context = '') {
  if (Array.isArray(hint?.targets) && hint.targets.length) return hint.targets;
  // Old links already contain per-key context, which is safer than a case-wide title.
  if (typeof context === 'string' && context.trim()) return [{ title: context.trim() }];
  return [{ title: '暂无具体元素说明，请补充脚本中的操作描述' }];
}
