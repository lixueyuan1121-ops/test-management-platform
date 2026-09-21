// Return concrete live matches without acting on the device. Bound the payload
// because probe results are stored in a TEXT column and rendered in a dialog.
export async function inspectSelectionMatches(checker, item, limit = 30) {
  const result = await checker.inspect({ ...(item.target || {}), key: '__selection' }, { all: true });
  const matches = [];
  for (const group of result.matches || []) {
    for (let index = 0; index < group.count && matches.length < limit; index++) {
      const loc = group.loc.nth(index);
      const detail = await loc.evaluate(el => {
        const refs = window.__qalabProbeElements ||= new Map();
        let ref = [...refs].find(([, node]) => node === el)?.[0];
        if (!ref) { ref = `selection-${Date.now()}-${Math.random().toString(36).slice(2)}`; refs.set(ref, el); }
        let uniqueXPath = '';
        if (el.getRootNode() === document) {
          const parts = [];
          for (let node = el; node?.nodeType === 1; node = node.parentElement) {
            let pos = 1;
            for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) if (sib.tagName === node.tagName) pos++;
            parts.unshift(`${node.localName}[${pos}]`);
          }
          uniqueXPath = '/' + parts.join('/');
        }
        return { element_ref: ref, tag: el.localName, text: (el.innerText || el.value || '').trim().slice(0, 160),
          accessibleName: (el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 160), uniqueXPath };
      });
      const { scope, ...candidate } = group.hit;
      matches.push({ ...detail, frame: scope, visible: await loc.isVisible(), absRect: await loc.boundingBox().then(r => r && ({x:r.x,y:r.y,w:r.width,h:r.height})),
        candidate: { ...candidate, nth: candidate.nth ?? index } });
    }
    if (matches.length >= limit) break;
  }
  return { count: result.count, matches, truncated: result.count > matches.length };
}
