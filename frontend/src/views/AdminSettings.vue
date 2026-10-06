<script setup>
import { computed, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import { api, setToken } from '../api.js'
import { toast } from '../useToast.js'

const form = ref(null) // school settings + clock-out time
const bssidText = ref('')
const fingerprint = ref('')
const students = ref(0)
const saving = ref(false)
const saveError = ref('')
const log = ref([])
const pw = ref({ current: '', next: '', confirm: '' })
const pwError = ref('')
const pwBusy = ref(false)
const error = ref('')

const mapUrl = computed(() => {
  const f = form.value
  if (!f || f.lat === '' || f.lat == null || f.lon === '' || f.lon == null) return null
  return `https://www.openstreetmap.org/?mlat=${f.lat}&mlon=${f.lon}#map=17/${f.lat}/${f.lon}`
})

async function load() {
  try {
    const s = await api('/api/admin/settings')
    form.value = {
      clock_out_after: s.clock_out_after,
      wifi_ssid: s.wifi_ssid,
      lat: s.lat,
      lon: s.lon,
      radius_m: s.radius_m,
    }
    bssidText.value = s.wifi_bssids.join('\n')
    fingerprint.value = s.certificate_fingerprint || 'unknown'
    students.value = s.students
  } catch (e) {
    error.value = e.message
  }
}

async function loadLog() {
  try {
    log.value = (await api('/api/admin/log')).slice(0, 50)
  } catch {
    // shown elsewhere
  }
}

async function save() {
  saveError.value = ''
  saving.value = true
  const bssids = bssidText.value.split(/[\s,]+/).filter(Boolean)
  try {
    const s = await api('/api/admin/settings', {
      method: 'PUT',
      body: {
        ...form.value,
        lat: form.value.lat === '' ? null : form.value.lat,
        lon: form.value.lon === '' ? null : form.value.lon,
        wifi_bssids: bssids,
      },
    })
    bssidText.value = s.wifi_bssids.join('\n')
    toast('Settings saved')
    loadLog()
  } catch (e) {
    saveError.value = e.message
  } finally {
    saving.value = false
  }
}

async function changePassword() {
  pwError.value = ''
  if (pw.value.next !== pw.value.confirm) {
    pwError.value = 'The new passwords do not match.'
    return
  }
  pwBusy.value = true
  try {
    const { token } = await api('/api/admin/password', {
      method: 'POST',
      body: { current: pw.value.current, new: pw.value.next },
    })
    setToken(token)
    pw.value = { current: '', next: '', confirm: '' }
    toast('Password changed. Other browsers were signed out.')
    loadLog()
  } catch (e) {
    pwError.value = e.message
  } finally {
    pwBusy.value = false
  }
}

async function copyFingerprint() {
  try {
    await navigator.clipboard.writeText(fingerprint.value)
    toast('Copied to clipboard')
  } catch {
    toast('Could not copy', 'error')
  }
}

function logTime(t) {
  return t.replace('T', ' ').slice(0, 16)
}

onMounted(() => {
  load()
  loadLog()
})
</script>

<template>
  <div class="page">
    <PageHeader title="Settings" />

    <div v-if="error" class="banner banner-error"><AppIcon name="error" :size="20" />{{ error }}</div>

    <form v-if="form" class="card" @submit.prevent="save">
      <div class="card-head"><h2 class="card-title">School rules</h2><span class="muted small">All kiosks</span></div>

      <div class="srow">
        <div class="slabel"><AppIcon name="schedule" :size="22" /> <span>Clock-out time</span></div>
        <div class="svalue">
          <div class="tf w-sm">
            <input id="cutoff" v-model="form.clock_out_after" type="time" required />
            <label for="cutoff">Time</label>
          </div>
          <span class="tf-help">Before: 3 blinks to sign out · After: 1 blink</span>
        </div>
      </div>

      <div class="srow">
        <div class="slabel"><AppIcon name="wifi" :size="22" /> <span>School Wi-Fi</span></div>
        <div class="svalue stack">
          <div class="tf">
            <input id="ssid" v-model="form.wifi_ssid" maxlength="64" placeholder=" " />
            <label for="ssid">Network name</label>
          </div>
          <div>
            <div class="tf">
              <textarea id="bssids" v-model="bssidText" class="mono" rows="3" placeholder="Any" />
              <label for="bssids">Allowed access points (one per line)</label>
            </div>
          </div>
        </div>
      </div>

      <div class="srow">
        <div class="slabel"><AppIcon name="location_on" :size="22" /> <span>School location</span></div>
        <div class="svalue">
          <div class="three">
            <div class="tf">
              <input id="lat" v-model.number="form.lat" type="number" step="any" placeholder=" " />
              <label for="lat">Latitude</label>
            </div>
            <div class="tf">
              <input id="lon" v-model.number="form.lon" type="number" step="any" placeholder=" " />
              <label for="lon">Longitude</label>
            </div>
            <div class="tf">
              <input id="radius" v-model.number="form.radius_m" type="number" min="10" max="5000" placeholder=" " />
              <label for="radius">Radius (m)</label>
            </div>
          </div>
          <a v-if="mapUrl" :href="mapUrl" target="_blank" rel="noopener" class="btn map">
            <AppIcon name="open_in_new" :size="18" /> View on map
          </a>
        </div>
      </div>

      <div class="card-foot">
        <span v-if="saveError" class="err"><AppIcon name="error" :size="18" fill /> {{ saveError }}</span>
        <button class="btn btn-filled" type="submit" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
      </div>
    </form>
    <div v-else class="skeleton" style="height: 360px; border-radius: 8px" />

    <div class="two">
      <section class="card">
        <div class="card-head"><h2 class="card-title">Server</h2></div>
        <div class="srow compact">
          <div class="slabel"><AppIcon name="group" :size="22" /> <span>Students</span></div>
          <div class="svalue big">{{ students }}</div>
        </div>
        <div class="srow compact">
          <div class="slabel"><AppIcon name="verified_user" :size="22" /> <span>Certificate</span></div>
          <div class="svalue fp">
            <code>{{ fingerprint }}</code>
            <button type="button" class="icon-btn" data-tip="Copy" aria-label="Copy fingerprint" @click="copyFingerprint">
              <AppIcon name="content_copy" :size="20" />
            </button>
          </div>
        </div>
      </section>

      <form class="card" @submit.prevent="changePassword">
        <div class="card-head"><h2 class="card-title">Admin password</h2></div>
        <div class="pw-fields">
          <div class="tf">
            <input id="cur" v-model="pw.current" type="password" autocomplete="current-password" placeholder=" " />
            <label for="cur">Current password</label>
          </div>
          <div class="tf">
            <input id="new" v-model="pw.next" type="password" autocomplete="new-password" placeholder=" " />
            <label for="new">New password (8+ characters)</label>
          </div>
          <div class="tf">
            <input id="confirm" v-model="pw.confirm" type="password" autocomplete="new-password" placeholder=" " />
            <label for="confirm">Confirm new password</label>
          </div>
        </div>
        <div class="card-foot">
          <span v-if="pwError" class="err"><AppIcon name="error" :size="18" fill /> {{ pwError }}</span>
          <button class="btn btn-filled" type="submit" :disabled="pwBusy || !pw.current || !pw.next">Change</button>
        </div>
      </form>
    </div>

    <section class="card">
      <div class="card-head"><h2 class="card-title">Activity</h2></div>
      <EmptyState v-if="!log.length" icon="description" title="No activity yet" />
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Computer</th>
              <th>Action</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(e, i) in log" :key="i">
              <td class="muted nowrap">{{ logTime(e.time) }}</td>
              <td class="mono muted">{{ e.ip }}</td>
              <td class="cap">{{ e.action.replaceAll('_', ' ') }}</td>
              <td class="muted">{{ e.detail }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.srow {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 24px;
  padding: 20px 24px;
  border-bottom: 1px solid var(--divider-soft);
}
.srow.compact {
  grid-template-columns: 160px minmax(0, 1fr);
  align-items: center;
  padding: 14px 24px;
}
.srow:last-of-type {
  border-bottom: none;
}
.slabel {
  display: flex;
  align-items: center;
  gap: 16px;
  font-weight: 500;
  align-self: start;
  padding-top: 16px;
}
.compact .slabel {
  padding-top: 0;
  align-self: center;
}
.slabel svg {
  color: var(--text-2);
}
.svalue.stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.w-sm {
  max-width: 200px;
}
.three {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.map {
  margin-top: 8px;
  margin-left: -16px;
}
.big {
  font-family: var(--font-display);
  font-size: 22px;
}
.fp {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fp code {
  flex: 1;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-2);
  word-break: break-all;
}
.card-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  padding: 12px 24px 16px;
  border-top: 1px solid var(--divider);
}
.err {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: auto;
  font-size: 13px;
  color: var(--red-text);
}
.two {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}
.pw-fields {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 24px;
}
.cap {
  text-transform: capitalize;
}
.nowrap {
  white-space: nowrap;
}
@media (max-width: 1000px) {
  .two {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 700px) {
  .srow,
  .srow.compact {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .slabel {
    padding-top: 0;
  }
  .three {
    grid-template-columns: 1fr;
  }
}
</style>
