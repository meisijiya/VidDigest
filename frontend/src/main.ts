import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import { createAppRouter } from './router/index.ts'

/**
 * 装配顺序有讲究：
 *
 * 1. **先装 Pinia**，再装路由。路由守卫（将来要用）与组件 setup 里
 *    都会 `useSessionStore()`，而 Pinia 必须在 app.use() 之后才生效；
 *    放在它前面，守卫第一次跑就会报「没有 active Pinia」。
 * 2. 路由在 App 之前装：`<router-view>` 是 App.vue 模板里的一部分，
 *    路由没装上就会渲染成一段注释。
 */
const app = createApp(App)

app.use(createPinia())
app.use(createAppRouter())

app.mount('#app')
