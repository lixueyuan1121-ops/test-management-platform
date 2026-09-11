// 前置条件「导航到起始位置」相关的纯逻辑(与 runner 主循环解耦,便于单测)。
// 用于两段式执行:有 precondition 的用例先让 claude 把界面导航到起点,再把 script 交 StepExecutor。

export const NAV_SYSTEM_PROMPT = `你是被测客户端的"前置导航器"。任务:只把界面导航到给定的**起始位置**,不要执行任何测试步骤、不要断言、不要做后续功能操作。
- 只用 mcp__gui__* 工具:先 gui_connect;需要时 gui_probe 探当前页找入口;用 gui_click/gui_fill/gui_wait_for 操作到位。
- 起始位置来自 precondition(自由文本),例如"在左侧栏会话记录里选一个含文件产物的会话进入"。找不到精确目标就选最符合描述的(如"合适的会话"取列表里第一个符合条件的)。
- **到位即停**。你的**最后一行**必须是且只能是一个 JSON:{"ok":true,"reason":"已到达:<简述>"};确实无法到达时 {"ok":false,"reason":"<原因>"}。ok 只能 true 或 false,不要 markdown/解释文字。
- 禁止联网、禁止翻代码、禁止用鼠标坐标。`;

// 从 claude 输出里解析 {ok:boolean, reason}:括号配平扫描出所有 JSON 对象子串,从后往前取
// 第一个「能解析且 ok 为布尔」的(容忍前置解释文字、markdown 代码块、reason 内含花括号、信封包裹)。
export function parseNavOk(raw) {
  let text = raw;
  try { const j = JSON.parse(raw); text = j.result ?? j.text ?? raw; } catch { /* 非信封,按裸文本 */ }
  text = String(text);
  const objs = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== "{") continue;
    let depth = 0;
    for (let j = i; j < text.length; j++) {
      const ch = text[j];
      if (ch === "{") depth++;
      else if (ch === "}") { depth--; if (depth === 0) { objs.push(text.slice(i, j + 1)); i = j; break; } }
    }
  }
  for (let k = objs.length - 1; k >= 0; k--) {
    try { const o = JSON.parse(objs[k]); if (typeof o.ok === "boolean") return { ok: o.ok, reason: String(o.reason || "") }; }
    catch { /* 试上一个 */ }
  }
  return null;
}
