import { test } from 'node:test';
import assert from 'node:assert/strict';
import { homeRoleKeys } from './home-anchors.mjs';
import { resetOrBlock } from './reset-home.mjs';
const entry = (by, value, extra = {}) => ({ candidates: [{ by, value, ...extra }] });
test('audit keys resolve by precise locators without changing the registry', () => {
  const registry = { dom_title: entry('css', '.home-skill-guidance__title'), dom_nav: entry('testid', 'nav-home'), dom_main: entry('css', '[data-testid="home-main"]') };
  const before = JSON.stringify(registry);
  assert.deepEqual(homeRoleKeys(registry, 'ready'), ['dom_title', 'dom_main']);
  assert.deepEqual(homeRoleKeys(registry, 'navigation'), ['dom_nav']);
  assert.equal(JSON.stringify(registry), before);
});
test('navigation, fuzzy text, retired candidates and collections are not readiness proof', () => {
  const registry = { nav: entry('testid', 'nav-home'), text: entry('text', '首页'), fuzzy: entry('css', '[class*="home"]'), retired: entry('testid', 'home-main', {status:'retired'}), collection: entry('css', '.home-skill-guidance__title', {src:'audit_collection'}), learned: entry('testid','home-main',{src:'learned'}), mixed:{candidates:[{by:'testid',value:'home-main'},{by:'css',value:'.anything'}]} };
  assert.deepEqual(homeRoleKeys(registry, 'ready'), []);
});
test('generated home title passes only after real readiness is confirmed', async () => {
  let home = false; let clicked = false;
  const gui = {registry:{dom_title:entry('css','.home-skill-guidance__title'),dom_nav:entry('testid','nav-home')},
    async resetHome(){}, async verifyKeys(keys){return {verify:Object.fromEntries(keys.map(k=>[k,k==='dom_title' && home]))};},
    async click({key}){assert.equal(key,'dom_nav');clicked=true;home=true;}};
  const result = await resetOrBlock(gui,()=>{},{readyTimeout:1,pollMs:1,maxHealRounds:1});
  assert.equal(result.ok,true);assert.equal(clicked,true);
  gui.click=async()=>{};home=false;
  assert.equal((await resetOrBlock(gui,()=>{},{readyTimeout:1,pollMs:1,maxHealRounds:1})).ok,false);
});
test('generated login modal takes precedence over home title', async()=>{
  const gui={registry:{a:entry('testid','home-main'),b:entry('css','.login-modal-wrap')},async resetHome(){},async verifyKeys(keys){return {verify:Object.fromEntries(keys.map(k=>[k,true]))};}};
  const r=await resetOrBlock(gui,()=>{},{readyTimeout:1,pollMs:1});assert.equal(r.ok,false);assert.match(r.result.reason,/重新登录/);
});
