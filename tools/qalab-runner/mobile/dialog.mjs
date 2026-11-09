import {setTimeout as sleep} from 'node:timers/promises';

import {descriptions, dialogScenarios, templateSteps, validateSteps} from './dialog-script.mjs';
export {dialogScenarios};
class AssertionFailure extends Error {}

export async function runDialog({driver, scenario, script, marker, artifact, resolveLocator, timeoutMs = 90000, pollMs = 350}) {
  if (!/^QA-[\w-]{1,60}$/.test(marker)) {
    throw new Error('未知的真机对话场景或测试标记');
  }
  const steps = [];
  const lookup = async (key, params = {}) => {
    const locator = await resolveLocator(key, params);
    if (!locator) return [];
    return driver.findAll(locator);
  };
  const wait = async (observe, description, {business = false, limit = timeoutMs} = {}) => {
    const deadline = Date.now() + limit;
    do {
      try {
        const value = await observe();
        if (value) return value;
      } catch (error) {
        // Compose and UIKit can replace a cell between lookup and reading. Re-query
        // only observations; input, send, stop and navigation are never replayed.
        if (!/stale element reference|no such element/i.test(error.message)) throw error;
      }
      await sleep(pollMs);
    } while (Date.now() < deadline);
    throw new (business ? AssertionFailure : Error)(description);
  };
  const single = async (key, params) => {
    const list = await lookup(key, params);
    if (list.length > 1) throw new Error('控件不唯一：' + key);
    return list[0];
  };
  const requireOne = async (key, params) => {
    let triedRecovery = false;
    const start = Date.now();
    return wait(async () => {
      const found = await single(key, params);
      if (!found && !triedRecovery && Date.now() - start >= 2000) {
        triedRecovery = true;
        await resolveLocator.recover?.(key);
      }
      return found;
    }, '未定位到控件：' + key);
  };
  const read = async id => {
    const value = await driver.text(id);
    return String(value ?? '');
  };
  const check = (target, actual, expected, mode = 'equals') => ({target, actual, expected, mode});
  const step = async (action, desc, fn) => {
    const row = {no: steps.length + 1, action, desc, ok: false};
    steps.push(row);
    try {
      const observed = await fn();
      if (observed) {
        row.check = observed;
        row.shot = await artifact(row.no);
      }
      row.ok = true;
    } catch (error) {
      row.error = error.message;
      if (error.check) row.check = error.check;
      throw error;
    }
  };
  const messages = async (key, params) => {
    const ids = await lookup(key, params);
    return Promise.all(ids.map(read));
  };
  async function goHome() {
    if (await single('home')) return;
    if (await single('stopPrompt')) {
      const cancel = await requireOne('cancelStop');
      await driver.click(cancel);
    }
    const back = await single('back');
    if (back) await driver.click(back);
    if (!(await single('home'))) {
      const tasks = await single('tasks');
      if (tasks) await driver.click(tasks);
    }
    await requireOne('home');
  }
  let ownedDraft = null;
  async function draftValue(editor, expected) {
    const value = driver.platform === 'ios'
      ? String(await driver.attribute(editor, 'value') || '') : await read(editor);
    const hints = ['发消息','输入消息','发送消息','输入你想问的内容','给纳米Work发消息','安排任务，纳米Work帮你完成'];
    return value === expected || !hints.includes(value) ? value : '';
  }
  async function compose(text) {
    const editor = await requireOne('input');
    await driver.click(editor);
    if (await draftValue(editor, '')) throw new Error('输入区已有草稿，保留现场');
    ownedDraft = text;
    await driver.type(editor, text);
  }
  async function clearDraft() {
    const editor = await requireOne('input');
    const actual = await draftValue(editor, ownedDraft);
    if (actual && actual !== ownedDraft) throw new Error('已有草稿与本次输入不一致，保留现场');
    if (actual) await driver.clear(editor);
    ownedDraft = null;
  }
  async function userBubble(prompt) {
    const seen = await messages('question', {prompt});
    return seen.find(value => value === prompt);
  }
  async function answerSnapshot() {
    return (await messages('answer')).filter(Boolean);
  }
  async function completed(previous) {
    let last = '', repeats = 0;
    return wait(async () => {
      // During streaming an accessibility tree can change faster than it can be read.
      // Poll a native completion control before requesting the full answer hierarchy.
      if (!(await lookup('finished')).length) return;
      const entries = await answerSnapshot();
      const answer = entries.at(-1);
      if (!answer || (entries.length === previous.length && answer === previous.at(-1))) return;
      if ((await lookup('stop')).length) return;
      const input = await single('input');
      if (!input || !(await driver.enabled(input))) return;
      repeats = answer === last ? repeats + 1 : 1;
      last = answer;
      return repeats >= 2 ? answer : null;
    }, '本轮未取得新的完整回复和可用输入框', {business: true});
  }
  const turns = [];
  let previous = [], lastAnswer = '', restored = false;
  function assertValue(target, actual, expected, mode='equals') {
    const observation = check(target,actual,expected,mode);
    const pass = mode === 'contains' ? actual.includes(expected) : actual === expected;
    if (!pass) {
      const error = new AssertionFailure(target + '与预期不符');
      error.check = observation;
      throw error;
    }
    return observation;
  }
  async function priorMessage(prompt) {
    let found = await userBubble(prompt);
    for (let n = 0; !found && n < 4; n++) {
      const thread = await single('thread');
      if (!thread) break;
      if (driver.platform === 'ios') await driver.mobile('swipe',{direction:'down',elementId:thread});
      else await driver.mobile('scrollGesture',{direction:'up',percent:0.8,elementId:thread});
      await sleep(pollMs);
      found = await userBubble(prompt);
    }
    if (!found) throw new Error('滚动会话仍未取得指定轮次的消息证据');
    return found;
  }
  async function stopGeneration() {
    const button = await requireOne('stop');
    if (!await userBubble(turns.at(-1))) throw new Error('停止操作无法关联本轮测试会话');
    await driver.click(button);
    let confirmed = false, readySamples = 0;
    await wait(async () => {
      if (await single('stopPrompt')) {
        readySamples = 0;
        if (!confirmed) {
          await driver.click(await requireOne('confirmStop'));
          confirmed = true;
        }
        return false;
      }
      if ((await lookup('stop')).length) { readySamples = 0; return false; }
      const input = await single('input');
      readySamples = input && await driver.enabled(input) ? readySamples + 1 : 0;
      return readySamples >= 2;
    },'停止后仍在生成或输入区不可用',{business:true});
    return check('停止后输入区可用',true,true);
  }
  async function perform({action,text,turn}) {
    switch (action) {
      case 'new_chat': {
        await goHome();
        const editor = await requireOne('input');
        if (await draftValue(editor, '')) throw new Error('输入区已有草稿，保留现场');
        return;
      }
      case 'compose': return compose(text);
      case 'clear_draft': return clearDraft();
      case 'assert_draft': return assertValue('输入区文本',await draftValue(await requireOne('input'),text),text);
      case 'assert_send_unavailable': {
        await requireOne('input');
        const button = await single('send');
        const canSend = !!button && await driver.displayed(button) && await driver.enabled(button);
        return assertValue('发送按钮可用',canSend,false);
      }
      case 'send': {
        previous = turns.length ? await answerSnapshot() : [];
        const button = await requireOne('send');
        if (!(await driver.enabled(button))) throw new AssertionFailure('发送按钮不可用');
        await driver.click(button);
        turns.push(ownedDraft);
        ownedDraft = null;
        lastAnswer = '';
        return;
      }
      case 'assert_user_message': {
        const prompt = turns[turn-1];
        const actual = turn === turns.length
          ? await wait(() => userBubble(prompt),'本轮消息未出现在用户气泡中',{business:true})
          : await priorMessage(prompt);
        return check('第 '+turn+' 轮用户消息',actual,prompt);
      }
      case 'wait_reply':
        lastAnswer = await completed(previous);
        return check('第 '+turns.length+' 轮完成时的助手回复',lastAnswer,'','neq');
      case 'assert_reply_contains': return assertValue('本轮回复',lastAnswer,text,'contains');
      case 'stop_generation': return stopGeneration();
      case 'open_history':
        await goHome();
        await driver.click(await requireOne('historyItem',{marker}));
        restored = true;
        return;
      case 'assert_history_restored': {
        if (!restored) throw new Error('尚未从历史打开当前会话');
        // Read the latest reply before scrolling back to the original question.
        const current = await wait(async () => (await answerSnapshot()).at(-1),'未找到历史回复',{business:true});
        const question = await priorMessage(turns[0]);
        return assertValue('历史首条消息和末轮回复',[question,current].join('\n'),[turns[0],lastAnswer].join('\n'));
      }
    }
  }
  try {
    const actions = scenario === 'script' ? script : templateSteps(scenario);
    validateSteps(actions);
    for (const raw of actions) {
      const item = raw.text === undefined ? raw : {...raw,text:raw.text.replaceAll('{{marker}}',marker)};
      await step(item.action,descriptions[item.action],() => perform(item));
    }
    await resolveLocator.verified?.();
    return {verdict:'pass',reason:'真机对话步骤及本轮断言均通过',steps};
  } catch (error) {
    return {verdict:error instanceof AssertionFailure ? 'fail' : 'blocked',reason:error.message,steps};
  } finally {
    // Release only an unsent draft that exactly matches the one this case created.
    if (ownedDraft !== null) {
      try { await clearDraft(); } catch { /* Preserve an edited or inaccessible draft. */ }
    }
  }
}
