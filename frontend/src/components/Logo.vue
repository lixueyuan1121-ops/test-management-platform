<template>
  <div class="mascot" :style="{ width: size + 'px', height: size + 'px' }">
    <svg viewBox="0 0 120 120" class="bug" :class="{ wiggle: animated }">
      <defs>
        <linearGradient id="bugBody" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#5ad1c4" />
          <stop offset="100%" stop-color="#2bb6a3" />
        </linearGradient>
        <linearGradient id="bugHead" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#7be0cf" />
          <stop offset="100%" stop-color="#3fc4b1" />
        </linearGradient>
      </defs>

      <!-- 触角 -->
      <g class="antenna left">
        <path d="M48 32 Q40 16 36 10" stroke="#2bb6a3" stroke-width="3" fill="none" stroke-linecap="round" />
        <circle cx="35" cy="9" r="4.5" fill="#5ad1c4" />
      </g>
      <g class="antenna right">
        <path d="M72 32 Q80 16 84 10" stroke="#2bb6a3" stroke-width="3" fill="none" stroke-linecap="round" />
        <circle cx="85" cy="9" r="4.5" fill="#5ad1c4" />
      </g>

      <!-- 小腿 -->
      <g stroke="#2bb6a3" stroke-width="3" stroke-linecap="round" class="legs">
        <path d="M24 70 L12 64" /><path d="M24 82 L10 84" /><path d="M22 92 L12 100" />
        <path d="M96 70 L108 64" /><path d="M96 82 L110 84" /><path d="M98 92 L108 100" />
      </g>

      <!-- 身体 -->
      <ellipse cx="60" cy="74" rx="40" ry="34" fill="url(#bugBody)" />
      <!-- 背上的对勾（测试通过） -->
      <path d="M40 74 L54 88 L82 56" stroke="#ffffff" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round" class="check" />

      <!-- 头 -->
      <circle cx="60" cy="44" r="20" fill="url(#bugHead)" />
      <!-- 眼睛 -->
      <g class="eyes">
        <ellipse class="eye left" cx="52" cy="44" rx="6" ry="7" fill="#fff" />
        <ellipse class="eye right" cx="68" cy="44" rx="6" ry="7" fill="#fff" />
        <circle cx="53" cy="45" r="3" fill="#1f2d3d" />
        <circle cx="69" cy="45" r="3" fill="#1f2d3d" />
        <circle cx="54" cy="43.5" r="1" fill="#fff" />
        <circle cx="70" cy="43.5" r="1" fill="#fff" />
      </g>
      <!-- 笑脸 -->
      <path d="M53 54 Q60 60 67 54" stroke="#1f2d3d" stroke-width="2.5" fill="none" stroke-linecap="round" />
      <!-- 腮红 -->
      <circle cx="46" cy="52" r="3" fill="#ff9a9e" opacity="0.7" />
      <circle cx="74" cy="52" r="3" fill="#ff9a9e" opacity="0.7" />
    </svg>
  </div>
</template>

<script setup>
defineProps({
  size: { type: Number, default: 56 },
  animated: { type: Boolean, default: true },
})
</script>

<style scoped>
.mascot { display: inline-block; }
.bug { width: 100%; height: 100%; display: block; transform-origin: 50% 80%; }
.bug.wiggle { animation: bugFloat 3.2s ease-in-out infinite; }

.antenna { transform-origin: 50px 30px; }
.antenna.left { animation: wiggleL 2.6s ease-in-out infinite; }
.antenna.right { animation: wiggleR 2.6s ease-in-out infinite; }

.eye { transform-origin: center; transform-box: fill-box; }
.eyes { animation: blink 4.5s ease-in-out infinite; transform-origin: 60px 44px; transform-box: fill-box; }

.check { stroke-dasharray: 60; stroke-dashoffset: 60; animation: drawCheck 1.2s ease-out 0.3s forwards; }

@keyframes bugFloat {
  0%, 100% { transform: translateY(0) rotate(0); }
  50% { transform: translateY(-3px) rotate(-1.5deg); }
}
@keyframes wiggleL {
  0%, 100% { transform: rotate(0); }
  50% { transform: rotate(-8deg); }
}
@keyframes wiggleR {
  0%, 100% { transform: rotate(0); }
  50% { transform: rotate(8deg); }
}
@keyframes blink {
  0%, 92%, 100% { transform: scaleY(1); }
  96% { transform: scaleY(0.1); }
}
@keyframes drawCheck {
  to { stroke-dashoffset: 0; }
}
</style>
