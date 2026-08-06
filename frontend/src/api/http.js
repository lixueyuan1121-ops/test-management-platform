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

// 用裸 axios 调刷新接口，绕过下方响应拦截器，避免 401 递归。
let refreshingPromise = null
function tryRefresh() {
  const auth = useAuthStore()
  if (!auth.refreshToken) return Promise.resolve(false)
  if (refreshingPromise) return refreshingPromise
  refreshingPromise = axios
    .post(`${baseURL}/auth/refresh`, { refresh_token: auth.refreshToken })
    .then((resp) => {
      const body = resp.data
      if (body && body.code === 0 && body.data?.access_token) {
        auth.setToken(body.data.access_token)
        return true
      }
      return false
    })
    .catch(() => false)
    .finally(() => { refreshingPromise = null })
  return refreshingPromise
}

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
  async (error) => {
    const status = error.response?.status
    const original = error.config
    // login 401=凭据错、refresh 401=refresh 已死，这两种不触发自动刷新；其余（如 /auth/me）过期则刷新
    const url = original?.url || ''
    const skipRefresh = url.endsWith('/auth/login') || url.endsWith('/auth/refresh')
    // access token 过期：尝试用 refresh token 换新 token 后重放原请求（仅重试一次）
    if (status === 401 && original && !original._retried && !skipRefresh) {
      const refreshed = await tryRefresh()
      if (refreshed) {
        original._retried = true
        return http(original)
      }
      const auth = useAuthStore()
      auth.logout()
      ElMessage.error('登录已失效，请重新登录')
      router.push('/login')
      return Promise.reject(error)
    }
    if (status === 401) {
      const auth = useAuthStore()
      auth.logout()
      router.push('/login')
    } else {
      const msg = error.response?.data?.msg || error.message
      ElMessage.error(msg || '网络错误')
    }
    return Promise.reject(error)
  }
)

export default http
