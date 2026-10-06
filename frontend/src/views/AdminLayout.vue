<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import PasLogo from '../components/PasLogo.vue'
import { api, isKioskHost, setToken } from '../api.js'
import { useTheme } from '../useTheme.js'

const router = useRouter()
const route = useRoute()
const onKiosk = isKioskHost()
const { dark, toggle } = useTheme()

const collapsed = ref(false) // desktop: icon rail
const mobileOpen = ref(false) // mobile: overlay drawer
const menuOpen = ref(false)
const menuEl = ref(null)

const links = [
  { name: 'today', label: 'Dashboard', icon: 'dashboard' },
  { name: 'register', label: 'Register', icon: 'person_add' },
  { name: 'students', label: 'Students', icon: 'group' },
  { name: 'devices', label: 'Devices', icon: 'computer' },
  { name: 'settings', label: 'Settings', icon: 'settings' },
]

function toggleNav() {
  if (window.innerWidth < 840) mobileOpen.value = !mobileOpen.value
  else collapsed.value = !collapsed.value
}

async function logout() {
  menuOpen.value = false
  try {
    await api('/api/admin/logout', { method: 'POST' })
  } catch {
    // already logged out
  }
  setToken(null)
  router.push(onKiosk ? '/' : '/admin/login')
}

function onDocClick(e) {
  if (menuOpen.value && menuEl.value && !menuEl.value.contains(e.target)) menuOpen.value = false
}

watch(() => route.fullPath, () => {
  mobileOpen.value = false
  menuOpen.value = false
})

onMounted(() => {
  collapsed.value = window.innerWidth < 1200
  document.addEventListener('click', onDocClick)
})
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div class="shell" :class="{ collapsed, 'mobile-open': mobileOpen }">
    <header class="appbar">
      <button class="icon-btn" aria-label="Main menu" data-tip="Main menu" @click="toggleNav">
        <AppIcon name="menu" />
      </button>
      <router-link :to="{ name: 'today' }" class="brand"><PasLogo :size="40" /></router-link>

      <div class="appbar-right">
        <button class="icon-btn" :data-tip="dark ? 'Light theme' : 'Dark theme'" @click="toggle">
          <AppIcon :name="dark ? 'light_mode' : 'dark_mode'" />
        </button>
        <div ref="menuEl" class="account">
          <button class="avatar-btn" aria-label="Account" data-tip="IT officer" @click="menuOpen = !menuOpen">
            <span class="avatar">IT</span>
          </button>
          <Transition name="fade">
            <div v-if="menuOpen" class="menu" role="menu">
              <div class="menu-head">
                <span class="avatar big">IT</span>
                <div>
                  <div class="menu-name">IT officer</div>
                  <div class="muted small">Administrator</div>
                </div>
              </div>
              <router-link v-if="onKiosk" to="/" class="menu-item" role="menuitem">
                <AppIcon name="videocam" :size="20" /> Back to kiosk
              </router-link>
              <router-link :to="{ name: 'settings' }" class="menu-item" role="menuitem">
                <AppIcon name="key" :size="20" /> Change password
              </router-link>
              <button class="menu-item" role="menuitem" @click="logout">
                <AppIcon name="logout" :size="20" /> Sign out
              </button>
            </div>
          </Transition>
        </div>
      </div>
    </header>

    <div class="scrim" @click="mobileOpen = false" />

    <nav class="drawer" aria-label="Main">
      <router-link
        v-for="l in links"
        :key="l.name"
        :to="{ name: l.name }"
        class="nav-item"
        active-class="active"
        :data-tip="collapsed ? l.label : null"
      >
        <AppIcon :name="l.icon" :size="24" />
        <span class="nav-label">{{ l.label }}</span>
      </router-link>
    </nav>

    <main class="main">
      <div class="content">
        <router-view v-slot="{ Component }">
          <Transition name="fade" mode="out-in">
            <component :is="Component" />
          </Transition>
        </router-view>
      </div>
    </main>
  </div>
</template>

<style scoped>
.shell {
  --drawer: 256px;
  min-height: 100vh;
  background: var(--bg);
}
.shell.collapsed {
  --drawer: 72px;
}
.appbar {
  position: fixed;
  inset: 0 0 auto 0;
  z-index: 40;
  height: 64px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px 0 12px;
  background: var(--bg);
  border-bottom: 1px solid var(--divider);
}
.brand {
  padding: 4px 8px;
  border-radius: 8px;
  text-decoration: none !important;
}
.appbar-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
.account {
  position: relative;
}
.avatar-btn {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: none;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.avatar-btn:hover {
  background: var(--hover);
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #188038;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font: 500 13px var(--font-display);
}
.avatar.big {
  width: 48px;
  height: 48px;
  font-size: 18px;
}
.menu {
  position: absolute;
  right: 4px;
  top: calc(100% + 4px);
  width: 280px;
  padding: 8px 0;
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--elev-3);
}
:global(.dark) .menu {
  background: var(--surface-2);
}
.menu-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px 16px;
  border-bottom: 1px solid var(--divider);
  margin-bottom: 8px;
}
.menu-name {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 500;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  height: 44px;
  padding: 0 16px;
  border: none;
  background: none;
  color: var(--text);
  font: 400 14px var(--font);
  text-decoration: none !important;
  cursor: pointer;
}
.menu-item svg {
  color: var(--text-2);
}
.menu-item:hover {
  background: var(--hover);
}
.drawer {
  position: fixed;
  top: 64px;
  bottom: 0;
  left: 0;
  z-index: 30;
  width: var(--drawer);
  padding: 8px 12px 8px 0;
  background: var(--bg);
  overflow: hidden;
  transition: width 0.2s var(--ease);
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 18px;
  height: 48px;
  padding: 0 24px;
  margin-bottom: 2px;
  border-radius: 0 24px 24px 0;
  color: var(--text);
  font: 500 14px var(--font-display);
  letter-spacing: 0.25px;
  text-decoration: none !important;
  white-space: nowrap;
}
.nav-item svg {
  color: var(--text-2);
}
.nav-item:hover {
  background: var(--hover);
}
.nav-item.active {
  background: var(--primary-tonal);
  color: var(--primary-on-tonal);
  font-weight: 600;
}
.nav-item.active svg {
  color: var(--primary-on-tonal);
}
.collapsed .nav-item {
  width: 48px;
  padding: 0 12px;
  margin-left: 12px;
  border-radius: 50%;
}
.collapsed .nav-label {
  display: none;
}
.collapsed .nav-item[data-tip]:hover::after {
  left: calc(100% + 8px);
  top: 50%;
  bottom: auto;
  transform: translateY(-50%);
}
.main {
  padding: 64px 0 0 var(--drawer);
  transition: padding 0.2s var(--ease);
}
.content {
  max-width: 1280px;
  padding: 24px 32px 48px;
}
.scrim {
  display: none;
}
@media (max-width: 839px) {
  .shell,
  .shell.collapsed {
    --drawer: 256px;
  }
  .drawer {
    transform: translateX(-100%);
    transition: transform 0.2s var(--ease);
  }
  .mobile-open .drawer {
    transform: none;
    box-shadow: var(--elev-3);
  }
  .collapsed .nav-item {
    width: auto;
    padding: 0 24px;
    margin-left: 0;
    border-radius: 0 24px 24px 0;
  }
  .collapsed .nav-label {
    display: inline;
  }
  .mobile-open .scrim {
    display: block;
    position: fixed;
    inset: 64px 0 0 0;
    z-index: 25;
    background: rgba(32, 33, 36, 0.5);
  }
  .main {
    padding-left: 0;
  }
  .content {
    padding: 16px 16px 40px;
  }
  .brand :deep(.word) {
    font-size: 18px;
  }
}
</style>
