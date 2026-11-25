<template>
  <p class="scope">团队 Mac 使用 codesign 检查原生文件的签名、Developer ID、时间戳及 hardened runtime。本地预检不等于 Apple 公证通过。</p>
  <template v-if="current">
    <p v-if="current.team_id">预期 Team ID <code>{{ current.team_id }}</code></p>
    <template v-if="report">
      <p>已检查 <b>{{ report.checked }}</b> 个原生文件，<b>{{ report.failed }}</b> 个发现问题。</p>
      <p v-if="!report.checked">未发现可判读的原生文件。</p>
      <div v-if="report.warnings?.length" class="warning">
        <b>未覆盖内容</b>
        <ul><li v-for="(warning, i) in report.warnings" :key="i">{{ warning }}</li></ul>
      </div>
      <div v-if="findings.length" class="findings">
        <div v-for="(item, i) in findings" :key="i">
          <b>{{ item.path }}</b>
          <ul><li v-for="(msg, j) in [...item.problems, ...item.notes]" :key="j">{{ msg }}</li></ul>
        </div>
      </div>
    </template>
    <div class="downloads" v-if="report || log">
      <el-button v-if="report" size="small" @click="download('json')">下载 JSON 报告</el-button>
      <el-button v-if="log" size="small" @click="download('log')">下载执行日志</el-button>
    </div>
  </template>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ current: Object, log: String })
const report = computed(() => props.current?.report)
const findings = computed(() => (Array.isArray(report.value?.results) ? report.value.results : [])
  .filter(r => Array.isArray(r?.problems) && Array.isArray(r?.notes) && (r.problems.length || r.notes.length)))

function download(ext) {
  const json = ext === 'json'
  const data = json ? JSON.stringify(report.value, null, 2) : props.log
  const link = document.createElement('a')
  const url = URL.createObjectURL(new Blob([data], { type: json ? 'application/json' : 'text/plain;charset=utf-8' }))
  link.href = url
  link.download = props.current.filename + '-' + props.current.id + '.' + ext
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<style scoped>
.scope { color: var(--tech-muted); line-height: 1.6; font-size: 13px; }
.warning { color: var(--tech-warn); }
.findings, .warning { max-height: 16rem; overflow: auto; overflow-wrap: anywhere; }
.findings, .downloads { margin-top: 12px; }
ul { padding-inline-start: 20px; }
</style>
