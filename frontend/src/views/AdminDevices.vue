<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api.js'
import { confirmDialog, toast } from '../useToast.js'

const devices = ref(null)
const name = ref('')
const mac = ref('')
const addError = ref('')
const error = ref('')
const adding = ref(false)
const dialogOpen = ref(false)
const nameInput = ref(null)
const showRemoved = ref(false)
let timer = null

const list = computed(() => devices.value || [])
const visible = computed(() => list.value.filter((d) => showRemoved.value || !d.removed))
const activeCount = computed(() => list.value.filter((d) => !d.removed).length)

async function load() {
  try {
    devices.value = await api('/api/admin/devices')
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

async function openDialog() {
  name.value = ''
  mac.value = ''
  addError.value = ''
  dialogOpen.value = true
  await nextTick()
  nameInput.value?.focus()
}

async function add() {
  adding.value = true
  addError.value = ''
  try {
    const d = await api('/api/admin/devices', {
      method: 'POST',
      body: { name: name.value, mac: mac.value },
    })
    dialogOpen.value = false
    toast(`${d.name} added`)
    await load()
  } catch (e) {
    addError.value = e.message
  } finally {
    adding.value = false
  }
}

async function remove(d) {
  const ok = await confirmDialog({
    title: `Remove ${d.name}?`,
    message: 'It stops working immediately. Its past records are kept.',
    confirmLabel: 'Remove',
    danger: true,
  })
  if (!ok) return
  try {
    await api(`/api/admin/devices/${d.id}`, { method: 'DELETE' })
    toast(`${d.name} removed`)
    await load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function allowAccessPoint(d) {
  try {
    await api(`/api/admin/devices/${d.id}/allow-access-point`, { method: 'POST' })
    toast(`Access point ${d.last_bssid} allowed`)
  } catch (e) {
    toast(e.message, 'error')
  }
}

// ---- kiosk tuning (photo / phone detection) -------------------------------------------
const tuneDevice = ref(null)
const tuning = ref(null)
const tuneError = ref('')
const tuneBusy = ref(false)
let tuneTimer = null

// Each tab trains one check: two 30-second steps, then a suggested value to save.
const SECTIONS = {
  phone: {
    label: 'Phones',
    steps: [
      { phase: 'phone', title: 'Phones', hint: 'Hold phones up to your face' },
      { phase: 'clear', title: 'No phone', hint: 'Faces only' },
    ],
    suggestion: 'object_suggestion',
    current: 'object_threshold',
    fallback: 'default_object_threshold',
    endpoint: 'object-threshold',
  },
  photo: {
    label: 'Photos',
    steps: [
      { phase: 'real', title: 'Real faces', hint: 'A few people look at the camera' },
      { phase: 'spoof', title: 'Photos & phones', hint: 'Hold up photos and phone screens of faces' },
    ],
    suggestion: 'suggestion',
    current: 'threshold',
    fallback: 'default_threshold',
    endpoint: 'spoof-threshold',
  },
}
const tuneTab = ref('phone')
const section = computed(() => SECTIONS[tuneTab.value])
const suggestion = computed(() => tuning.value?.[section.value.suggestion] || null)

async function loadTuning() {
  if (!tuneDevice.value) return
  try {
    tuning.value = await api(`/api/admin/devices/${tuneDevice.value.id}/tuning`)
    tuneError.value = ''
  } catch (e) {
    tuneError.value = e.message
  }
}

function openTune(d) {
  tuneDevice.value = d
  tuneTab.value = 'phone'
  tuning.value = null
  tuneError.value = ''
  loadTuning()
  clearInterval(tuneTimer)
  tuneTimer = setInterval(loadTuning, 2000)
}

function closeTune() {
  clearInterval(tuneTimer)
  tuneDevice.value = null
}

async function startStep(phase) {
  tuneBusy.value = true
  try {
    tuning.value = await api(`/api/admin/devices/${tuneDevice.value.id}/tuning`, {
      method: 'POST',
      body: { phase },
    })
  } catch (e) {
    tuneError.value = e.message
  } finally {
    tuneBusy.value = false
  }
}

async function saveThreshold(threshold) {
  tuneBusy.value = true
  try {
    tuning.value = await api(`/api/admin/devices/${tuneDevice.value.id}/${section.value.endpoint}`, {
      method: 'POST',
      body: { threshold },
    })
    toast(threshold == null ? 'Back to the default' : `Saved · ${threshold.toFixed(2)}`)
    await load()
  } catch (e) {
    tuneError.value = e.message
  } finally {
    tuneBusy.value = false
  }
}

function stepState(phase) {
  const t = tuning.value
  if (!t) return { text: '', done: false, running: false }
  if (t.active?.phase === phase) {
    return {
      text: t.active.status === 'running' ? 'Recording…' : 'Waiting for the kiosk…',
      done: false,
      running: true,
    }
  }
  const done = t.steps?.[phase]
  return { text: done ? `Done · ${done.samples} frames` : 'Not done', done: !!done, running: false }
}

const currentValue = computed(() => tuning.value?.[section.value.current] ?? null)
const currentThreshold = computed(() => {
  const t = tuning.value
  if (!t) return ''
  const fallback = t[section.value.fallback]
  return currentValue.value == null ? `${fallback.toFixed(2)} (default)` : currentValue.value.toFixed(2)
})

function when(iso) {
  if (!iso) return 'Never'
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins} min ago`
  return iso.replace('T', ' ').slice(0, 16)
}

function onKey(e) {
  if (e.key === 'Escape') {
    dialogOpen.value = false
    closeTune()
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 5000)
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  clearInterval(timer)
  clearInterval(tuneTimer)
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div class="page">
    <PageHeader title="Devices" :count="devices ? activeCount : null">
      <label class="check"><input v-model="showRemoved" type="checkbox" /> Show removed</label>
      <button class="btn btn-filled" @click="openDialog">
        <AppIcon name="add" :size="20" /> Add device
      </button>
    </PageHeader>

    <div v-if="error" class="banner banner-error"><AppIcon name="error" :size="20" />{{ error }}</div>

    <section class="card">
      <div v-if="!devices" class="skeletons">
        <div v-for="i in 3" :key="i" class="skeleton" style="height: 40px" />
      </div>
      <EmptyState v-else-if="!visible.length" icon="computer" title="No devices yet">
        <button class="btn btn-tonal" @click="openDialog">Add device</button>
      </EmptyState>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>MAC address</th>
              <th>Status</th>
              <th>Last seen</th>
              <th>Network</th>
              <th class="right" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in visible" :key="d.id" :class="{ removed: d.removed }">
              <td>
                <div class="person">
                  <AppIcon name="computer" :size="22" :class="d.online && !d.removed ? 'on' : 'off'" />
                  <span>{{ d.name }}</span>
                  <span v-if="d.mac_conflict" class="chip chip-red" :data-tip="d.mac_conflict">
                    <AppIcon name="warning" :size="14" /> MAC used twice
                  </span>
                  <span v-if="d.backlit && !d.removed" class="chip chip-amber tip-left" :data-tip="d.backlit">
                    <AppIcon name="light_mode" :size="14" /> Backlit
                  </span>
                  <span v-if="d.spoof_today && !d.removed" class="chip chip-red tip-left"
                        data-tip="Photos or phones held up to this kiosk today. Details are in the activity log.">
                    <AppIcon name="gpp_bad" :size="14" /> Spoof attempts · {{ d.spoof_today }}
                  </span>
                </div>
              </td>
              <td class="mono muted">{{ d.mac }}</td>
              <td>
                <span v-if="d.removed" class="chip chip-red">Removed</span>
                <span v-else-if="d.online" class="chip chip-green">Online</span>
                <span v-else class="chip">Offline</span>
                <span v-if="d.pending" class="chip chip-amber pending" data-tip="Records waiting to upload">
                  <AppIcon name="cloud_upload" :size="14" /> {{ d.pending }}
                </span>
              </td>
              <td class="muted">{{ when(d.last_seen) }}</td>
              <td class="muted">
                {{ d.last_ssid || '—' }}<span v-if="d.last_ip" class="small"> · {{ d.last_ip }}</span>
              </td>
              <td>
                <div class="row-actions">
                  <button v-if="d.last_bssid && !d.removed" class="icon-btn" data-tip="Allow its access point"
                          :aria-label="`Allow ${d.name}'s access point`" @click="allowAccessPoint(d)">
                    <AppIcon name="wifi" :size="20" />
                  </button>
                  <button v-if="!d.removed" class="icon-btn" data-tip="Tune photo detection"
                          :aria-label="`Tune ${d.name}`" @click="openTune(d)">
                    <AppIcon name="tune" :size="20" />
                  </button>
                  <button v-if="!d.removed" class="icon-btn danger" data-tip="Remove"
                          :aria-label="`Remove ${d.name}`" @click="remove(d)">
                    <AppIcon name="delete" :size="20" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <Teleport to="body">
      <Transition name="fade">
        <div v-if="dialogOpen" class="scrim" @click.self="dialogOpen = false">
          <form class="dialog" role="dialog" aria-modal="true" aria-label="Add device" @submit.prevent="add">
            <h2>Add device</h2>
            <div class="tf">
              <input id="dname" ref="nameInput" v-model="name" maxlength="60" placeholder="Main gate" />
              <label for="dname">Name</label>
            </div>
            <div class="tf">
              <input id="dmac" v-model="mac" class="mono" maxlength="20" placeholder="00-1A-2B-3C-4D-5E" />
              <label for="dmac">MAC address</label>
            </div>
            <span class="tf-help">Shown by configure_kiosk.py on the kiosk</span>
            <div v-if="addError" class="banner banner-error"><AppIcon name="error" :size="20" />{{ addError }}</div>
            <div class="dialog-actions">
              <button type="button" class="btn" @click="dialogOpen = false">Cancel</button>
              <button type="submit" class="btn" :disabled="adding || !name.trim() || !mac.trim()">Add</button>
            </div>
          </form>
        </div>
      </Transition>
      <Transition name="fade">
        <div v-if="tuneDevice" class="scrim" @click.self="closeTune">
          <div class="dialog tune" role="dialog" aria-modal="true" :aria-label="`Tune ${tuneDevice.name}`">
            <h2>Tune {{ tuneDevice.name }}</h2>
            <div class="filter-chips" role="tablist">
              <button v-for="(sec, key) in SECTIONS" :key="key" type="button" role="tab"
                      class="filter-chip" :class="{ on: tuneTab === key }"
                      :aria-selected="tuneTab === key" @click="tuneTab = key">
                {{ sec.label }}
              </button>
            </div>
            <p class="muted small">Sensitivity · current {{ currentThreshold }}</p>
            <ol class="steps">
              <li v-for="(step, i) in section.steps" :key="step.phase">
                <span class="step-no" :class="{ done: stepState(step.phase).done }">
                  <AppIcon v-if="stepState(step.phase).done" name="check" :size="18" />
                  <template v-else>{{ i + 1 }}</template>
                </span>
                <span class="step-text">
                  <span>{{ step.title }}</span>
                  <span class="muted small">{{ step.hint }}</span>
                  <span class="small" :class="stepState(step.phase).running ? 'live' : 'muted'">
                    {{ stepState(step.phase).text }}
                  </span>
                </span>
                <button class="btn btn-tonal" :disabled="tuneBusy || !!tuning?.active"
                        @click="startStep(step.phase)">
                  {{ stepState(step.phase).done ? 'Redo' : 'Start' }}
                </button>
              </li>
            </ol>
            <div v-if="suggestion?.error" class="banner banner-error">
              <AppIcon name="error" :size="20" />{{ suggestion.error }}
            </div>
            <div v-else-if="suggestion" class="suggestion" :class="`q-${suggestion.quality.toLowerCase()}`">
              <strong>{{ suggestion.threshold.toFixed(2) }}</strong>
              <span class="chip">{{ suggestion.quality }}</span>
              <span v-if="suggestion.phone_caught != null" class="muted small caught">
                {{ Math.round(suggestion.phone_caught * 100) }}% of phone frames caught
              </span>
              <span class="muted small">{{ suggestion.message }}</span>
            </div>
            <div v-if="tuneError" class="banner banner-error"><AppIcon name="error" :size="20" />{{ tuneError }}</div>
            <div class="dialog-actions">
              <button class="btn" :disabled="tuneBusy || currentValue == null" @click="saveThreshold(null)">
                Reset to default
              </button>
              <button class="btn" @click="closeTune">Close</button>
              <button class="btn" :disabled="tuneBusy || !suggestion || !!suggestion.error"
                      @click="saveThreshold(suggestion.threshold)">
                Save
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.skeletons {
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.on {
  color: var(--green);
}
.off {
  color: var(--text-3);
}
.pending {
  margin-left: 6px;
}
.tip-left[data-tip]:hover::after {
  white-space: normal;
  width: 280px;
  left: 0;
  transform: none;
}
.removed {
  opacity: 0.5;
}
.scrim {
  position: fixed;
  inset: 0;
  z-index: 90;
  background: rgba(32, 33, 36, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.dialog {
  width: min(100%, 440px);
  background: var(--surface);
  border-radius: 8px;
  padding: 24px 24px 8px;
  box-shadow: var(--elev-3);
  display: flex;
  flex-direction: column;
  gap: 16px;
}
:global(.dark) .dialog {
  background: var(--surface-2);
}
.dialog h2 {
  font-size: 22px;
  line-height: 28px;
  margin-bottom: 4px;
}
.dialog .tf-help {
  margin-top: -12px;
}
.tune {
  width: min(100%, 520px);
}
.tune > .muted {
  margin-top: -8px;
}
.suggestion .caught {
  flex-basis: auto !important;
}
.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.steps li {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--divider-soft);
}
.steps li:last-child {
  border-bottom: none;
}
.step-no {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  flex: none;
  display: grid;
  place-items: center;
  font-size: 14px;
  font-weight: 500;
  background: var(--surface-2);
  color: var(--text-2);
}
.step-no.done {
  background: var(--green);
  color: #fff;
}
.step-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.live {
  color: var(--primary);
}
.suggestion {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  padding: 12px 16px;
  border-radius: 8px;
  background: var(--surface-2);
}
.suggestion strong {
  font-family: var(--font-display);
  font-size: 22px;
}
.suggestion .small {
  flex-basis: 100%;
}
.q-good .chip {
  color: var(--green);
}
.q-fair .chip {
  color: var(--amber);
}
.q-poor .chip {
  color: var(--red);
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-right: -16px;
  padding: 8px 0;
}
</style>
