<template>
  <transition name="overlay-fade">
    <div v-if="app.visible" class="overlay">
      <div class="scene-box">
        <Logo :size="88" />
      </div>
      <div class="ld-text">加载中<span class="dots"><i /><i /><i /></span></div>
    </div>
  </transition>
</template>

<script setup>
import { useAppStore } from '@/store/app'
import Logo from '@/components/Logo.vue'
const app = useAppStore()
</script>

<style scoped>
.overlay {
  position: fixed; inset: 0; z-index: 3000;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 18px;
  background: linear-gradient(135deg, #1c2440, #243b6b, #3b2a6b, #1c2440);
  background-size: 300% 300%;
  animation: gradientShift 16s ease infinite;
}
.scene-box { animation: floatY 2.6s ease-in-out infinite; }
.ld-text { color: #c8d4f5; font-size: 14px; letter-spacing: 2px; display: flex; align-items: center; }
.dots { display: inline-flex; gap: 3px; margin-left: 4px; }
.dots i {
  width: 5px; height: 5px; border-radius: 50%; background: #9db4ff; display: inline-block;
  animation: dotPulse 1.2s ease-in-out infinite;
}
.dots i:nth-child(2) { animation-delay: 0.2s; }
.dots i:nth-child(3) { animation-delay: 0.4s; }

@keyframes floatY { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
@keyframes dotPulse { 0%,80%,100% { opacity: 0.3; transform: scale(0.8); } 40% { opacity: 1; transform: scale(1.2); } }

.overlay-fade-enter-active, .overlay-fade-leave-active { transition: opacity 0.25s ease; }
.overlay-fade-enter-from, .overlay-fade-leave-to { opacity: 0; }
</style>
