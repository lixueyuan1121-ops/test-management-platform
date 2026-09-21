const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{
 const {createAutomationRuntime}=await import('../../backend/app/services/playwright_runtime.mjs');
 const {inspectSelectionMatches}=await import('../../tools/qalab-runner/gui-mcp/selection-matches.mjs');
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  await page.setContent('<button data-testid="action" class="action">Save</button><button data-testid="action" class="action">Cancel</button>');
  const values={testid:'action',xpath:'//button',css:'.action'};
  for(const by of Object.keys(values)) {
   const candidate={by,value:values[by],has_text:'Cancel',primary:true};
   const rt=createAutomationRuntime({page,registry:{x:{frame:'shell',candidates:[candidate]}}});
   assert.equal((await rt.getText({key:'x'})).text,'Cancel');
   assert.equal((await rt.inspect({key:'x'})).hit.has_text,'Cancel');
  }
  const primary=createAutomationRuntime({page,registry:{x:{frame:'shell',candidates:[{by:'testid',value:'action',nth:0},{by:'xpath',value:'//button',has_text:'Cancel',primary:true}]}}});
  assert.equal((await primary.getText({key:'x'})).text,'Cancel');
  await page.setContent('<button class="icon"></button><button class="icon"></button>');
  await page.evaluate(()=>{ window.__qalabProbeElements=new Map([['first',document.querySelectorAll('button')[0]],['second',document.querySelectorAll('button')[1]]]); });
  const candidate={by:'xpath',value:'//button',primary:true};
  const registry={__selection:{frame:'shell',candidates:[candidate]}};
  const rt=createAutomationRuntime({page,registry});
  const options=await inspectSelectionMatches(rt,{});
  assert.equal(options.count,2); assert.equal(options.matches.length,2);
  assert.deepEqual(options.matches.map(m=>m.element_ref),['first','second']);
  assert.equal(options.matches[1].candidate.nth,1);
  assert.ok(options.matches[1].uniqueXPath); assert.ok(options.matches[1].absRect);
  registry.__selection.candidates=[options.matches[1].candidate];
  const selected=await rt.inspect({key:'__selection'});
  assert.equal(selected.count,1);
  assert.equal(await selected.loc.evaluate(el=>el===window.__qalabProbeElements.get('second')),true);
  const capped=await inspectSelectionMatches(createAutomationRuntime({page,registry:{__selection:{frame:'shell',candidates:[candidate]}}}),{},1);
  assert.equal(capped.matches.length,1);assert.equal(capped.truncated,true);
  console.log('PASS: testid/XPath/CSS + text, explicit primary, duplicate match options, selected occurrence identity and bounded results');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
