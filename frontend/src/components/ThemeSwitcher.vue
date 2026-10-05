<template>
  <el-dropdown trigger="click" @command="setTheme">
    <button class="theme-switcher" type="button" aria-label="切换界面主题" data-testid="theme-switcher">
      <el-icon><Moon v-if="theme === 'tech'" /><Sunny v-else /></el-icon>
      <span class="theme-label">{{ currentLabel }}</span>
      <el-icon class="theme-arrow"><ArrowDown /></el-icon>
    </button>
    <template #dropdown>
      <el-dropdown-menu aria-label="界面主题">
        <el-dropdown-item v-for="item in THEMES" :key="item.value" :command="item.value"
          :class="{ 'theme-current': theme === item.value }">
          <span>{{ item.label }}</span>
          <el-icon v-if="theme === item.value" aria-label="当前主题"><Check /></el-icon>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup>
import { computed } from 'vue'
import { Moon, Sunny, ArrowDown, Check } from '@element-plus/icons-vue'
import { THEMES, theme, setTheme } from '@/utils/theme'
const currentLabel = computed(() => THEMES.find(item => item.value === theme.value).label)
</script>

<style scoped>
.theme-switcher { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 34px; padding: 6px 10px; border: 1px solid var(--tech-line); border-radius: var(--tech-control-radius); background: var(--tech-panel); color: var(--tech-fg); font: inherit; font-size: 12px; cursor: pointer; white-space: nowrap; }
.theme-switcher:hover { background: var(--tech-signal-weak); border-color: var(--tech-signal-line); }
.theme-switcher:focus-visible { outline: 2px solid var(--tech-signal); outline-offset: 3px; }
.theme-arrow { font-size: 10px; color: var(--tech-muted); }
.theme-current { color: var(--tech-signal); font-weight: 600; gap: 16px; }
@media (max-width: 700px) { .theme-label, .theme-arrow { display: none; } .theme-switcher { min-width: 36px; padding: 8px; } }
@media (pointer: coarse) { .theme-switcher { min-width: 44px; min-height: 44px; } }
</style>
