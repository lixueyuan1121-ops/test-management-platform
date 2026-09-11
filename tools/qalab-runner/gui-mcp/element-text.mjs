// 从一个 DOM 元素取"用户可见/可断言文本"。
//
// 关键:表单控件(input/textarea/select)的当前值在 `.value`,**不在 textContent**。
// 早期 assertText/getText 一律用 textContent → 对输入框恒读到空串,导致断言"输入框含 X"
// 必然假失败(实测 tc15/tc14:期望步进器含"23"/"6",实际读到「」,被误记为 business 功能 bug)。
// 本函数按标签分流:表单控件取 .value,其余取 textContent。抽成纯函数以便单测(见 element-text.test.mjs),
// 运行时经 loc.evaluate 在浏览器上下文里对真实元素调用同一逻辑。
//
// el:DOM 元素(浏览器上下文)或形如 { tagName, value, textContent } 的对象(测试)。
// 注意:本函数会被 Playwright 的 loc.evaluate 序列化后送进浏览器上下文执行,
// 故**不能引用任何外部作用域变量**(常量集合必须内联),否则 evaluate 时 ReferenceError。
// 与执行器/导出脚本使用同一个实现。
export { elementTextValue } from "./runtime-loader.mjs";
