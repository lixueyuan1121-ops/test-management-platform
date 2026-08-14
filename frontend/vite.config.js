import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [
    vue(),
    // ElementPlus 按需:只打包用到的 el-* 组件/指令的 JS(替代全量 app.use,砍首屏包)。
    // importStyle:false —— 样式仍走 main.js 的全量 CSS(保证所有样式在,杜绝缺样式)。
    Components({ resolvers: [ElementPlusResolver({ importStyle: false })], dts: false }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
