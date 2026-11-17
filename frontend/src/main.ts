import { createApp, type App as VueApp } from 'vue'
import App from './App.vue'
import { createFuelVRouter } from './router'
import './style.css'

export function mountFuelV(
  container: string | Element = '#app',
  basePath?: string
): VueApp {
  const app = createApp(App)
  const router = createFuelVRouter(basePath)
  app.use(router)
  app.mount(container)
  return app
}

// Auto-mount when running standalone
if (typeof document !== 'undefined' && document.querySelector('#app')) {
  mountFuelV('#app')
}
