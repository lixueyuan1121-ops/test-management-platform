<template>
  <SignCheckWorkspace :key="platform" :platform="platform" @switch-platform="platform = $event" />
</template>

<script setup>
import { ref } from 'vue'
import SignCheckWorkspace from '@/components/SignCheckWorkspace.vue'

const platform = ref('windows')
</script>
