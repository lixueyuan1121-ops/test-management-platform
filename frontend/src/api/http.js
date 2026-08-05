import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import router from '@/router'

// dev 走 vite 代理 /api；生产由后端(uvicorn)同源托管前端页面与 /api，
// 所以生产也用相对路径 /api，无需拼 host/端口，天然无 CORS 问题。
const baseURL = '/api'

const http = axios.create({
  baseURL,
  timeout: 15000,
})

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

http.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      ElMessage.error(body.msg || '请求失败')
      return Promise.reject(new Error(body.msg || '请求失败'))
    }
    return body
  },
  (error) => {
    const status = error.response?.status
    const msg = error.response?.data?.msg || error.message
    if (status === 401) {
      const auth = useAuthStore()
      auth.logout()
      ElMessage.error('登录已失效，请重新登录')
      router.push('/login')
    } else {
      ElMessage.error(msg || '网络错误')
    }
    return Promise.reject(error)
  }
)

export default http
