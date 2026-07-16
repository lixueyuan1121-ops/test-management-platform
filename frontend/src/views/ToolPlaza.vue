<template>
  <div class="plaza">
    <div class="plaza-header">
      <div class="plaza-title">
        <span class="title-icon">🧰</span>
        <div>
          <h2>测试工具广场</h2>
          <p class="subtitle">团队自研辅助测试工具集，点击卡片查看详情</p>
        </div>
      </div>
      <el-select v-model="catFilter" placeholder="全部分类" clearable size="default" style="width:200px" @change="load">
        <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>
    </div>

    <div v-loading="loading" class="plaza-body">
      <el-empty v-if="!tools.length" description="暂无已上线工具" />
      <div class="tool-grid">
        <div v-for="t in tools" :key="t.id" class="tool-card" @click="onTool(t)">
          <div class="card-accent" :style="{ background: accentColor(t.category_id) }"></div>
          <div class="card-content">
            <div class="card-top">
              <div class="card-icon" :style="{ background: accentBg(t.category_id) }">
                {{ t.icon || '🔧' }}
              </div>
              <div class="card-info">
                <div class="card-name">{{ t.name }}</div>
                <div class="card-meta">
                  <span class="card-cat">{{ t.category_name }}</span>
                  <span v-if="t.version" class="card-ver">v{{ t.version }}</span>
                </div>
              </div>
            </div>
            <div class="card-desc">{{ t.description || '暂无描述' }}</div>
            <div class="card-bottom">
              <span v-if="t.download_url" class="card-tag tag-download">可下载</span>
              <span v-if="t.doc_url" class="card-tag tag-doc">有文档</span>
              <span class="card-tag tag-online">已上线</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-if="detail.visible" v-model="detail.visible" :title="detail.name" width="520px" class="detail-dialog">
      <el-descriptions :column="1" border size="default">
        <el-descriptions-item label="分类">{{ detail.category_name }}</el-descriptions-item>
        <el-descriptions-item label="版本" v-if="detail.version">v{{ detail.version }}</el-descriptions-item>
        <el-descriptions-item label="描述">{{ detail.description || '暂无' }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button v-if="detail.doc_url" @click="openLink(detail.doc_url)">查看文档</el-button>
        <el-button v-if="detail.download_url" type="primary" @click="copyUrl(detail.download_url)">复制下载地址</el-button>
        <el-button @click="detail.visible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listCategories, listTools } from '@/api'

const categories = ref([])
const catFilter = ref(null)
const tools = ref([])
const loading = ref(false)
const detail = reactive({ visible: false, name: '', description: '', doc_url: '', download_url: '', category_name: '', version: '' })

onMounted(async () => {
  categories.value = await listCategories()
  await load()
})

async function load() {
  loading.value = true
  try {
    tools.value = await listTools({ category_id: catFilter.value || undefined, online_only: true })
  } finally { loading.value = false }
}

function onTool(t) {
  Object.assign(detail, { visible: true, name: t.name, description: t.description, doc_url: t.doc_url, download_url: t.download_url, category_name: t.category_name, version: t.version })
}
function openLink(url) { window.open(url, '_blank') }
function copyUrl(url) {
  navigator.clipboard.writeText(url).then(() => ElMessage.success('下载地址已复制到剪贴板')).catch(() => {
    const ta = document.createElement('textarea'); ta.value = url; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); ElMessage.success('下载地址已复制')
  })
}

const morandi = ['#a8b5a2','#b8a9c9','#c9a9a6','#a3b8c8','#c4b896','#a9c4b8','#c2a3a3','#b0c4b1']
function accentColor(cid) { return morandi[(cid || 0) % morandi.length] }
function accentBg(cid) { return morandi[(cid || 0) % morandi.length] + '22' }
</script>

<style scoped>
.plaza { padding: 4px; }

.plaza-header {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 28px; padding: 0 4px;
}
.plaza-title { display: flex; align-items: center; gap: 14px; }
.plaza-title h2 { margin: 0; font-size: 20px; font-weight: 600; color: #303133; letter-spacing: 0.5px; }
.title-icon { font-size: 32px; }
.subtitle { margin: 4px 0 0; font-size: 13px; color: #909399; }

.plaza-body { min-height: 200px; }

.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 18px;
}

.tool-card {
  position: relative;
  background: #fff;
  border-radius: 14px;
  overflow: hidden;
  cursor: pointer;
  border: 1px solid #f0f0f0;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.02);
  transition: box-shadow 0.25s ease, transform 0.25s ease;
}
.tool-card:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.04);
  transform: translateY(-2px);
}

.card-accent {
  height: 4px; width: 100%;
}

.card-content { padding: 20px 22px 18px; }

.card-top { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }

.card-icon {
  width: 48px; height: 48px; border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; flex-shrink: 0;
}

.card-info { flex: 1; min-width: 0; }
.card-name {
  font-size: 15px; font-weight: 600; color: #303133;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.card-meta { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.card-cat {
  font-size: 12px; color: #909399; background: #f5f7fa; padding: 1px 8px;
  border-radius: 4px; line-height: 20px;
}
.card-ver { font-size: 11px; color: #b0b4bb; }

.card-desc {
  font-size: 13px; color: #606266; line-height: 1.6;
  overflow: hidden; text-overflow: ellipsis;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  margin-bottom: 14px; min-height: 42px;
}

.card-bottom { display: flex; gap: 6px; flex-wrap: wrap; }

.card-tag {
  font-size: 11px; padding: 2px 10px; border-radius: 20px;
  line-height: 18px; font-weight: 500; letter-spacing: 0.3px;
}
.tag-online { background: #e8f5e9; color: #66bb6a; }
.tag-download { background: #e3f2fd; color: #42a5f5; }
.tag-doc { background: #fff3e0; color: #ffa726; }
</style>
