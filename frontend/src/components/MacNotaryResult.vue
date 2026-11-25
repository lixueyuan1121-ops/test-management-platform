<template>
  <p v-if="current?.check_mode === 'mac-native'" class="scope">历史原生预检。团队 Mac 使用 codesign 检查原生文件的签名、Developer ID、时间戳及 hardened runtime。本地预检不等于 Apple 公证通过。</p>
  <p v-else class="scope">检查包内原生文件的代码页摘要、Developer ID 签名信息、时间戳和 hardened runtime 标记。证书信任及 Apple 公证状态需另行验证。</p>
  <template v-if="current">
    <p v-if="current.team_id">预期 Team ID <code>{{ current.team_id }}</code></p>
    <template v-if="report">
      <p v-if="report.checked">
        已检查 <b>{{ report.checked }}</b> 个原生文件，<template v-if="report.failed">其中 <b>{{ report.failed }}</b> 个存在签名问题。</template><template v-else>未发现签名问题。</template>
      </p>
      <p v-else>未扫描到可判读的原生文件。</p>
      <div v-if="report.warnings?.length" class="warning">
        <b>未完整扫描的内容</b>
        <ul><li v-for="(warning, i) in report.warnings" :key="i">{{ warning }}</li></ul>
      </div>
      <div v-for="section in detailSections" :key="section.field" class="findings">
        <h3>{{ section.title }}</h3>
        <div v-for="(item, i) in section.items" :key="i">
          <b>{{ item.path }}</b>
          <ul><li v-for="(msg, j) in item[section.field]" :key="j">{{ msg }}</li></ul>
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
const detailSections = computed(() => {
  const results = Array.isArray(report.value?.results) ? report.value.results : []
  return [{ field: 'problems', title: '签名问题' }, { field: 'notes', title: '提示（不影响结论）' }]
    .map(section => ({
      ...section,
      items: results.filter(item => Array.isArray(item?.[section.field]) && item[section.field].length),
    }))
    .filter(section => section.items.length)
})

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
.findings h3 { margin: 0 0 6px; font-size: 14px; }
ul { padding-inline-start: 20px; }
</style>
