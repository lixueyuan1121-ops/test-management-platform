<template>
  <iframe
    class="cloud-config"
    title="纳米Work 云控配置表"
    :srcdoc="cloudConfigHtml"
    sandbox="allow-scripts"
    referrerpolicy="no-referrer"
  />
</template>

<script setup>
import cloudConfigHtml from '@/assets/cloud-config.html?raw'
</script>

<style scoped>
.cloud-config {
  box-sizing: border-box;
  display: block;
  width: 100%;
  height: calc(100vh - 100px);
  height: calc(100dvh - 100px);
  min-height: 360px;
  border: 1px solid var(--tech-line);
  border-radius: 8px;
  background: #f5f6f8;
}
</style>
