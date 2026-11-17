import { createRouter, createWebHistory, type Router } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import CaptureView from '../views/CaptureView.vue'

export function createFuelVRouter(basePath?: string): Router {
  const resolvedBase = basePath ?? import.meta.env.VITE_BASE_PATH ?? '/'
  return createRouter({
    history: createWebHistory(resolvedBase),
    routes: [
      {
        path: '/',
        name: 'dashboard',
        component: DashboardView,
      },
      {
        path: '/capture',
        name: 'capture',
        component: CaptureView,
      },
    ],
  })
}

const router = createFuelVRouter()
export default router
