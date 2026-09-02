<template>
  <div class="my-devices">
    <el-card>
      <template #header>
        <div class="header">
          <span>我的执行设备</span>
          <el-button type="primary" size="small" :icon="Plus" @click="openRegister">注册设备</el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="intro">
        在自己的电脑上部署 runner,把这里生成的 <b>专属 token</b> 填进 runner 的 <code>.env</code>(RUNNER_TOKEN)与
        <code>RUNNER_ID</code>(填设备的 runner_id)。之后在用例库/任务清单下发时选中该设备,用例就会到你这台机器上执行。
        <br>这台机<b>当前在跑哪类 runner</b>(功能 <code>run.sh</code> / 测评 <code>run-eval.sh</code>)由平台<b>自动感知</b>——
        跑哪个就接哪类任务,无需手动配置(两套 runner 抢同一客户端,不能在一台机上同时跑)。
      </el-alert>

      <el-table :data="devices" v-loading="loading" size="small" border stripe empty-text="还没有登记设备,点右上角『注册设备』">
        <el-table-column prop="runner_id" label="runner_id" min-width="130" />
        <el-table-column prop="name" label="设备名" min-width="120" />
        <el-table-column label="平台" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="PLATFORM_TYPE[row.platform || 'web']" size="small" effect="plain">{{ PLATFORM_LABEL[row.platform || 'web'] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="当前 runner" width="130" align="center">
          <template #default="{ row }">
            <template v-for="c in (row.active_kinds || [])" :key="c">
              <el-tag :type="CAP_TYPE[c]" size="small" effect="light" style="margin: 0 2px">{{ CAP_LABEL[c] || c }}</el-tag>
            </template>
            <span v-if="!(row.active_kinds || []).length" class="mono idle">未启动</span>
          </template>
        </el-table-column>
        <el-table-column label="token" min-width="130">
          <template #default="{ row }"><span class="mono">{{ row.token }}</span></template>
        </el-table-column>
        <el-table-column label="最近活跃" width="150">
          <template #default="{ row }">{{ fmtTime(row.last_seen_at) || '从未' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="210" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="onReset(row)">重置 token</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 注册/编辑对话框(共用;dialog.id 为空=注册,非空=编辑) -->
    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑执行设备' : '注册执行设备'" width="460px">
      <el-form label-width="90px">
        <el-form-item label="runner_id" required>
          <el-input v-model="dialog.runner_id" :disabled="!!dialog.id"
                    placeholder="如 alice-mac(须与 runner .env 的 RUNNER_ID 一致)" />
          <div v-if="dialog.id" class="form-hint">runner_id 是稳定标识,不可修改</div>
        </el-form-item>
        <el-form-item label="设备名" required>
          <el-input v-model="dialog.name" placeholder="如 我的 MacBook" />
        </el-form-item>
        <el-form-item label="平台" required>
          <el-radio-group v-model="dialog.platform">
            <el-radio-button value="web">PC/Web</el-radio-button>
            <el-radio-button value="android">Android</el-radio-button>
            <el-radio-button value="ios">iOS</el-radio-button>
          </el-radio-group>
          <div class="form-hint">决定该设备能执行哪类用例（web=PC端 GUI/E2E）</div>
        </el-form-item>
        <el-form-item>
          <div class="form-hint">提示:这台机接功能还是测评任务,由它实际启动的 runner(<code>run.sh</code>/<code>run-eval.sh</code>)自动决定,无需在此配置。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="doSave">{{ dialog.id ? '保存' : '生成 token' }}</el-button>
      </template>
    </el-dialog>

    <!-- token 明文一次性展示 -->
    <el-dialog v-model="tokenDlg.visible" title="设备 token(仅显示这一次,请复制保存)" width="520px">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:10px">
        token 只在此刻明文显示一次。填入该设备 runner 的 <code>.env</code>:<code>RUNNER_TOKEN={{ tokenDlg.token }}</code>
      </el-alert>
      <div class="token-box">
        <span class="mono token-text">{{ tokenDlg.token }}</span>
        <el-button size="small" :icon="CopyDocument" @click="copyToken">复制</el-button>
      </div>
      <template #footer><el-button type="primary" @click="tokenDlg.visible = false">我已保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, CopyDocument } from '@element-plus/icons-vue'
import { listMyDevices, registerDevice, updateDevice, resetDeviceToken, deleteDevice } from '@/api'

const PLATFORM_LABEL = { web: 'PC/Web', android: 'Android', ios: 'iOS' }
const PLATFORM_TYPE = { web: '', android: 'success', ios: 'warning' }
// 当前 runner 类型标识:func=功能测试 / eval=对话测评(运行时感知,后端 active_kinds 返回)
const CAP_LABEL = { func: '功能测试', eval: '对话测评' }
const CAP_TYPE = { func: 'primary', eval: 'warning' }

const devices = ref([])
const loading = ref(false)
// dialog 兼注册/编辑:id 为空=注册,非空=编辑该设备。能力不再配置(运行时感知)。
const dialog = reactive({ visible: false, id: null, runner_id: '', name: '', platform: 'web', saving: false })
const tokenDlg = reactive({ visible: false, token: '' })

async function load() {
  loading.value = true
  try { devices.value = await listMyDevices() } finally { loading.value = false }
}
onMounted(load)

function openRegister() {
  Object.assign(dialog, { id: null, runner_id: '', name: '', platform: 'web' })
  dialog.visible = true
}

function openEdit(row) {
  Object.assign(dialog, {
    id: row.id,
    runner_id: row.runner_id,
    name: row.name,
    platform: row.platform || 'web',
  })
  dialog.visible = true
}

async function doSave() {
  if (!dialog.runner_id.trim() || !dialog.name.trim()) { ElMessage.warning('请填写 runner_id 与设备名'); return }
  dialog.saving = true
  try {
    if (dialog.id) {
      await updateDevice(dialog.id, { name: dialog.name.trim(), platform: dialog.platform })
      dialog.visible = false
      ElMessage.success('已保存')
    } else {
      const d = await registerDevice(dialog.runner_id.trim(), dialog.name.trim(), dialog.platform)
      dialog.visible = false
      showToken(d.token)
    }
    await load()
  } catch { /* http 拦截器已提示 */ }
  finally { dialog.saving = false }
}

async function onReset(row) {
  try {
    await ElMessageBox.confirm(`重置「${row.name}」的 token?旧 token 立即失效,该设备的 runner 需换用新 token。`, '重置 token', { type: 'warning' })
  } catch { return }
  try {
    const d = await resetDeviceToken(row.id)
    showToken(d.token)
    await load()
  } catch { /* 已提示 */ }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`删除设备「${row.name}」?其 token 立即失效,无法再用它下发/执行。`, '删除设备', { type: 'warning' })
  } catch { return }
  try { await deleteDevice(row.id); ElMessage.success('已删除'); await load() } catch { /* 已提示 */ }
}

function showToken(token) {
  tokenDlg.token = token
  tokenDlg.visible = true
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(tokenDlg.token)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败,请手动选择复制')
  }
}

function fmtTime(s) {
  if (!s) return ''
  return String(s).replace('T', ' ').slice(0, 16)
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.intro { margin-bottom: 12px; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: #5a6b7b; }
.token-box { display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: #f5f7fa; border-radius: 6px; }
.token-text { word-break: break-all; flex: 1; }
.form-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.idle { color: #a8b0bb; }
</style>
