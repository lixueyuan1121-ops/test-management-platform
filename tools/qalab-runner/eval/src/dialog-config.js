'use strict';

const normalize = value => String(value || '').replace(/\s/g, '').replace(/（/g, '(').replace(/）/g, ')').toLowerCase();
function configError(message) { return new Error(`[CONFIG_ERROR] ${message}`); }

async function readSelection(locator) {
  if (!locator || !await locator.count()) return [];
  return locator.evaluate(root => {
    const values = new Set();
    const walk = node => {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.textContent.trim()) values.add(node.textContent.trim());
        return;
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (!node.getClientRects().length || getComputedStyle(node).visibility === 'hidden') return;
        if (node !== root && node.matches('[role="menu"], [role="listbox"]')) return;
        for (const name of ['aria-label', 'title', 'data-value']) {
          const value = node.getAttribute(name);
          if (value) values.add(value);
        }
        if (node.shadowRoot) for (const child of node.shadowRoot.childNodes) walk(child);
      }
      for (const child of node.childNodes) walk(child);
    };
    walk(root);
    return [...values];
  });
}

async function verifySelection(page, locator, wanted) {
  let values = [];
  for (let i = 0; i < 10; i++) {
    values = await readSelection(locator);
    if (values.some(value => normalize(value) === normalize(wanted))) return values;
    await page.waitForTimeout(200);
  }
  throw configError(`请求「${wanted}」，实际控件显示「${values.join(' / ') || '无法读取'}」`);
}

module.exports = { readSelection, verifySelection, configError, normalize };
