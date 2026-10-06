<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import PageHeader from '../components/PageHeader.vue'
import { api, isKioskHost } from '../api.js'
import { toast } from '../useToast.js'

const route = useRoute()
const router = useRouter()
const onKiosk = isKioskHost()
const streamUrl = '/api/stream'

const fullName = ref('')
const studentNo = ref('')
const deviceId = ref(null)
const devices = ref([])
const devicesLoaded = ref(false)
const thisKiosk = ref(null)
const nameInput = ref(null)
const job = ref(null) // { id, status, message, progress, device_name, full_name }
const error = ref('')
const busy = ref(false)
let pollTimer = null

// Re-capturing an existing student's face: /admin/register?recapture=<id>&name=<name>
const recaptureId = computed(() => (route.query.recapture ? Number(route.query.recapture) : null))
const recaptureName = computed(() => route.query.name || '')
const online = computed(() => devices.value.filter((d) => d.online))
const active = computed(() => job.value && ['waiting', 'capturing'].includes(job.value.status))
const showPreview = computed(
  () => onKiosk && active.value && thisKiosk.value && job.value.device_name === thisKiosk.value,
)
const canStart = computed(
  () => !busy.value && deviceId.value && (recaptureId.value || (fullName.value.trim() && studentNo.value.trim())),
)

async function loadDevices() {
  try {
    devices.value = (await api('/api/admin/devices')).filter((d) => !d.removed)
    if (!online.value.some((d) => d.id === deviceId.value)) {
      const mine = online.value.find((d) => d.name === thisKiosk.value)
      deviceId.value = (mine || online.value[0])?.id ?? null
    }
  } catch (e) {
    error.value = e.message
  } finally {
    devicesLoaded.value = true
  }
}

async function poll() {
  clearTimeout(pollTimer)
  if (!job.value) return
  const before = job.value.status
  try {
    job.value = await api(`/api/admin/register/${job.value.id}`)
  } catch (e) {
    error.value = e.message
  }
  if (active.value) pollTimer = setTimeout(poll, 500)
  else if (before !== job.value.status && job.value.status === 'done') {
    toast(`${job.value.full_name} registered`)
  }
}

async function start() {
  error.value = ''
  busy.value = true
  try {
    job.value = recaptureId.value
      ? await api(`/api/admin/students/${recaptureId.value}/recapture`, {
          method: 'POST',
          body: { device_id: deviceId.value },
        })
      : await api('/api/admin/register', {
          method: 'POST',
          body: { full_name: fullName.value, student_no: studentNo.value, device_id: deviceId.value },
        })
    poll()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function cancel() {
  try {
    await api(`/api/admin/register/${job.value.id}/cancel`, { method: 'POST' })
  } finally {
    poll()
  }
}

async function next() {
  job.value = null
  error.value = ''
  fullName.value = ''
  studentNo.value = ''
  if (recaptureId.value) router.replace({ name: 'register' })
  await nextTick()
  nameInput.value?.focus()
}

onMounted(async () => {
  if (onKiosk) {
    try {
      thisKiosk.value = (await (await fetch('/api/kiosk/info')).json()).device_name
    } catch {
      // not important
    }
  }
  loadDevices()
})
onUnmounted(() => clearTimeout(pollTimer))
</script>

<template>
  <div class="page">
    <PageHeader :title="recaptureId ? `Re-capture face · ${recaptureName}` : 'Register student'">
      <router-link v-if="recaptureId" :to="{ name: 'students' }" class="btn">
        <AppIcon name="arrow_back" :size="18" /> Students
      </router-link>
    </PageHeader>

    <!-- Capturing -->
    <section v-if="active" class="card panel">
      <div class="linear" :class="{ indeterminate: job.progress == null }">
        <div :style="job.progress != null ? { width: `${Math.round(job.progress * 100)}%` } : null" />
      </div>
      <div class="panel-body">
        <div class="cap-head">
          <div>
            <h2>{{ job.full_name }}</h2>
            <p class="muted"><AppIcon name="computer" :size="16" class="inline" /> {{ job.device_name }}</p>
          </div>
          <span class="chip chip-blue">{{ job.status === 'waiting' ? 'Waiting for kiosk' : 'Capturing' }}</span>
        </div>
        <div v-if="showPreview" class="preview"><img :src="streamUrl" alt="Camera preview" /></div>
        <p class="message">{{ job.message }}</p>
        <div class="row-end">
          <button class="btn" @click="cancel">Cancel</button>
        </div>
      </div>
    </section>

    <!-- Finished -->
    <section v-else-if="job" class="card panel">
      <div class="panel-body result">
        <AppIcon :name="job.status === 'done' ? 'check_circle' : 'cancel'" :size="56" fill
                 :class="job.status === 'done' ? 'ok' : 'bad'" />
        <h2>{{ job.status === 'done' ? job.message : 'Registration did not finish' }}</h2>
        <p v-if="job.status !== 'done'" class="muted">{{ job.message }}</p>
        <div class="result-actions">
          <template v-if="job.status === 'done'">
            <router-link :to="{ name: 'students' }" class="btn">View students</router-link>
            <button class="btn btn-filled" @click="next">{{ recaptureId ? 'Done' : 'Register another' }}</button>
          </template>
          <template v-else>
            <button class="btn" @click="next">Cancel</button>
            <button class="btn btn-filled" @click="start">Try again</button>
          </template>
        </div>
      </div>
    </section>

    <!-- Form -->
    <form v-else class="card panel" @submit.prevent="start">
      <div class="panel-body fields">
        <template v-if="!recaptureId">
          <div class="tf">
            <input id="name" ref="nameInput" v-model="fullName" maxlength="100" placeholder=" " autofocus />
            <label for="name">Full name</label>
          </div>
          <div class="tf">
            <input id="sid" v-model="studentNo" maxlength="40" placeholder=" " />
            <label for="sid">Student ID</label>
          </div>
        </template>

        <div v-if="online.length" class="tf">
          <select id="dev" v-model="deviceId" class="select">
            <option v-for="d in online" :key="d.id" :value="d.id">
              {{ d.name }}{{ d.name === thisKiosk ? ' (this kiosk)' : '' }}
            </option>
          </select>
          <label for="dev">Kiosk</label>
          <AppIcon name="keyboard_arrow_down" class="tf-arrow" />
        </div>
        <div v-else-if="devicesLoaded" class="banner banner-warn">
          <AppIcon name="cloud_off" :size="20" />
          <span class="grow">No kiosk online</span>
          <button type="button" class="btn" @click="loadDevices">Retry</button>
        </div>
        <div v-else class="skeleton" style="height: 56px" />

        <div v-if="error" class="banner banner-error"><AppIcon name="error" :size="20" />{{ error }}</div>

        <div class="form-foot">
          <span class="muted small">Student blinks once · 5 photos · no photos stored</span>
          <button class="btn btn-filled" type="submit" :disabled="!canStart">
            <AppIcon name="photo_camera" :size="18" /> Start capture
          </button>
        </div>
      </div>
    </form>
  </div>
</template>

<style scoped>
.panel {
  max-width: 640px;
  overflow: hidden;
}
.panel-body {
  padding: 24px;
}
.fields {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.form-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 4px;
}
.grow {
  flex: 1;
}
.cap-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.cap-head h2 {
  font-size: 22px;
  line-height: 28px;
}
.inline {
  vertical-align: -3px;
}
.preview {
  margin-top: 20px;
  border-radius: 8px;
  overflow: hidden;
  background: #000;
  aspect-ratio: 16 / 10;
  display: flex;
  align-items: center;
  justify-content: center;
}
.preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.message {
  margin-top: 20px;
  font-family: var(--font-display);
  font-size: 18px;
  line-height: 24px;
}
.row-end {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.result {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
  padding: 40px 24px 24px;
}
.result h2 {
  font-size: 22px;
  line-height: 28px;
  margin-top: 8px;
}
.ok {
  color: var(--green);
}
.bad {
  color: var(--red);
}
.result-actions {
  align-self: stretch;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 28px;
}
</style>
