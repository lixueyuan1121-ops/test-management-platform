// 输入 query 后校验其真的进了输入框:type() 执行一次输入,readText() 读回当前文本;
// 为空且 query 非空 → 重试(默认 1 次);仍空 → 显式抛错(把「静默不输入」暴露成失败,不发空)。
//
// 为什么需要:桌面客户端里附件 setInputFiles 后 ProseMirror 焦点/selection 常未落稳,
// pressSequentially 静默不进字符。纯控制流,不依赖 Playwright,便于单测;GUI 侧(聚焦/清空/输入)
// 由调用方在 type() 闭包里实现,并保证 type() 幂等(每次先清空再输入,重试不重复堆字)。
async function typeQuestionVerified({ type, readText, question, sleep, warn, retries = 1 }) {
  const want = (question || '').trim();
  for (let attempt = 0; attempt <= retries; attempt++) {
    await type();
    const got = ((await readText()) || '').trim();
    if (got || !want) return got;                 // 进去了,或本就无需输入(空 query)→ 完成
    if (attempt < retries) {
      if (warn) warn(`输入框校验为空,重聚焦重试输入(第 ${attempt + 1} 次)`);
      if (sleep) await sleep(300);
    }
  }
  throw new Error('query 未能输入到对话框(输入后读回校验仍为空,疑似附件上传后输入框失焦)');
}

module.exports = { typeQuestionVerified };
