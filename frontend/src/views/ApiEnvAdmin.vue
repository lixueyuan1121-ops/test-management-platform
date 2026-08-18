<template>
  <div class="api-env-admin">
    <el-card>
      <template #header>
        <div class="header">
          <span>api 测试环境</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
          </div>
        </div>
      </template>

      <el-alert
        v-if="pid && !isAdmin" type="warning" :closable="false" show-icon
        title="仅项目管理员可查看/编辑 api 环境（含被测系统凭据）" style="margin-bottom:12px"
      />

      <el-empty v-if="!pid" description="请先选择项目" :image-size="70" />

      <el-form v-else-if="isAdmin" v-loading="loading" label-width="96px" class="env-form">
        <el-form-item label="base_url">
          <el-input v-model="form.base_url" placeholder="被测系统地址，如 https://biz.example.com（执行器拼接 path）" clearable />
          <div class="form-hint">api 用例执行时以此为前缀拼接每步的相对 path。</div>
        </el-form-item>

        <el-form-item label="鉴权方式">
          <el-radio-group v-model="form.auth_type">
            <el-radio value="fixed">固定 token/header</el-radio>
            <el-radio value="login">用例内登录取 token</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item :label="form.auth_type === 'fixed' ? '固定鉴权' : '登录信息'">
          <el-input
            v-model="form.authText" type="textarea" :rows="5"
            :placeholder="authPlaceholder" spellcheck="false" class="mono"
          />
          <div class="form-hint">
            <template v-if="form.auth_type === 'fixed'">
              JSON 对象，执行器把 <code>auth.headers</code> 预置进每个请求。示例：<code>{"headers":{"Authorization":"Bearer xxx"}}</code>
            </template>
            <template v-else>
              登录模式下 token 由用例内「登录步骤」的 <code>extract</code> 取得，此处可留 <code>{}</code> 或存登录接口备注。
            </template>
          </div>
        </el-form-item>

        <el-form-item label="接口契约">
          <div class="contract-bar">
            <el-button size="small" @click="curlDlg.visible = true"><el-icon><Position /></el-icon>粘贴 curl 解析</el-button>
            <el-button size="small" @click="oapiDlg.visible = true"><el-icon><Upload /></el-icon>导入 OpenAPI/Swagger</el-button>
            <span class="form-hint">契约越全，AI 生成的 api 用例越能"打对接口"；无契约时 api 用例会降级人工。</span>
          </div>
          <el-input
            v-model="form.contract" type="textarea" :rows="12"
            placeholder="每行一个接口，如：&#10;GET /api/users 用户列表(page,size)&#10;POST /api/users 创建用户{name,email}&#10;DELETE /api/users/{id} 删除"
            spellcheck="false" class="mono"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          <el-button @click="reload">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 粘贴 curl 解析 -->
    <el-dialog v-model="curlDlg.visible" title="粘贴 curl 解析" width="720px" @closed="onCurlClosed">
      <el-input
        v-model="curlDlg.text" type="textarea" :rows="6" spellcheck="false" class="mono"
        placeholder="粘贴一条 curl（支持浏览器「复制为 cURL」）。鉴权头会被自动剥离，不会入库。"
      />
      <div class="dlg-actions">
        <el-button type="primary" size="small" :loading="curlDlg.parsing" :disabled="!curlDlg.text.trim()" @click="doParseCurl">解析</el-button>
      </div>

      <template v-if="curlDlg.result">
        <el-descriptions :column="1" border size="small" class="parsed">
          <el-descriptions-item label="method / path">
            <el-tag size="small">{{ curlDlg.result.parsed.method }}</el-tag> {{ curlDlg.result.parsed.path }}
          </el-descriptions-item>
          <el-descriptions-item label="base_url">{{ curlDlg.result.parsed.base_url || '（无，用项目 base_url）' }}</el-descriptions-item>
          <el-descriptions-item v-if="hasKeys(curlDlg.result.parsed.query)" label="query">{{ JSON.stringify(curlDlg.result.parsed.query) }}</el-descriptions-item>
          <el-descriptions-item v-if="hasKeys(curlDlg.result.parsed.headers)" label="headers">{{ JSON.stringify(curlDlg.result.parsed.headers) }}</el-descriptions-item>
          <el-descriptions-item v-if="curlDlg.result.parsed.stripped_auth?.length" label="已剥离鉴权">
            <el-tag v-for="a in curlDlg.result.parsed.stripped_auth" :key="a" type="warning" size="small" effect="plain" style="margin-right:4px">{{ a }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="curlDlg.result.parsed.warnings?.length" label="告警">
            <div v-for="w in curlDlg.result.parsed.warnings" :key="w" class="warn">{{ w }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="契约行">{{ curlDlg.result.contract_line }}</el-descriptions-item>
        </el-descriptions>

        <div class="seed-block">
          <div class="form-hint">单步 script 种子（可复制到某 api 用例，AI 再补断言/边界/清理）：</div>
          <el-input :model-value="seedText" type="textarea" :rows="6" readonly spellcheck="false" class="mono" />
        </div>
      </template>

      <template #footer>
        <el-button @click="curlDlg.visible = false">关闭</el-button>
        <el-button :disabled="!curlDlg.result" @click="copySeed">复制 script 种子</el-button>
        <el-button type="primary" :disabled="!curlDlg.result?.contract_line" @click="applyCurlToContract">并入契约</el-button>
      </template>
    </el-dialog>

    <!-- 导入 OpenAPI/Swagger -->
    <el-dialog v-model="oapiDlg.visible" title="导入 OpenAPI/Swagger" width="720px" @closed="onOapiClosed">
      <el-input
        v-model="oapiDlg.text" type="textarea" :rows="7" spellcheck="false" class="mono"
        placeholder="粘贴 openapi.json / swagger.json 的内容（出于安全不在服务端拉取 URL，请自行获取后粘贴）。"
      />
      <div class="dlg-actions">
        <el-button type="primary" size="small" :loading="oapiDlg.importing" :disabled="!oapiDlg.text.trim()" @click="doImportOpenapi">解析</el-button>
      </div>
      <template v-if="oapiDlg.result">
        <div class="form-hint">解析出 <b>{{ oapiDlg.result.count }}</b> 个接口{{ oapiDlg.result.base_url ? `，base_url：${oapiDlg.result.base_url}` : '' }}：</div>
        <el-input :model-value="oapiDlg.result.contract" type="textarea" :rows="10" readonly spellcheck="false" class="mono" />
      </template>
      <template #footer>
        <el-button @click="oapiDlg.visible = false">关闭</el-button>
        <el-button :disabled="!oapiDlg.result" @click="applyOpenapi('append')">追加到契约</el-button>
        <el-button type="primary" :disabled="!oapiDlg.result" @click="applyOpenapi('replace')">替换契约</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Position, Upload } from '@element-plus/icons-vue'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { readApiEnv, upsertApiEnv, parseCurl, importOpenapi } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const auth = useAuthStore()
const app = useAppStore()

const projects = ref([])
const pid = ref(null)
const loading = ref(false)
const saving = ref(false)

const DEFAULT_AUTH = '{\n  "headers": {\n    "Authorization": "Bearer <token>"\n  }\n}'
const form = reactive({ base_url: '', auth_type: 'fixed', authText: DEFAULT_AUTH, contract: '' })

// 项目 admin 才可读写（GET/PUT 后端强制 admin；平台管理员在任何项目视为 admin）。
const isAdmin = computed(() => !!pid.value && (auth.isPlatformAdmin || auth.roleIn(pid.value) === 'admin'))

const authPlaceholder = computed(() =>
  form.auth_type === 'fixed' ? '{"headers":{"Authorization":"Bearer xxx"}}' : '{}')

const hasKeys = (o) => o && typeof o === 'object' && Object.keys(o).length > 0

onMounted(async () => {
  try { projects.value = await app.fetchProjects() } catch { projects.value = [] }
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await reload()
  }
})

async function onProjectChange() {
  if (pid.value) setLastProjectId(pid.value)
  await reload()
}

function resetForm() {
  form.base_url = ''
  form.auth_type = 'fixed'
  form.authText = DEFAULT_AUTH
  form.contract = ''
}

async function reload() {
  if (!pid.value || !isAdmin.value) { resetForm(); return }
  loading.value = true
  try {
    const env = await readApiEnv(pid.value)
    if (env) {
      form.base_url = env.base_url || ''
      form.auth_type = env.auth_type || 'fixed'
      form.authText = JSON.stringify(env.auth || {}, null, 2)
      form.contract = env.contract || ''
    } else {
      resetForm()
    }
  } catch { /* http 拦截器已提示 */ }
  finally { loading.value = false }
}

async function save() {
  let authObj
  try { authObj = JSON.parse(form.authText || '{}') } catch { ElMessage.error('鉴权 JSON 不合法'); return }
  if (authObj === null || typeof authObj !== 'object' || Array.isArray(authObj)) { ElMessage.error('鉴权须为 JSON 对象'); return }
  saving.value = true
  try {
    await upsertApiEnv({
      project_id: pid.value,
      base_url: form.base_url.trim(),
      auth_type: form.auth_type,
      auth: authObj,
      contract: form.contract,
    })
    ElMessage.success('已保存')
  } catch { /* 已提示 */ }
  finally { saving.value = false }
}

// ---- curl 解析 ----
const curlDlg = reactive({ visible: false, text: '', result: null, parsing: false })
const seedText = computed(() => (curlDlg.result ? JSON.stringify(curlDlg.result.script_seed, null, 2) : ''))

async function doParseCurl() {
  if (!curlDlg.text.trim()) return
  curlDlg.parsing = true
  try { curlDlg.result = await parseCurl(curlDlg.text) } catch { curlDlg.result = null } finally { curlDlg.parsing = false }
}

function applyCurlToContract() {
  const line = curlDlg.result?.contract_line
  if (!line) return
  form.contract = form.contract.trim() ? `${form.contract.trimEnd()}\n${line}` : line
  if (!form.base_url && curlDlg.result.parsed?.base_url) form.base_url = curlDlg.result.parsed.base_url
  ElMessage.success('已并入契约（记得点「保存」落库）')
  curlDlg.visible = false
}

async function copySeed() {
  try { await navigator.clipboard.writeText(seedText.value); ElMessage.success('script 种子已复制') }
  catch { ElMessage.warning('复制失败，请手动选中复制') }
}

function onCurlClosed() { curlDlg.text = ''; curlDlg.result = null }

// ---- OpenAPI 导入 ----
const oapiDlg = reactive({ visible: false, text: '', result: null, importing: false })

async function doImportOpenapi() {
  if (!oapiDlg.text.trim() || !pid.value) return
  oapiDlg.importing = true
  try { oapiDlg.result = await importOpenapi(pid.value, oapiDlg.text) } catch { oapiDlg.result = null } finally { oapiDlg.importing = false }
}

function applyOpenapi(mode) {
  const c = oapiDlg.result?.contract
  if (!c) return
  form.contract = mode === 'replace'
    ? c
    : (form.contract.trim() ? `${form.contract.trimEnd()}\n${c}` : c)
  if (!form.base_url && oapiDlg.result.base_url) form.base_url = oapiDlg.result.base_url
  ElMessage.success('已并入契约（记得点「保存」落库）')
  oapiDlg.visible = false
}

function onOapiClosed() { oapiDlg.text = ''; oapiDlg.result = null }
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.env-form { max-width: 900px; }
.form-hint { color: #90a4ae; font-size: 12px; line-height: 1.6; }
.contract-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.mono :deep(textarea), .mono :deep(input) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.dlg-actions { margin: 10px 0; }
.parsed { margin-top: 6px; }
.seed-block { margin-top: 12px; }
.warn { color: #e6a23c; font-size: 12px; line-height: 1.6; }
code { background: #f0f2f5; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
</style>
