import { readonly, ref, watch, nextTick } from 'vue'

export const THEME_STORAGE_KEY = 'tp_ui_theme'
export const THEMES = Object.freeze([
  { value: 'fresh', label: '清爽易用' },
  { value: 'tech', label: '深色科技' },
])
const validTheme = value => THEMES.some(item => item.value === value)
const currentTheme = ref('fresh')
let initialized = false

function applyTheme(value) {
  currentTheme.value = validTheme(value) ? value : 'fresh'
  document.documentElement.dataset.uiTheme = currentTheme.value
  document.documentElement.style.colorScheme = currentTheme.value === 'tech' ? 'dark' : 'light'
}

export function initializeTheme() {
  if (initialized) return
  initialized = true
  try { applyTheme(localStorage.getItem(THEME_STORAGE_KEY)) }
  catch { applyTheme('fresh') }
  window.addEventListener('storage', event => {
    if (event.key === THEME_STORAGE_KEY || event.key === null) applyTheme(event.newValue)
  })
}

export function setTheme(value) {
  if (!validTheme(value)) return
  applyTheme(value)
  // Theme selection still works when browser storage is unavailable.
  try { localStorage.setItem(THEME_STORAGE_KEY, value) } catch { /* session only */ }
}

export const theme = readonly(currentTheme)

// Canvas charts need resolved colors; CSS variables alone work for DOM/SVG.
export function themeColor(token) {
  // Track the preference when this helper is used by a template color binding.
  void currentTheme.value
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim()
}

export function useChartTheme(redraw) {
  watch(currentTheme, async () => {
    await nextTick()
    redraw()
  }, { flush: 'post' })
}

export function chartAppearance() {
  return {
    backgroundColor: 'transparent',
    color: ['--tech-chart-blue', '--tech-chart-green', '--tech-chart-purple', '--tech-chart-orange', '--tech-chart-cyan'].map(themeColor),
    textStyle: { color: themeColor('--tech-muted') },
  }
}

export function chartTooltip() {
  return {
    backgroundColor: themeColor('--tech-panel'),
    borderColor: themeColor('--tech-line-strong'),
    textStyle: { color: themeColor('--tech-fg') },
  }
}

export function chartAxis() {
  return {
    axisLabel: { color: themeColor('--tech-muted') },
    nameTextStyle: { color: themeColor('--tech-muted') },
    axisLine: { lineStyle: { color: themeColor('--tech-line-strong') } },
    splitLine: { lineStyle: { color: themeColor('--tech-line') } },
  }
}

export function withChartTheme(options) {
  const themed = { ...chartAppearance(), ...options }
  if (options.tooltip) themed.tooltip = { ...chartTooltip(), ...options.tooltip }
  if (options.legend) themed.legend = { ...options.legend, textStyle: { color: themeColor('--tech-muted'), ...options.legend.textStyle } }
  for (const key of ['xAxis', 'yAxis']) {
    if (!options[key]) continue
    const apply = axis => {
      const appearance = chartAxis()
      return { ...appearance, ...axis,
        axisLabel: { ...appearance.axisLabel, ...axis.axisLabel },
        nameTextStyle: { ...appearance.nameTextStyle, ...axis.nameTextStyle },
        axisLine: { ...appearance.axisLine, ...axis.axisLine },
        splitLine: { ...appearance.splitLine, ...axis.splitLine },
      }
    }
    themed[key] = Array.isArray(options[key]) ? options[key].map(apply) : apply(options[key])
  }
  return themed
}
