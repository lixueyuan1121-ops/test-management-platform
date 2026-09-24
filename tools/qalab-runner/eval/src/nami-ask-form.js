// 纳米 Work 反问卡片：只操作卡片内部，不向主聊天输入框发送补充消息。
const TEXT_FIELDS = 'textarea, input:not([type]), input[type="text"], input[type="search"], [contenteditable="true"]';

function newState(context = {}) {
  return { question: String(context.question || ''), answers: context.clarificationAnswers || {}, pending: null, history: [] };
}

function replyFor(state, key, title) {
  // 精确匹配测试提供的补充信息；不使用预期答案/评分标准生成回复。
  const configured = state.answers[key] ?? state.answers[title];
  if (typeof configured === 'string' && configured.trim()) return { text: configured.trim(), source: 'test_case' };
  return {
    text: '请依据原始需求中已明确的信息继续完成任务；未提供的信息请采用合理的示例假设，并在结果中明确标注为假设或待确认，不要表述为真实数据。保留原需求的目标和约束，不扩大任务范围。' +
      (state.question ? `\n原始需求：${state.question.slice(0, 2000)}` : ''),
    source: 'runner_default_assumption',
  };
}

async function handleAskForm(runner) {
  const state = runner._askForm || (runner._askForm = newState());
  const P = runner.platform;
  const cards = runner._ctx().locator(P.askFormSelector || '.chat-ask-form-card, chat-question-form-card');
  let card;
  for (let i = await cards.count() - 1; i >= 0; i--) {
    if (await cards.nth(i).isVisible()) { card = cards.nth(i); break; }
  }
  const completePending = () => {
    if (state.pending) state.pending.record.status = 'card_closed_or_advanced';
    state.pending = null;
  };
  if (!card) { completePending(); return false; }
  const fail = message => new Error(`[NAMI_ASK_FORM] ${message}`);
  const readIdentity = () => card.evaluate(el => {
    // 无 questionKey 的旧组件用实例标识 + 题目区分；不包含会随填写变化的 value/选中态。
    const prop = '__qalabAskInstance';
    if (!el[prop]) el[prop] = `${Date.now()}-${Math.random()}`;
    const root = el.shadowRoot || el;
    const title = root.querySelector('.ask-form__title')?.textContent?.trim() || '';
    return { request: String(el.questionKey || ''), title,
      key: JSON.stringify([el.questionKey || el[prop], title,
        [...root.querySelectorAll('.ask-form__option-label')].map(e => e.textContent)]) };
  });
  const identity = await readIdentity();
  if (state.pending?.key === identity.key) {
    if (Date.now() - state.pending.submittedAt > (runner.execution.askFormSubmitTimeoutMs ?? 15000)) {
      state.pending.record.status = 'submit_timeout';
      throw fail('反问已点击提交，但卡片未关闭或进入下一题；已阻止重复提交和新建对话');
    }
    return true;
  }
  completePending();
  if (state.history.length >= (runner.execution.askFormMaxSubmissions ?? 30)) throw fail('反问次数达到上限，保留当前对话待检查');

  const record = { question: identity.title, request_key: identity.request, answers: [], selections: [],
    status: 'filling', started_at: new Date().toISOString() };
  state.history.push(record);
  try {
    const optionSel = P.askFormOptionSelector || '.ask-form__option';
    const selected = option => option.evaluate(el =>
      el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-checked') === 'true' ||
      el.classList.contains('is-selected') || el.checked === true);
    const groups = card.locator(P.askFormOptionsGroupSelector || '.ask-form__options');
    const scopes = await groups.count() ? await groups.all() : [card];
    for (const scope of scopes) {
      const options = scope.locator(optionSel);
      let chosen = null;
      for (let i = 0; i < await options.count(); i++) {
        const option = options.nth(i);
        if (!await option.isVisible()) continue;
        if (await selected(option)) { chosen = option; break; }
      }
      if (!chosen) {
        for (let i = 0; i < await options.count(); i++) {
          if (await options.nth(i).isVisible()) { chosen = options.nth(i); break; }
        }
        if (chosen) {
          await chosen.click({ timeout: 2000 });
          const deadline = Date.now() + 2000;
          while (!await selected(chosen) && Date.now() < deadline) await runner.page.waitForTimeout(50);
          if (!await selected(chosen)) throw fail('无法确认反问选项已选中');
        }
      }
      if (chosen) record.selections.push((await chosen.locator('.ask-form__option-label').allTextContents()).join(' ').trim());
    }
    // 等待选中“其它”后 Lit 渲染输入框，支持 textarea / contenteditable 与多个输入。
    await runner.page.waitForTimeout(100);
    const filled = new Map();
    const fillFields = async () => {
      const fields = card.locator(TEXT_FIELDS);
      for (let i = 0; i < await fields.count(); i++) {
        const field = fields.nth(i);
        if (!await field.isVisible() || !await field.isEditable()) continue;
        const info = await field.evaluate(el => ({
          value: 'value' in el ? el.value : el.innerText,
          label: el.getAttribute('aria-label') || el.getAttribute('placeholder') || '',
          maxLength: el.maxLength ?? -1,
          singleLine: el.tagName === 'INPUT',
          // 未选中的其它选项可能仍挂着输入框，不可给它填内容。
          inactive: !!el.closest('.ask-form__option') &&
            !(el.closest('.ask-form__option').getAttribute('aria-selected') === 'true' ||
              el.closest('.ask-form__option').classList.contains('is-selected')),
        }));
        if (info.inactive) continue;
        const reply = info.value?.trim() ? { text: info.value, source: 'existing_input' }
          : replyFor(state, identity.request, identity.title);
        if (info.singleLine) reply.text = reply.text.replace(/[\r\n]+/g, ' ');
        if (info.maxLength >= 0 && reply.text.length > info.maxLength) throw fail(`输入框长度限制为 ${info.maxLength}，无法完整填写补充说明`);
        if (!info.value?.trim()) await field.fill(reply.text, { timeout: 3000 });
        const value = await field.evaluate(el => 'value' in el ? el.value : el.innerText);
        if (value !== reply.text) throw fail('反问输入内容校验失败');
        if (!filled.has(i)) {
          const evidence = { field: info.label, text: value, source: reply.source };
          filled.set(i, evidence); record.answers.push(evidence);
        } else if (filled.get(i).text !== value) throw fail('输入内容在等待提交时发生变化');
      }
    };
    if ((await readIdentity()).key !== identity.key) throw fail('填写期间反问题目发生变化，未提交');
    const submit = card.locator(P.askFormSubmitSelector || '.ask-form__btn--ok');
    if (await submit.count() !== 1) throw fail('未找到唯一的反问提交按钮');
    const deadline = Date.now() + (runner.execution.askFormReadyTimeoutMs ?? 3000);
    // 部分版本选中其它后异步挂载输入，等待时持续检查，而不是盲点禁用按钮。
    do {
      if ((await readIdentity()).key !== identity.key) throw fail('填写期间反问题目发生变化，未提交');
      await fillFields();
      if (await submit.isEnabled()) break;
      await runner.page.waitForTimeout(100);
    } while (Date.now() < deadline);
    if (!await submit.isEnabled()) throw fail('填写后提交按钮仍不可用，请检查必填项或输入校验');
    const valid = await card.locator('input:visible, textarea:visible').evaluateAll(els => els.every(el => !el.checkValidity || el.checkValidity()));
    if (!valid) throw fail('反问表单未通过输入校验');
    if ((await readIdentity()).key !== identity.key) throw fail('提交前反问题目发生变化，未提交');
    state.pending = { key: identity.key, submittedAt: Date.now(), record };
    await submit.click({ timeout: 2000 });
    record.status = 'submitted';
    record.submitted_at = new Date().toISOString();
    runner.logger?.info(`       ↳ [${runner.label}] 专家反问：已填写 ${record.answers.length} 项并提交，等待关闭或下一题`);
    return true;
  } catch (error) {
    record.status = 'failed'; record.error = error.message;
    throw error.message.startsWith('[NAMI_ASK_FORM]') ? error : fail(error.message.split('\n')[0]);
  }
}

module.exports = { TEXT_FIELDS, newState, handleAskForm };
