import { createRouter, createWebHistory } from 'vue-router'
import { getToken, onUnauthorized } from './api.js'
import KioskView from './views/KioskView.vue'
import AdminLogin from './views/AdminLogin.vue'
import AdminLayout from './views/AdminLayout.vue'
import AdminRegister from './views/AdminRegister.vue'
import AdminStudents from './views/AdminStudents.vue'
import AdminToday from './views/AdminToday.vue'
import AdminSettings from './views/AdminSettings.vue'
import AdminDevices from './views/AdminDevices.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'kiosk', component: KioskView },
    { path: '/admin/login', name: 'login', component: AdminLogin, meta: { title: 'Sign in' } },
    {
      path: '/admin',
      component: AdminLayout,
      meta: { requiresAdmin: true },
      children: [
        { path: '', redirect: { name: 'today' } },
        { path: 'register', name: 'register', component: AdminRegister, meta: { title: 'Register' } },
        { path: 'students', name: 'students', component: AdminStudents, meta: { title: 'Students' } },
        { path: 'today', name: 'today', component: AdminToday, meta: { title: 'Dashboard' } },
        { path: 'devices', name: 'devices', component: AdminDevices, meta: { title: 'Devices' } },
        { path: 'settings', name: 'settings', component: AdminSettings, meta: { title: 'Settings' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (to.matched.some((r) => r.meta.requiresAdmin) && !getToken()) {
    return { name: 'login', query: { next: to.fullPath } }
  }
})

router.afterEach((to) => {
  const title = [...to.matched].reverse().find((r) => r.meta.title)?.meta.title
  document.title = title ? `${title} · PAS Attendance` : 'PAS Attendance'
  // The kiosk screen is always dark; the admin theme only applies under /admin.
  if (!to.path.startsWith('/admin')) document.documentElement.classList.remove('dark')
  else {
    try {
      document.documentElement.classList.toggle('dark', localStorage.getItem('pas_theme') === 'dark')
    } catch {
      // storage unavailable
    }
  }
})

onUnauthorized(() => {
  if (router.currentRoute.value.name !== 'login') {
    router.push({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
  }
})

export default router
