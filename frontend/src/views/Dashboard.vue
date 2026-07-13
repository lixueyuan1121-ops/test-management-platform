<template>
  <div class="dashboard stagger">
    <!-- 欢迎横幅 -->
    <el-card class="banner anim-fade-up" shadow="never" body-style="padding:24px 28px;" style="animation-delay:0s">
      <div class="banner-inner">
        <div>
          <div class="hi">{{ greeting }}，{{ auth.user?.name || auth.user?.username }} 👋</div>
          <div class="hi-sub">{{ auth.user?.is_platform_admin ? '平台管理员视角 · 管理所有项目与人员' : '项目成员视角 · 查看你的任务与日报' }}</div>
        </div>
        <div class="banner-stats">
          <div class="bs"><div class="bs-num">{{ projects.length }}</div><div class="bs-lbl">可见项目</div></div>
          <div class="bs"><div class="bs-num">{{ auth.memberships.length }}</div><div class="bs-lbl">成员关系</div></div>
        </div>
      </div>
    </el-card>

    <!-- 快捷入口卡片 -->
    <el-row :gutter="16" class="quick">
      <el-col :span="6" v-for="(q, i) in quickEntries" :key="q.path">
        <div class="quick-card hover-float" @click="$router.push(q.path)" :style="{ background: q.bg, animationDelay: (0.1 + i * 0.08) + 's' }">
          <el-icon class="qicon"><component :is="q.icon" /></el-icon>
          <div class="qtext">
            <div class="qtitle">{{ q.title }}</div>
            <div class="qdesc">{{ q.desc }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 我的项目 -->
    <el-card class="block anim-fade-up" style="animation-delay:0.45s">
      <template #header>
        <div class="card-head">
          <span class="card-title">我的项目</span>
          <el-button v-if="auth.isPlatformAdmin" link type="primary" @click="$router.push('/projects')">去管理</el-button>
        </div>
      </template>
      <el-table :data="projects" v-loading="projectsLoading" size="default" empty-text="暂无项目，去「组织管理-项目管理」创建">
        <el-table-column prop="code" label="编码" width="140" />
        <el-table-column prop="name" label="名称" />
        <el-table-column label="我的角色" width="120">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.id)" effect="light">{{ roleLabel(row.id) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small" effect="plain">
              {{ row.status === 'active' ? '活跃' : '归档' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="primary" @click="$router.push(`/projects/${row.id}/members`)">成员管理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/store/auth'
import { listProjects } from '@/api'
import { Files, List, DataLine, TrendCharts } from '@element-plus/icons-vue'

const auth = useAuthStore()
const projects = ref([])
const projectsLoading = ref(false)

onMounted(async () => {
  projectsLoading.value = true
  try { projects.value = await listProjects() }
  finally { projectsLoading.value = false }
})

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const quickEntries = computed(() => {
  const base = [
    { path: '/stats', title: '日报统计', desc: '应交/已交/上线一览', icon: DataLine, bg: 'linear-gradient(135deg,#36a3eb,#1f7fd6)' },
    { path: '/workload', title: '工作量统计', desc: '人时趋势图表', icon: TrendCharts, bg: 'linear-gradient(135deg,#67c23a,#3fa436)' },
    { path: '/issues', title: '遗留问题', desc: '未解决缺陷跟踪', icon: Files, bg: 'linear-gradient(135deg,#e6a23c,#d48806)' },
  ]
  if (auth.isPlatformAdmin) {
    base.unshift({ path: '/tasks', title: '任务分配', desc: '分配每日工作任务', icon: List, bg: 'linear-gradient(135deg,#9b59b6,#7d3c98)' })
  } else {
    base.unshift({ path: '/my-reports', title: '我的日报', desc: '填报今日测试进度', icon: List, bg: 'linear-gradient(135deg,#9b59b6,#7d3c98)' })
  }
  return base
})

function roleLabel(pid) {
  const r = auth.roleIn(pid)
  if (r === 'admin') return '管理员'
  if (r === 'member') return '成员'
  if (r === 'guest') return '嘉宾'
  return r || '-'
}
function roleTagType(pid) {
  const r = auth.roleIn(pid)
  if (r === 'admin') return 'danger'
  if (r === 'member') return 'success'
  if (r === 'guest') return 'info'
  return ''
}
</script>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: 16px; }
.banner { border: none; border-radius: 10px; background: linear-gradient(120deg,#1f2d3d 0%,#2c3e50 100%); color: #fff; }
.banner-inner { display: flex; justify-content: space-between; align-items: center; }
.hi { font-size: 20px; font-weight: 600; }
.hi-sub { font-size: 13px; color: #c0c4cc; margin-top: 6px; }
.banner-stats { display: flex; gap: 28px; }
.bs { text-align: center; }
.bs-num { font-size: 26px; font-weight: 700; }
.bs-lbl { font-size: 12px; color: #c0c4cc; margin-top: 2px; }

.quick { margin: 0 !important; }
.quick-card {
  display: flex; align-items: center; gap: 14px; padding: 18px 18px; border-radius: 10px;
  color: #fff; cursor: pointer; height: 76px;
  animation: fadeInUp 0.5s ease-out backwards;
}
.quick-card:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,0.15); }
.qicon { font-size: 26px; }
.qtitle { font-size: 15px; font-weight: 600; }
.qdesc { font-size: 12px; opacity: .85; margin-top: 3px; }

.block { border-radius: 10px; }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.card-title { font-weight: 600; }
</style>
