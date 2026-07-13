<template>
  <div class="scene" :style="{ width: width + 'px' }">
    <svg viewBox="0 0 480 320" class="svg">
      <defs>
        <linearGradient id="scBg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#243b6b" />
          <stop offset="55%" stop-color="#3b2a6b" />
          <stop offset="100%" stop-color="#1c2440" />
        </linearGradient>
        <radialGradient id="scGlow" cx="50%" cy="45%" r="55%">
          <stop offset="0%" stop-color="#6a8cff" stop-opacity="0.55" />
          <stop offset="60%" stop-color="#6a8cff" stop-opacity="0.12" />
          <stop offset="100%" stop-color="#6a8cff" stop-opacity="0" />
        </radialGradient>
        <linearGradient id="screenGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0f2a3a" />
          <stop offset="100%" stop-color="#0a1a2a" />
        </linearGradient>
        <linearGradient id="bugShell" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#5ad1c4" />
          <stop offset="100%" stop-color="#279a87" />
        </linearGradient>
        <linearGradient id="bugHead" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#7be0cf" />
          <stop offset="100%" stop-color="#3fc4b1" />
        </linearGradient>
        <linearGradient id="deskGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3a4258" />
          <stop offset="100%" stop-color="#262c3d" />
        </linearGradient>
      </defs>

      <!-- 背景渐变 + 柔光 -->
      <rect width="480" height="320" rx="16" fill="url(#scBg)" />
      <ellipse class="glow" cx="240" cy="150" rx="190" ry="120" fill="url(#scGlow)" />

      <!-- 显示器 -->
      <g class="monitor">
        <rect x="234" y="208" width="12" height="40" fill="#2b3040" />
        <rect x="206" y="246" width="68" height="7" rx="3" fill="#2b3040" />
        <rect x="148" y="78" width="184" height="138" rx="12" fill="#1a2030" stroke="#33394d" stroke-width="2" />
        <rect x="160" y="90" width="160" height="114" rx="6" fill="url(#screenGrad)" />
        <!-- 屏幕内容：进度条 + 对勾 + 代码行 -->
        <g class="screen-content">
          <rect x="172" y="104" width="60" height="6" rx="3" fill="#2f5d7a" opacity="0.7" />
          <rect x="172" y="118" width="100" height="5" rx="2" fill="#2f5d7a" opacity="0.5" />
          <rect x="172" y="130" width="80" height="5" rx="2" fill="#2f5d7a" opacity="0.5" />
          <!-- 进度条 -->
          <rect x="172" y="150" width="120" height="8" rx="4" fill="#1b2c3a" />
          <rect class="bar-fill" x="172" y="150" width="0" height="8" rx="4" fill="#5ad1c4" />
          <!-- 对勾 -->
          <path class="check2" d="M300 110 L310 120 L328 100" stroke="#5ad1c4" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round" />
        </g>
      </g>

      <!-- 键盘 -->
      <g class="keyboard">
        <rect x="190" y="244" width="100" height="16" rx="5" fill="#2b3040" />
        <g fill="#3a4258">
          <circle cx="205" cy="252" r="2.2" /><circle cx="216" cy="252" r="2.2" /><circle cx="227" cy="252" r="2.2" />
          <circle cx="238" cy="252" r="2.2" /><circle cx="249" cy="252" r="2.2" /><circle cx="260" cy="252" r="2.2" />
          <circle cx="271" cy="252" r="2.2" /><circle cx="282" cy="252" r="2.2" />
        </g>
      </g>

      <!-- 测试虫（背视图，坐在桌前打字） -->
      <g class="bug-worker">
        <!-- 身体/壳 -->
        <ellipse cx="240" cy="262" rx="40" ry="30" fill="url(#bugShell)" />
        <path class="check-back" d="M222 260 L234 273 L260 244" stroke="#ffffff" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round" />
        <!-- 头（顶部） -->
        <ellipse cx="240" cy="236" rx="24" ry="20" fill="url(#bugHead)" />
        <!-- 触角 -->
        <g class="ant">
          <path d="M230 222 Q222 210 220 200" stroke="#279a87" stroke-width="3" fill="none" stroke-linecap="round" />
          <circle cx="219" cy="199" r="4" fill="#5ad1c4" />
        </g>
        <g class="ant">
          <path d="M250 222 Q258 210 260 200" stroke="#279a87" stroke-width="3" fill="none" stroke-linecap="round" />
          <circle cx="261" cy="199" r="4" fill="#5ad1c4" />
        </g>
        <!-- 打字的手（左右交替） -->
        <g class="hand left-hand">
          <path d="M208 262 Q200 258 200 250" stroke="#279a87" stroke-width="7" fill="none" stroke-linecap="round" />
          <circle cx="200" cy="248" r="6" fill="#7be0cf" />
        </g>
        <g class="hand right-hand">
          <path d="M272 262 Q280 258 280 250" stroke="#279a87" stroke-width="7" fill="none" stroke-linecap="round" />
          <circle cx="280" cy="248" r="6" fill="#7be0cf" />
        </g>
      </g>

      <!-- 咖啡杯 + 蒸汽 -->
      <g class="coffee">
        <path class="steam" d="M378 232 Q384 222 378 212 Q372 202 378 192" stroke="#cfe3ff" stroke-width="3" fill="none" stroke-linecap="round" />
        <path class="steam s2" d="M390 232 Q396 222 390 212 Q384 202 390 192" stroke="#cfe3ff" stroke-width="3" fill="none" stroke-linecap="round" />
        <path d="M368 234 h28 v14 a4 4 0 0 1 -4 4 h-20 a4 4 0 0 1 -4 -4 z" fill="#3a4258" />
        <path d="M396 238 q8 0 8 6 q0 6 -8 6" stroke="#3a4258" stroke-width="3" fill="none" />
      </g>

      <!-- 桌面（前缘，遮住虫下半身） -->
      <rect x="30" y="270" width="420" height="10" rx="5" fill="url(#deskGrad)" />
    </svg>

    <!-- 漂浮粒子（CSS，轻量） -->
    <div class="dots">
      <span v-for="n in 10" :key="n" :style="dotStyle(n)" />
    </div>
  </div>
</template>

<script setup>
defineProps({ width: { type: Number, default: 480 } })

function dotStyle(n) {
  const left = (n * 41) % 100
  const size = 4 + (n * 7) % 7
  const dur = 7 + (n * 5) % 8
  const delay = (n * 1.7) % 6
  return {
    left: left + '%',
    width: size + 'px',
    height: size + 'px',
    animationDuration: dur + 's',
    animationDelay: '-' + delay + 's',
  }
}
</script>

<style scoped>
.scene { position: relative; display: inline-block; }
.svg { width: 100%; height: auto; display: block; border-radius: 16px; }

.glow { animation: glowPulse 4s ease-in-out infinite; transform-origin: 240px 150px; transform-box: fill-box; }
.monitor { animation: floatY 5s ease-in-out infinite; transform-origin: 240px 150px; transform-box: fill-box; }

.bar-fill { animation: barFill 3.2s ease-in-out infinite; }
.check2 { stroke-dasharray: 40; stroke-dashoffset: 40; animation: drawC 2.6s ease-out infinite; }

.bug-worker { animation: bob 2.4s ease-in-out infinite; transform-origin: 240px 262px; transform-box: fill-box; }
.ant { transform-origin: 230px 222px; transform-box: fill-box; animation: wig 3s ease-in-out infinite; }
.ant:last-of-type { animation-delay: -1.5s; }
.hand { transform-origin: 210px 262px; transform-box: fill-box; }
.left-hand { animation: typeL 0.5s ease-in-out infinite; }
.right-hand { animation: typeR 0.5s ease-in-out infinite; }
.check-back { stroke-dasharray: 50; stroke-dashoffset: 50; animation: drawC 2.8s ease-out 0.2s infinite; }

.coffee { animation: floatY 5s ease-in-out infinite; transform-origin: 382px 250px; transform-box: fill-box; }
.steam { animation: rise 3s ease-in-out infinite; transform-origin: 378px 212px; transform-box: fill-box; }
.steam.s2 { animation-delay: -1.5s; }

.dots { position: absolute; inset: 0; pointer-events: none; overflow: hidden; border-radius: 16px; }
.dots span {
  position: absolute; bottom: -10px; border-radius: 50%; background: #9db4ff; opacity: 0.6;
  animation-name: dotUp; animation-iteration-count: infinite; animation-timing-function: ease-in-out;
}

@keyframes glowPulse { 0%,100% { opacity: 0.85; transform: scale(1); } 50% { opacity: 1; transform: scale(1.05); } }
@keyframes floatY { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
@keyframes bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-2px); } }
@keyframes wig { 0%,100% { transform: rotate(0); } 50% { transform: rotate(-7deg); } }
@keyframes typeL { 0%,100% { transform: translateY(0); } 50% { transform: translateY(3px); } }
@keyframes typeR { 0%,100% { transform: translateY(3px); } 50% { transform: translateY(0); } }
@keyframes barFill { 0% { width: 0; } 70% { width: 96px; } 100% { width: 96px; } }
@keyframes drawC { 0% { stroke-dashoffset: 40; } 60% { stroke-dashoffset: 0; } 100% { stroke-dashoffset: 0; } }
@keyframes rise { 0% { opacity: 0; transform: translateY(0); } 30% { opacity: 0.8; } 100% { opacity: 0; transform: translateY(-18px); } }
@keyframes dotUp {
  0% { transform: translateY(0); opacity: 0; }
  10% { opacity: 0.6; } 90% { opacity: 0.4; }
  100% { transform: translateY(-260px); opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .glow, .monitor, .bug-worker, .coffee, .ant, .hand, .bar-fill, .check2, .check-back, .steam, .dots span { animation: none !important; }
}
</style>
