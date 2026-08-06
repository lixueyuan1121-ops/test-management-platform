<template>
  <div class="target" :style="{ width: size + 'px', height: size + 'px' }">
    <svg viewBox="0 0 120 120" :class="{ scan: animated }">
      <!-- 外框角标：工业取景框四角 -->
      <g stroke="var(--tm-line, #2a2f37)" stroke-width="2" fill="none" stroke-linecap="square">
        <path d="M10 26 L10 10 L26 10" />
        <path d="M94 10 L110 10 L110 26" />
        <path d="M110 94 L110 110 L94 110" />
        <path d="M26 110 L10 110 L10 94" />
      </g>

      <!-- 同心圆靶环 -->
      <circle cx="60" cy="60" r="40" fill="none" stroke="var(--tm-line, #2a2f37)" stroke-width="2" />
      <circle cx="60" cy="60" r="27" fill="none" stroke="var(--tm-dim, #4a525c)" stroke-width="2" />

      <!-- 十字准星 -->
      <g stroke="var(--tm-dim, #4a525c)" stroke-width="2" stroke-linecap="square">
        <path d="M60 8 L60 24" />
        <path d="M60 96 L60 112" />
        <path d="M8 60 L24 60" />
        <path d="M96 60 L112 60" />
      </g>

      <!-- 扫描环（旋转，仅 animated 时动） -->
      <circle
        class="sweep"
        cx="60" cy="60" r="40"
        fill="none"
        stroke="var(--tm-signal, #00e5a0)"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-dasharray="30 221"
      />

      <!-- 中心对勾（测试通过） -->
      <path
        class="check"
        d="M46 60 L56 70 L76 48"
        fill="none"
        stroke="var(--tm-signal, #00e5a0)"
        stroke-width="4.5"
        stroke-linecap="square"
        stroke-linejoin="miter"
      />
    </svg>
  </div>
</template>

<script setup>
defineProps({
  size: { type: Number, default: 120 },
  animated: { type: Boolean, default: true },
})
</script>

<style scoped>
.target { display: inline-block; }
.target svg { width: 100%; height: 100%; display: block; }

/* 扫描环缓慢旋转 */
.sweep { transform-origin: 60px 60px; opacity: 0; }
.scan .sweep {
  opacity: 1;
  animation: sweepRot 3.2s linear infinite;
}
@keyframes sweepRot {
  to { transform: rotate(360deg); }
}

/* 对勾一次性描绘（仅动画模式从头画；静态模式直接显示） */
.check { stroke-dasharray: 60; stroke-dashoffset: 0; }
.scan .check { stroke-dashoffset: 60; animation: drawCheck 0.9s ease-out 0.5s forwards; }
@keyframes drawCheck {
  to { stroke-dashoffset: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .scan .sweep { animation: none; }
  .scan .check { animation: none; stroke-dashoffset: 0; }
}
</style>
