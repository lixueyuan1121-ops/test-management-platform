<template>
  <main class="shared-digest">
    <div v-if="loading" class="share-status">正在加载日报…</div>
    <div v-else-if="error" class="share-status"><p>{{ error }}</p><el-button @click="load">重试</el-button><el-button @click="$router.push('/tasks')">返回任务台账</el-button></div>
    <iframe v-else :srcdoc="html" title="测试进度日报" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" />
  </main>
</template>
<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getSharedTaskDigest } from '@/api/taskLedger'
const route = useRoute()
const html = ref(''), loading = ref(false), error = ref('')
async function load() {
  loading.value = true; error.value = ''; html.value = ''
  try { html.value = (await getSharedTaskDigest(route.params.code)).html }
  catch (err) { error.value = err.response?.status === 403 ? '你没有该日报所属项目的查看权限' : err.response?.data?.msg || '日报加载失败，请重试' }
  finally { loading.value = false }
}
watch(() => route.params.code, load, { immediate: true })
</script>
<style scoped>
.shared-digest { height:100vh; background:#f5f5f7; }
.shared-digest iframe { width:100%; height:100%; border:0; display:block; }
.share-status { text-align:center; padding:64px 20px; color:#606266; }
</style>
