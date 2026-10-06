<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import LockOverlay from '../components/LockOverlay.vue'
import PasLogo from '../components/PasLogo.vue'
import StatusBanner from '../components/StatusBanner.vue'
import YesNo from '../components/YesNo.vue'
import { isKioskHost } from '../api.js'
import { useKioskSocket } from '../useKioskSocket.js'

const router = useRouter()
const onKiosk = isKioskHost()
const { state, connected } = onKiosk ? useKioskSocket() : { state: ref(null), connected: ref(false) }

const view = computed(() => state.value?.view || null)
const tone = computed(() => view.value?.tone || 'idle')
const blocked = computed(() => !!view.value?.blur)
const device = computed(() => state.value?.device || null)
const streamSrc = ref(`/api/stream?t=${Date.now()}`)
const now = ref(new Date())
let clockTimer = null
let streamRetry = null

const timeText = computed(() => now.value.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }))
const dateText = computed(() =>
  now.value.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }),
)

function reloadStream() {
  clearTimeout(streamRetry)
  streamRetry = setTimeout(() => {
    streamSrc.value = `/api/stream?t=${Date.now()}`
  }, 2000)
}

async function post(path, body) {
  try {
    await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    // the socket will show the server is unreachable
  }
}

function answer(value) {
  post('/api/kiosk/answer', { answer: value })
}

function recheck() {
  post('/api/kiosk/recheck')
}

function onKey(event) {
  if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'a') {
    event.preventDefault()
    router.push('/admin')
  } else if (view.value?.question && ['y', 'n'].includes(event.key.toLowerCase())) {
    answer(event.key.toLowerCase() === 'y' ? 'yes' : 'no')
  }
}

onMounted(() => {
  clockTimer = setInterval(() => (now.value = new Date()), 1000)
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  clearInterval(clockTimer)
  clearTimeout(streamRetry)
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <!-- Opened on the server PC rather than a kiosk -->
  <div v-if="!onKiosk" class="server-home">
    <PasLogo :size="96" :wordmark="false" />
    <h1 class="sh-title">PAS Attendance</h1>
    <p class="muted">Students sign in at the kiosks.</p>
    <router-link to="/admin" class="btn btn-filled btn-lg">Open admin</router-link>
  </div>

  <div v-else class="kiosk">
    <header class="top">
      <PasLogo :size="44" />
      <div class="clock">
        <span class="time">{{ timeText }}</span>
        <span class="sep">•</span>
        <span>{{ dateText }}</span>
      </div>
    </header>

    <main class="stage">
      <div class="frame" :class="[`tone-${tone}`, { blocked }]">
        <img :src="streamSrc" alt="Camera" @error="reloadStream" />
        <Transition name="fade">
          <div v-if="blocked" class="blocked-notice" role="alert">
            <AppIcon name="block" :size="56" />
            <span>{{ view.headline }}</span>
          </div>
        </Transition>
      </div>
      <div class="below">
        <YesNo v-if="view?.question" :question="view.question" @answer="answer" />
        <StatusBanner v-else-if="!blocked" :view="view" />
      </div>
    </main>

    <footer class="foot">
      <span class="chip">
        <AppIcon name="computer" :size="16" /> {{ device?.name || 'Kiosk' }}
      </span>
      <span v-if="!connected" class="chip">Starting…</span>
      <span v-else-if="device && !device.online" class="chip chip-amber">
        <AppIcon name="cloud_off" :size="16" /> Offline<template v-if="device.pending"> · {{ device.pending }} waiting</template>
      </span>
    </footer>

    <LockOverlay
      v-if="connected && state?.locked"
      :checking="state.checking"
      :checks="state.checks"
      :macs="device?.macs || []"
      @recheck="recheck"
    />
  </div>
</template>

<style scoped>
.kiosk {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  padding: 16px 24px;
}
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.clock {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-display);
  font-size: 18px;
  color: var(--text-2);
}
.time {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.sep {
  font-size: 10px;
}
.stage {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 28px;
  padding: 16px 0;
}
.frame {
  --ring: transparent;
  flex: 0 1 auto;
  min-height: 0;
  height: min(64vh, calc((100vw - 48px) * 9 / 16));
  aspect-ratio: 16 / 9;
  max-width: 100%;
  border-radius: 8px;
  overflow: hidden;
  background: #202124;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 0 4px var(--ring);
  transition: box-shadow 0.25s var(--ease);
}
.tone-info {
  --ring: var(--primary);
}
.tone-success {
  --ring: var(--green);
}
.tone-warn {
  --ring: var(--amber);
}
.tone-error {
  --ring: var(--red);
}
.frame {
  position: relative;
}
.frame img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  transition: filter 0.2s var(--ease);
}
.frame.blocked img {
  filter: blur(28px) saturate(0.6);
  transform: scale(1.08); /* hide the blurred edge */
}
.frame.blocked {
  box-shadow: 0 0 0 6px var(--red);
}
.blocked-notice {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: rgba(32, 33, 36, 0.45);
  color: #fff;
  font-family: var(--font-display);
  font-size: clamp(24px, 3.4vw, 40px);
  font-weight: 500;
  text-align: center;
  padding: 24px;
}
.blocked-notice .msym {
  color: #f28b82;
}
.below {
  width: min(100%, 900px);
  min-height: 120px;
}
.foot {
  display: flex;
  justify-content: center;
  gap: 8px;
}
.foot .chip {
  height: 32px;
  padding: 0 12px;
  border-radius: 16px;
  font-size: 13px;
}
.server-home {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: 24px;
  text-align: center;
}
.server-home p {
  font-size: 16px;
}
</style>
