import { ref } from 'vue'

/**
 * 亮暗主题切换。
 *
 * 判定逻辑只有一处：index.html <head> 里的内联脚本，在首帧之前就把
 * data-theme 写好了（否则暗色用户会先看到一帧白底）。这个模块**只读 DOM**，
 * 不重算一遍「用户到底要哪个主题」——两处各算一次迟早会打架，
 * 而且打架时表现为「刷新后主题跳回去」，不报错、很难查。
 */

const STORAGE_KEY = 'viddigest-theme'
const META = { dark: '#0e141b', light: '#f8f9f9' }

// 模块级单例：页头与页脚等处共用同一个 ref，不会各切各的
const theme = ref('dark')

function readFromDom() {
  if (typeof document === 'undefined') return 'dark'
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

function apply(next) {
  theme.value = next
  document.documentElement.setAttribute('data-theme', next)

  // 隐私模式下 localStorage 会抛异常。主题仍然可切换，只是不记住 ——
  // 所以这里吞掉异常，不让它冒泡成一次「点主题按钮报错」。
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch (e) { /* 不记住，仅本次会话有效 */ }

  // 移动端浏览器的地址栏配色跟着 meta 走，不同步会与页面明显不符
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', META[next])
}

export function useTheme() {
  // 在 setup 期同步一次：内联脚本早已执行完，此刻读 DOM 一定拿得到真值
  if (theme.value === 'dark') theme.value = readFromDom()

  return {
    theme,
    toggleTheme() {
      apply(theme.value === 'dark' ? 'light' : 'dark')
    },
  }
}
