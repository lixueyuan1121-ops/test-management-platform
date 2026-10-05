// Run existing UI contracts in either appearance without changing their fixtures.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const choice = process.env.UI_THEME_TEST;
if (choice === 'fresh' || choice === 'tech') {
  const launch = chromium.launch.bind(chromium);
  chromium.launch = async (...args) => {
    const browser = await launch(...args);
    const newPage = browser.newPage.bind(browser);
    browser.newPage = async (...options) => {
      const page = await newPage(...options);
      await page.addInitScript(value => localStorage.setItem('tp_ui_theme', value), choice);
      return page;
    };
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async (...options) => {
      const context = await newContext(...options);
      await context.addInitScript(value => localStorage.setItem('tp_ui_theme', value), choice);
      return context;
    };
    return browser;
  };
}
