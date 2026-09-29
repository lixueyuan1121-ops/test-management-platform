// Resolve fixture roles by exact registered locators, independent of generated key names.
// Never infer readiness from a description, a navigation button, or fuzzy home text.
import { isActiveCandidate } from './gui-mcp/runtime-loader.mjs';
const ROLES = {
  ready: { names: ['homepageTitle', 'homeGreetingTitle'],
    testids: ['home-greeting-title', 'home-main'],
    css: ['.home-skill-guidance__title', '.home-skill-guidance__title-text',
      '.home-revamp-title__text', 'h1.home-revamp-title__text', '.homepage-title__text',
      '.home-skill-guidance__main', '.home-revamp-main'] },
  navigation: { names: ['navHome'], testids: ['nav-home'], css: [] },
  login: { names: ['loginModal'], testids: [], css: ['.login-modal-wrap'] },
};
export function homeRoleKeys(registry, role, preferred = []) {
  const spec = ROLES[role];
  if (!spec) throw new Error('Unknown homepage role: ' + role);
  const names = [...new Set([...preferred, ...spec.names])];
  if (!registry || typeof registry !== 'object') return names;
  const allowed = entry => entry && entry.change_status !== 'retired' && entry.status !== 'retired';
  const matches = candidate => {
    if (candidate.src === 'audit_collection' || candidate.nth !== undefined || candidate.has_text !== undefined) return false;
    if (candidate.by === 'testid') return spec.testids.includes(candidate.value);
    if (candidate.by !== 'css') return false;
    const css = candidate.value.trim();
    if (spec.css.includes(css)) return true;
    const attr = css.match(/^\[data-testid=(?:"([^"\n]+)"|'([^'\n]+)')\]$/);
    return !!attr && spec.testids.includes(attr[1] || attr[2]);
  };
  const found = names.filter(key => allowed(registry[key]));
  for (const [key, entry] of Object.entries(registry)) {
    if (!allowed(entry) || found.includes(key)) continue;
    const candidates = (entry.candidates || []).filter(isActiveCandidate);
    // All usable fallbacks must refer to the same fixture role.
    if (candidates.length && candidates.every(matches)) found.push(key);
  }
  return found;
}
