import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import vueParser from 'vue-eslint-parser'
import tseslint from 'typescript-eslint'
import prettierConfig from 'eslint-config-prettier'
import globals from 'globals'

/**
 * ESLint flat config（ESLint 10）。
 *
 * ## 三层配置与它们各自的职责
 *
 * 1. `js.configs.recommended` —— 通用 JS 规则。
 * 2. `pluginVue.configs['flat/recommended']` —— SFC 规则（模板 + 脚本）。
 * 3. `tseslint.configs.recommended` —— TS 规则（不加类型感知，
 *    免得每次 lint 都要跑一遍 tsc）。
 *
 * 最后一层 `prettierConfig` 关掉所有与格式有关的规则：格式归 Prettier 管，
 * 两边都管就会互相打回（这条冲突是 lint 与 format 一起接进来时最常见的）。
 *
 * ## 为什么 `.vue` 里单独关掉 `no-undef`
 *
 * TS 已经在查未定义变量了（`noUnusedLocals` / `noImplicitAny` 之外的那一类
 * 由 tsc 负责），而 `no-undef` 不认识 Vue SFC 的编译期绑定：
 * `defineProps` / `defineEmits` 声明出来的名字在作用域里并不存在，
 * 于是模板与脚本里正当的名字会被报成未定义。
 *
 * `.mjs` 测试文件同理由各自 runner 提供的全局（node:test 的 `describe` / `it`）
 * 兜底，所以那一段单独放行。
 */
export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // 组件名是文件名派生的单字（App.vue → App），这是 Vue 应用的惯例，
      // 不是命名问题。
      'vue/multi-word-component-names': ['error', { ignores: ['App'] }],
      // 属性顺序是审美，不是缺陷：改它会产生大量与本次接入无关的 diff。
      'vue/attributes-order': 'off',
    },
  },
  {
    files: ['**/*.vue'],
    languageOptions: {
      // **必须**显式指定 vue-eslint-parser：它上面那层的
      // tseslint.configs.recommended 把 TS 解析器设成了全局默认解析器，
      // 而 TS 解析器看不懂 SFC 的 <template>，于是每个 .vue 都报
      // `Parsing error: '>' expected`（2:7）。这里把 SFC 的外层解析器
      // 定成 vue-eslint-parser，内层 <script lang="ts"> 交给 TS 解析器。
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
      },
    },
    rules: {
      'no-undef': 'off',
    },
  },
  {
    files: ['tests/**/*.mjs'],
    languageOptions: {
      globals: {
        ...globals.node,
        ...globals.browser,
        describe: 'readonly',
        it: 'readonly',
        test: 'readonly',
        before: 'readonly',
        after: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
        mock: 'readonly',
      },
    },
    rules: {
      'no-undef': 'off',
    },
  },
  {
    // 排版只归 Prettier 管。eslint-plugin-vue 自带的一批排版规则
    // （属性换行、缩进、引号……）和 Prettier 会互相打回：两个格式化器
    // 抢同一段代码，就是每次 lint 与 format 各改一次、谁也过不了。
    // 这里把它们关掉，格式由 .prettierrc.json + `npm run format` 单独负责。
    rules: {
      'vue/first-attribute-linebreak': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/html-closing-bracket-spacing': 'off',
      'vue/html-indent': 'off',
      'vue/html-quotes': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/mustache-interpolation-spacing': 'off',
      'vue/no-multi-spaces-in-html-tag': 'off',
      'vue/no-spaces-around-equal-sign-in-attribute': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
    },
  },
  {
    // 本仓的 prop 在模板里一律 camelCase（:videoUrl / :initialHistory …）——
    // Vue 两种写法都认，而既有代码全是 camelCase，改成 kebab-case 只会制造
    // 一半一半的噪音。规则关掉，但约定写在这里：新增 prop 跟着现有风格。
    rules: {
      'vue/attribute-hyphenation': 'off',
    },
  },
  prettierConfig,
]
