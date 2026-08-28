import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/dist/index.css'   // 全量样式:配合按需导入(vite.config),保证所有 el-* 样式都在
import '@/styles/anim.css'
import '@/styles/theme.css'   // 亮色科技风：须在 element-plus 样式之后，才能覆盖其 CSS 变量

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
// 注:不再 app.use(ElementPlus) 全量注册;组件/指令由 unplugin-vue-components 按需自动导入。
// locale(中文)通过 App.vue 根部的 <el-config-provider :locale="zhCn"> 提供。

// 全局渲染错误兜底(对齐 React ErrorBoundary 实践):单个模板表达式抛错会让整个组件树
// 渲染失败——表现为"整页空白无声失败"(2026-08 测评结果页白屏事故根因)。兜底后错误
// 弹提示 + 控制台留完整堆栈,用户能报出具体错误而不是只说"页面空了"。10s 节流防刷屏。
let _lastErrToast = 0
app.config.errorHandler = (err, _instance, info) => {
  console.error('[全局错误]', info, err)
  const now = Date.now()
  if (now - _lastErrToast > 10000) {
    _lastErrToast = now
    import('element-plus').then(({ ElMessage }) =>
      ElMessage.error(`页面异常(${info}): ${err?.message || err}`.slice(0, 120)))
  }
}
app.mount('#app')

// 动态注入输入框背景修复——必须在 Element Plus 组件样式之后
const fixStyle = document.createElement('style')
fixStyle.textContent = `
  .el-input__wrapper,
  .el-input__wrapper:hover,
  .el-input__wrapper:focus-within,
  .el-input__wrapper.is-focus,
  .el-textarea__inner,
  .el-textarea__inner:hover,
  .el-textarea__inner:focus,
  .el-select .el-input .el-input__wrapper,
  .el-select .el-input .el-input__wrapper:hover,
  .el-date-editor .el-input__wrapper,
  .el-date-editor .el-input__wrapper:hover,
  .el-input-number .el-input .el-input__wrapper,
  .el-input-number .el-input .el-input__wrapper:hover {
    background-color: #fff !important;
  }
`
document.head.appendChild(fixStyle)
