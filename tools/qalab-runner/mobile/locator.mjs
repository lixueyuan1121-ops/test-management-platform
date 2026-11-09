import {createLocatorAI} from './locator-ai.mjs';

const xpath = value => ({using:'xpath', value});
const ax = value => ({using:'accessibility id', value});
const iosPredicate = value => ({using:'-ios predicate string', value});
function literal(text) {
  if (!text.includes("'")) return "'" + text + "'";
  if (!text.includes('"')) return '"' + text + '"';
  return 'concat(' + text.split("'").map(s => "'" + s + "'").join(', "\'", ') + ')';
}

// Keep reads scoped to the message roles and history of the current task.
// The home composer creates a fresh task when its first message is submitted.
export function dialogLocator(platform, appId, key, {marker = '', title = ''} = {}) {
  const id = value => ({using:'id', value:appId + ':id/' + value});
  const markerText = literal(marker);
  const titleText = title && !['纳米Work','新对话','新建任务'].includes(title) ? literal(title) : null;
  if (platform === 'android') {
    const locators = {
      home:id('phone_task_filter'), tasks:id('navigation_claw'), input:id('content_et'), send:id('send_container'),
      back:ax('返回'), newChat:ax('新对话'),
      stop:xpath("//*[contains(@resource-id, 'stop_container') or contains(@resource-id, 'stop_iv') or @content-desc='停止' or @content-desc='停止生成']"),
      stopPrompt:xpath("//*[@text='确认终止任务' or @text='确认终止当前任务？' or @text='确认终止当前任务?' or @text='确定终止当前任务？']"),
      confirmStop:xpath("//*[@text='终止' or @text='确认终止']"), cancelStop:xpath("//*[@text='取消']"),
      thread:xpath("//android.view.View[@scrollable='true'][.//*[@text='快速回答' or starts-with(@resource-id,'chat_user_message_')]]"),
      finished:ax('复制'),
      question:xpath("//*[starts-with(@resource-id,'chat_user_message_')]//android.widget.TextView"),
      answer:xpath("//android.widget.TextView[@text='快速回答' or @text='最终回答']/following-sibling::*[1]/descendant-or-self::android.widget.TextView[@text!='']"),
    };
    if (key === 'historyItem' && marker) {
      const matches = "contains(@text," + markerText + ")" + (titleText ? ' or @text=' + titleText : '');
      return xpath("//*[@resource-id=" + literal(appId + ':id/cv_item_container') + "][.//*[" + matches + "]]");
    }
    return locators[key];
  }
  const locators = {
    home:ax('nm.home.taskFilter.button'), tasks:ax('nm.shell.tab.tasks'), back:ax('nm.chat.navigationBar.back'),
    newChat:ax('nm.chat.navigationBar.newChat'),
    input:iosPredicate("type == 'XCUIElementTypeTextView' AND visible == 1"),
    send:iosPredicate("name IN {'nm.uitest.legacy.inputbar.send','new inputbar send','nm.work.composer.send'} AND visible == 1"),
    stop:iosPredicate("name IN {'new inputbar stop','nm.uitest.legacy.inputbar.stop','nm.work.composer.stop'} AND visible == 1"),
    stopPrompt:iosPredicate("type == 'XCUIElementTypeStaticText' AND label IN {'确认终止当前任务？','确认终止当前任务?'} AND visible == 1"),
    confirmStop:iosPredicate("type == 'XCUIElementTypeButton' AND label == '终止' AND visible == 1"),
    cancelStop:iosPredicate("type == 'XCUIElementTypeButton' AND label == '取消' AND visible == 1"),
    question:iosPredicate("name == 'nm.chat.userBubble' AND label != ''"),
    thread:ax('nm.ipad.chat.collectionView'), answer:ax('nm.chat.finalAnswer'), finished:ax('nm.chat.action.more'),
  };
  if (key === 'historyItem' && marker) {
    const match = "contains(@label," + markerText + ")" + (titleText ? ' or @label=' + titleText : '');
    return xpath("//XCUIElementTypeCell[.//*[" + match + "]]");
  }
  return locators[key];
}

export function createLocatorResolver(driver, {appId, build, cacheDir} = {}) {
  const verified = new Map();
  const ai = build ? createLocatorAI({platform:driver.platform, appId, build, cacheDir}) : null;
  const resolve = async (key, params) => verified.get(key) || dialogLocator(driver.platform, appId, key, params);
  resolve.recover = async key => {
    if (!ai || !['input','send','back','tasks','stop'].includes(key)) return;
    const cached = await ai.read(key);
    if (cached && (await driver.findAll(cached)).length === 1) {
      verified.set(key, cached);
      return;
    }
    const xml = await driver.source();
    // Only structural control identifiers are eligible. Never send labels, text or the tree to a model.
    const candidates = new Map();
    for (const node of xml.matchAll(/<[^!?/][^>]*>/g)) {
      const value = driver.platform === 'ios' ? /\bname="(nm\.[a-zA-Z0-9_.-]+)"/.exec(node[0])?.[1]
        : /\bresource-id="([^"]+)"/.exec(node[0])?.[1];
      if (!value || (driver.platform === 'android' && !value.startsWith(appId + ':id/'))) continue;
      candidates.set(value, {using:driver.platform === 'android' ? 'id' : 'accessibility id', value});
    }
    const selected = await ai.choose(key, [...candidates.values()]);
    if (selected && (await driver.findAll(selected)).length === 1) verified.set(key, selected);
  };
  resolve.verified = async () => {
    for (const [key, locator] of verified) await ai.commit(key, locator);
  };
  return resolve;
}
