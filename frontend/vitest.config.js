import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

/**
 * 挂载层测试（工单 #18）。
 *
 * ## 为什么是第二套 runner，而不是把 node --test 换掉
 *
 * `tests/*.test.mjs` 里有相当一部分是**该留的文本断言**——「源码里不许出现
 * 任何色值字面量」「模板里不出现 snake_case 取值」这类判据的**对象就是源码文本**，
 * 迁成挂载断言反而变弱（挂载后看不到模板源码了，断言会退化成「界面上没出现
 * 那个字符串」，而模板里写了、只是没渲染到，恰恰是真正要抓的回归）。
 *
 * 所以两条判据并存、各管一段：
 *   · `*.test.mjs` 走 `node --test`（npm test）——静态 / 契约判据，继续留着；
 *   · `*.spec.mjs` 走 `vitest run`（npm run test:mount）——真挂载，验运行时行为。
 *
 * ## include 必须显式写死
 *
 * vitest 的默认 include 覆盖 `.test` 与 `.spec` 两种后缀，会**顺手吃掉**那些
 * node:test 的 `*.test.mjs`。两者不兼容：node:test 的 `test()` / `assert`
 * 导入会被 vitest 当成普通导入，而 `describe` 体抛异常时 vitest 仍然退出 0
 * （AGENTS.md 那条「describe 体隐形失败」在这里会重演一次）。
 * 所以这里只收 `.spec.mjs`。
 *
 * （注：本注释刻意不写出默认 glob 的字面形态——它以「双星号 + 斜杠」开头，
 * 那是块注释的结束符，写在这里会把这段注释自己从中间截断，
 * 解析器报的错还落在另一个文件上。）
 *
 * ## 不挂 tailwind 插件
 *
 * 组件里没有 `<style>` 块，也不 import css——视觉相关的判据全在文本断言那边。
 * 少一个插件，测试启动快一截；真要断言样式，那是另一条判据，不该混进这里。
 */
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.spec.mjs'],
    // 组件挂载 + api 层 mock 的组合比纯函数测试慢一截，10s 足够一次真实
    // 请求往返的等待（挂载层的假响应是同步的，这条只是别把真卡死误判成失败）。
    testTimeout: 10000,
  },
})
