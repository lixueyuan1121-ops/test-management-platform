<template>
  <div class="commander-entry">
    <el-tabs v-model="tab">
      <el-tab-pane label="测试目标" name="missions"><MissionWorkspace v-if="tab === 'missions'" /></el-tab-pane>
      <el-tab-pane label="项目问答" name="chat"><CommanderChat v-if="tab === 'chat'" /></el-tab-pane>
    </el-tabs>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import MissionWorkspace from '@/components/MissionWorkspace.vue'
import CommanderChat from '@/components/CommanderChat.vue'
const tab = ref('missions')
</script>
<style scoped>.commander-entry { padding:0 20px; } @media(max-width:640px){.commander-entry{padding:0 8px}}</style>
