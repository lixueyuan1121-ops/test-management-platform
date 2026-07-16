import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import '@/styles/anim.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
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
