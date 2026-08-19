<template>
  <div class="perf-collect">
    <div class="head">
      <div class="title">
        采集控制 <span class="sub">{{ info.scenario }} / {{ info.variant }}</span>
        <el-tag size="small" :type="statusType" class="st">{{ statusText }}</el-tag>
      </div>
      <el-button size="small" @click="$router.push('/perf-dispatch')">返回下发</el-button>
    </div>

    <!-- 进行中:显示提示 + 推进/取消 -->
    <el-card v-if="info.status === 'running' || info.status === 'pending'" shadow="never" class="panel">
      <div v-if="info.status === 'pending'" class="waiting">
        <el-icon class="spin"><Loading /></el-icon>
        等待执行机认领…请确认目标执行机的 agent 正在运行(runner={{ info.runner || '?' }})。
      </div>
      <template v-else>
        <div class="step-label">当前操作</div>
        <div class="prompt" :class="{ empty: !info.prompt }">
          {{ info.prompt || '执行机采集中,暂无需操作,请稍候…' }}
        </div>
        <div class="tip">
          按上面提示<b>在被测机操作应用</b>(启动 / 发消息 / 切窗口),做完点【继续】推进；
          perfdog 探测到终点会自动结束并入报告。
        </div>
        <div class="ops">
          <el-button type="primary" size="large" :disabled="!info.prompt || pushing" :loading="pushing" @click="onContinue">
            继续 ▶
          </el-button>
          <el-button type="danger" plain @click="onCancel">取消采集</el-button>
        </div>
      </template>
    </el-card>

    <!-- 结束态 -->
    <el-result v-else-if="info.status === 'completed'" icon="success" title="采集完成" sub-title="数据已存入报告">
      <template #extra>
        <el-button type="primary" @click="$router.push('/perf-report')">去性能报告看结果 →</el-button>
      </template>
    </el-result>
    <el-result v-else-if="info.status === 'canceled'" icon="info" title="采集已取消" :sub-title="info.error || ''">
      <template #extra><el-button @click="$router.push('/perf-dispatch')">返回下发</el-button></template>
    </el-result>
    <el-result v-else-if="info.status === 'failed'" icon="error" title="采集失败" :sub-title="info.error || ''">
      <template #extra><el-button @click="$router.push('/perf-dispatch')">返回下发</el-button></template>
    </el-result>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { getPerfPrompt, signalPerfRun, cancelPerfRun } from '@/api'

const route = useRoute()
const runId = route.params.id
const info = reactive({ status: '', scenario: '', variant: '', prompt: null, runner: '', error: null, signal_seq: 0 })
const pushing = ref(false)
let timer = null

const statusType = computed(() => ({ pending: 'info', running: 'warning', completed: 'success', canceled: 'info', failed: 'danger' }[info.status] || 'info'))
const statusText = computed(() => ({ pending: '等待执行机', running: '采集中', completed: '已完成', canceled: '已取消', failed: '失败' }[info.status] || info.status || '…'))

async function tick() {
  try {
    const d = await getPerfPrompt(runId)
    Object.assign(info, d)
    if (['completed', 'canceled', 'failed'].includes(d.status)) stop()
  } catch { /* 轮询失败下次再试 */ }
}
function stop() { if (timer) { clearInterval(timer); timer = null } }

async function onContinue() {
  pushing.value = true
  try {
    await signalPerfRun(runId)
    info.prompt = null   // 立即置灰,等下一条提示
    await tick()
  } catch (e) {
    ElMessage.error('推进失败,可能采集已结束')
  } finally { pushing.value = false }
}

async function onCancel() {
  await ElMessageBox.confirm('取消本次采集?执行机会中止 perfdog。', '确认', { type: 'warning' })
  await cancelPerfRun(runId)
  ElMessage.success('已请求取消')
  await tick()
}

onMounted(() => { tick(); timer = setInterval(tick, 1500) })
onBeforeUnmount(stop)
</script>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.title .sub { font-size: 13px; color: #909399; font-weight: 400; margin-left: 8px; }
.title .st { margin-left: 10px; }
.panel { max-width: 720px; }
.waiting { display: flex; align-items: center; gap: 8px; color: #909399; padding: 20px 0; }
.spin { animation: rot 1s linear infinite; }
@keyframes rot { to { transform: rotate(360deg); } }
.step-label { font-size: 12px; color: #909399; }
.prompt { font-size: 20px; font-weight: 600; margin: 8px 0 16px; padding: 16px 18px; background: #f4f9f7; border-left: 4px solid #00b386; border-radius: 8px; line-height: 1.5; }
.prompt.empty { color: #909399; font-weight: 400; font-size: 15px; border-left-color: #dcdfe6; background: #f7f8fa; }
.tip { font-size: 13px; color: #606266; margin-bottom: 18px; line-height: 1.6; }
.ops { display: flex; gap: 12px; align-items: center; }
</style>
