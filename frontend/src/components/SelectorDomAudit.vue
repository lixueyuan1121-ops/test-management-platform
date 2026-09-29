<template>
  <section class="dom-audit" aria-label="DOM 自动巡检">
    <div class="audit-bar"><strong>DOM 自动巡检</strong><el-button type="primary" size="small" :loading="working" :disabled="!projectId || active || disabled" @click="run">立即巡检</el-button><el-button size="small" :disabled="!projectId || disabled" @click="settingsOpen = true">每日巡检设置</el-button><el-button size="small" :disabled="!projectId" @click="openResults">巡检记录</el-button><el-tag v-if="config.enabled" type="success">每天 {{ config.daily_time }}（北京时间）</el-tag><span v-else class="hint">每日巡检未开启</span></div>
    <p class="hint">设备需运行新版 Runner 并登录应用；巡检只访问已配置的页面入口，成功后同步新增与更新。未覆盖的页面不会直接标为废弃。</p>
    <p v-if="config.next_run_at" class="hint">下次运行：{{ new Date(config.next_run_at).toLocaleString() }}</p>
    <el-alert v-if="error || config.error" :title="error || config.error" type="warning" :closable="false" />
    <p v-if="active">{{ active.status === 'running' ? '正在巡检设备中的页面…' : '等待设备领取；长时间未领取请更新并启动 Runner。' }} <el-button link type="danger" @click="cancel">取消巡检</el-button></p>
    <p v-else-if="latest">最近巡检：{{ statusText(latest.status) }}<template v-if="latest.status === 'done'"> · 扫描 {{ latest.summary.scanned ?? "—" }} 次 · 归并后 {{ latest.summary.registered ?? "—" }} 个选择器 · 新增 {{ latest.summary.new || 0 }} · 更新 {{ latest.summary.updated || 0 }} · 废弃 {{ latest.summary.retired || 0 }}</template><span v-if="latest.error"> · {{ latest.error }}</span></p>
    <el-dialog v-model="settingsOpen" title="每日 DOM 巡检" width="560px">
      <el-form label-width="110px"><el-form-item label="执行设备"><el-select v-model="form.device_id" placeholder="选择自己的设备" style="width:100%"><el-option v-for="d in devices" :key="d.id" :value="d.id" :label="`${d.name || d.runner_id}（${d.runner_id}）`" /></el-select></el-form-item><el-form-item label="每天自动运行"><el-switch v-model="form.enabled" /></el-form-item><el-form-item label="运行时间"><el-time-select v-model="form.daily_time" start="00:00" step="00:30" end="23:30" :clearable="false" /> <span class="hint">北京时间</span></el-form-item></el-form>
      <p>巡检页面：{{ pages.map(p => p.label).join('、') }}</p><p class="hint">离线或设备忙时延后重试；不会同时启动多次巡检。包含列表、详情、设置和弹窗；列表抽样打开一项以覆盖模板。不存在或不可访问的入口会单独列为未覆盖，不会提交表单或删除数据。</p>
      <template #footer><el-button @click="settingsOpen = false">取消</el-button><el-button type="primary" :loading="working" @click="save">保存设置</el-button></template>
    </el-dialog>
    <el-dialog v-model="resultsOpen" title="DOM 巡检记录（最近 20 次）" width="min(1000px,95vw)">
      <el-table :data="runs" empty-text="暂无巡检记录"><el-table-column label="时间" width="180"><template #default="{row}">{{ new Date(row.created_at).toLocaleString() }}</template></el-table-column><el-table-column label="触发方式" width="100"><template #default="{row}">{{ row.trigger === 'daily' ? '每日定时' : '手动' }}</template></el-table-column><el-table-column label="状态" width="100"><template #default="{row}">{{ statusText(row.status) }}</template></el-table-column><el-table-column label="结果"><template #default="{row}"><span v-if="row.status==='done'">扫描 {{ row.summary.scanned ?? "—" }} 次 · 归并后 {{ row.summary.registered ?? "—" }} 个 · 新增 {{ row.summary.new }} · 更新 {{ row.summary.updated }} · 废弃 {{ row.summary.retired }} · 冲突跳过 {{ row.summary.conflicts }}</span><span v-else>{{ row.error || '等待执行完成' }}</span></template></el-table-column><el-table-column label="页面覆盖" width="100"><template #default="{row}"><el-button v-if="row.summary.pages" link @click="detail = row">查看</el-button></template></el-table-column></el-table>
    </el-dialog>
    <el-dialog :model-value="!!detail" title="页面覆盖与变更" width="min(900px,95vw)" @close="detail=null"><p v-if="detail">扫描 {{ detail.summary.scanned ?? "—" }} 次；归并重复 {{ detail.summary.merged ?? "—" }} 次；列表集合 {{ detail.summary.collections ?? "—" }} 个；testid {{ detail.summary.testid ?? "—" }} / CSS {{ detail.summary.css ?? "—" }} / XPath {{ detail.summary.xpath ?? "—" }}；逐项定位归并 {{ detail.summary.superseded || 0 }}；无有效定位 {{ detail.summary.unverified || 0 }}；冲突 {{ detail.summary.conflicts || 0 }}。</p><el-table :data="detail?.summary.pages || []"><el-table-column prop="label" label="页面"/><el-table-column label="结果"><template #default="{row}">{{ row.complete ? '完成' : row.ready ? '部分完成' : '未覆盖' }}</template></el-table-column><el-table-column prop="count" label="采集数" width="90"/><el-table-column prop="error" label="说明" min-width="260" /></el-table><el-table :data="detail?.summary.changes || []" max-height="320"><el-table-column prop="key" label="key"/><el-table-column prop="page" label="页面"/><el-table-column label="归并说明"><template #default="{row}">{{ row.replacement ? `${row.reason}：${row.replacement}` : "—" }}</template></el-table-column><el-table-column label="状态"><template #default="{row}">{{ {new:'新增',updated:'更新',retired:'废弃'}[row.status] }}</template></el-table-column></el-table></el-dialog>
  </section>
</template>
<script setup>
import { ref, reactive, computed, watch, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { getSelectorAudit, saveSelectorAuditSchedule, runSelectorAudit, cancelSelectorAudit } from '@/api'
const props=defineProps({projectId:Number,subProduct:{type:String,default:''},devices:{type:Array,default:()=>[]},disabled:Boolean})
const emit=defineEmits(['synced'])
const config=ref({}),runs=ref([]),pages=ref([]),error=ref(''),working=ref(false),settingsOpen=ref(false),resultsOpen=ref(false),detail=ref(null)
const form=reactive({device_id:null,enabled:false,daily_time:'09:00'})
const latest=computed(()=>runs.value[0]),active=computed(()=>runs.value.find(r=>['pending','running'].includes(r.status)))
const statusText=s=>({pending:'排队中',running:'执行中',done:'完成',failed:'失败/已取消'}[s]||s)
let version=0,timer=null,disposed=false
async function load(hydrate=false) {
 const v=version
 if(!props.projectId)return
 try {
  const data=await getSelectorAudit({project_id:props.projectId,sub_product:props.subProduct})
  if(disposed || v!==version)return
  const previous=runs.value[0]; config.value=data.config||{};runs.value=data.runs||[];pages.value=data.pages||[];error.value=''
  if(hydrate)Object.assign(form,{device_id:config.value.device_id||props.devices[0]?.id,enabled:!!config.value.enabled,daily_time:config.value.daily_time||'09:00'})
  if(previous && ['pending','running'].includes(previous.status) && latest.value?.status==='done')emit('synced')
 }catch(e){if(v===version)error.value=e?.response?.data?.msg||'巡检状态读取失败'}
 finally{if(v===version && !disposed){clearTimeout(timer);if(active.value)timer=setTimeout(()=>load(),5000)}}
}
watch(()=>[props.projectId,props.subProduct],()=>{version++;clearTimeout(timer);runs.value=[];config.value={};settingsOpen.value=false;resultsOpen.value=false;load(true)},{immediate:true})
watch(()=>props.devices,()=>{if(!form.device_id)form.device_id=props.devices[0]?.id})
const body=()=>({project_id:props.projectId,sub_product:props.subProduct,...form})
async function save(){if(!form.device_id){ElMessage.warning('请选择执行设备');return}working.value=true;try{await saveSelectorAuditSchedule(body());settingsOpen.value=false;await load(true);ElMessage.success('巡检设置已保存')}finally{working.value=false}}
async function run(){if(!form.device_id){settingsOpen.value=true;ElMessage.info('请先选择执行设备');return}working.value=true;try{await runSelectorAudit(body());await load();ElMessage.success('已下发 DOM 巡检')}finally{working.value=false}}
async function cancel(){await cancelSelectorAudit(active.value.probe_id);await load()}
async function openResults(){resultsOpen.value=true;await load()}
onBeforeUnmount(()=>{disposed=true;version++;clearTimeout(timer)})
</script>
<style scoped>
.dom-audit{padding:16px 0;border-bottom:1px solid var(--el-border-color-lighter);margin-bottom:16px}.audit-bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.hint{color:var(--el-text-color-secondary);font-size:12px;line-height:1.7}
</style>
