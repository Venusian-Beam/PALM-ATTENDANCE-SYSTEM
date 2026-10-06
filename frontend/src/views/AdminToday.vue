<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import PersonAvatar from '../components/PersonAvatar.vue'
import StatCard from '../components/StatCard.vue'
import { api, todayISO } from '../api.js'

const day = ref(todayISO())
const data = ref(null)
const devices = ref(null)
const registered = ref(null)
const error = ref('')
const loading = ref(false)
const search = ref('')
const filter = ref('all')
const now = ref(new Date())
let refreshTimer = null

const isToday = computed(() => day.value === todayISO())

const dayLabel = computed(() =>
  new Date(`${day.value}T12:00:00`).toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }),
)

const rate = computed(() => {
  if (!data.value || !registered.value) return null
  return Math.round((data.value.counts.students / registered.value) * 100)
})

const flagged = (s) => s.flags.length > 0

const filters = computed(() => {
  const list = data.value?.sessions || []
  return [
    { key: 'all', label: 'All', count: list.length },
    { key: 'in', label: isToday.value ? 'In school' : 'Not signed out', count: list.filter((s) => !s.closed).length },
    { key: 'out', label: 'Clocked out', count: list.filter((s) => s.closed).length },
    { key: 'flagged', label: 'Flagged', count: list.filter(flagged).length },
  ]
})

const rows = computed(() => {
  let list = data.value?.sessions || []
  if (filter.value === 'in') list = list.filter((s) => !s.closed)
  else if (filter.value === 'out') list = list.filter((s) => s.closed)
  else if (filter.value === 'flagged') list = list.filter(flagged)
  const term = search.value.trim().toLowerCase()
  if (term) list = list.filter((s) => `${s.full_name} ${s.student_no}`.toLowerCase().includes(term))
  return list
})

// Arrivals per hour for the chart (06:00–18:00).
const hours = computed(() => {
  const buckets = Array.from({ length: 13 }, (_, i) => ({ hour: i + 6, count: 0 }))
  for (const s of data.value?.sessions || []) {
    const h = Number(s.clock_in?.slice(0, 2))
    const b = buckets.find((x) => x.hour === h)
    if (b) b.count++
  }
  return buckets
})
const maxHour = computed(() => Math.max(4, ...hours.value.map((h) => h.count)))
const gridLines = computed(() => {
  const top = maxHour.value
  return [top, Math.round(top / 2), 0]
})
const peak = computed(() => {
  const top = [...hours.value].sort((a, b) => b.count - a.count)[0]
  return top && top.count ? `${String(top.hour).padStart(2, '0')}:00` : null
})

const activeDevices = computed(() => (devices.value || []).filter((d) => !d.removed))
const onlineCount = computed(() => activeDevices.value.filter((d) => d.online).length)

async function load() {
  loading.value = true
  try {
    const [sessions, devs, students] = await Promise.all([
      api(`/api/admin/sessions?date=${day.value}`),
      api('/api/admin/devices').catch(() => null),
      registered.value === null ? api('/api/admin/students').catch(() => null) : null,
    ])
    data.value = sessions
    if (devs) devices.value = devs
    if (students) registered.value = students.length
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function hhmm(t) {
  return t ? t.slice(0, 5) : '—'
}

function duration(s) {
  if (!s.clock_in) return '—'
  const [h1, m1] = s.clock_in.split(':').map(Number)
  let end
  if (s.clock_out) {
    const [h2, m2] = s.clock_out.split(':').map(Number)
    end = h2 * 60 + m2
  } else if (isToday.value) {
    end = now.value.getHours() * 60 + now.value.getMinutes()
  } else return '—'
  const mins = Math.max(0, end - (h1 * 60 + m1))
  return `${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, '0')} min`
}

watch(day, () => {
  filter.value = 'all'
  load()
})
onMounted(() => {
  load()
  refreshTimer = setInterval(() => {
    now.value = new Date()
    if (isToday.value) load()
  }, 15000)
})
onUnmounted(() => clearInterval(refreshTimer))
</script>

<template>
  <div class="page">
    <PageHeader title="Dashboard">
      <span class="muted day">{{ dayLabel }}</span>
      <label class="date-btn" data-tip="Choose a day">
        <AppIcon name="calendar_today" :size="20" />
        <input v-model="day" type="date" :max="todayISO()" aria-label="Day" />
      </label>
      <button v-if="!isToday" class="btn btn-outlined" @click="day = todayISO()">Today</button>
      <button class="icon-btn" data-tip="Refresh" :disabled="loading" @click="load">
        <AppIcon name="refresh" :class="{ spinning: loading }" />
      </button>
    </PageHeader>

    <div v-if="error" class="banner banner-error"><AppIcon name="error" :size="20" />{{ error }}</div>

    <div class="stats">
      <StatCard
        :label="isToday ? 'In school now' : 'Never signed out'"
        :value="data?.counts.in"
        icon="how_to_reg"
        :color="isToday ? 'green' : 'red'"
        :sub="isToday ? 'Updates every 15 seconds' : 'Open sessions on this day'"
      />
      <StatCard label="Clocked out" :value="data?.counts.out" icon="logout" sub="Completed sessions" />
      <StatCard label="Students seen" :value="data?.counts.students" icon="group" color="amber"
                :sub="registered != null ? `of ${registered} registered` : 'Registered count unavailable'" />
      <StatCard label="Attendance rate" :value="rate != null ? `${rate}%` : null" icon="bar_chart" color="green">
        <div class="linear rate"><div :style="{ width: `${Math.min(rate || 0, 100)}%` }" /></div>
      </StatCard>
    </div>

    <div class="mid">
      <section class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">Arrivals by hour</h2>
            <p class="muted small">{{ peak ? `Busiest at ${peak}` : 'No arrivals yet' }}</p>
          </div>
        </div>
        <div class="chart">
          <div class="grid">
            <div v-for="g in gridLines" :key="g" class="grid-line"><span>{{ g }}</span></div>
          </div>
          <div class="bars" role="img" aria-label="Arrivals per hour chart">
            <div v-for="h in hours" :key="h.hour" class="bar-col" :data-tip="`${String(h.hour).padStart(2, '0')}:00 · ${h.count}`">
              <div class="bar" :style="{ height: `${(h.count / maxHour) * 100}%` }" />
            </div>
          </div>
          <div class="labels">
            <span v-for="h in hours" :key="h.hour">{{ h.hour % 2 === 0 ? String(h.hour).padStart(2, '0') : '' }}</span>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <div>
            <h2 class="card-title">Kiosks</h2>
            <p class="muted small">
              {{ devices ? `${onlineCount} of ${activeDevices.length} online` : 'Status unavailable' }}
            </p>
          </div>
          <router-link :to="{ name: 'devices' }" class="btn">Manage</router-link>
        </div>
        <ul v-if="activeDevices.length" class="kiosk-list">
          <li v-for="d in activeDevices" :key="d.id">
            <AppIcon name="computer" :size="22" :class="d.online ? 'on' : 'off'" />
            <span class="k-text">
              <span class="k-name">{{ d.name }}
                <span v-if="d.backlit" class="chip chip-amber backlit" :title="d.backlit">
                  <AppIcon name="light_mode" :size="12" /> Backlit
                </span>
                <span v-if="d.spoof_today" class="chip chip-red backlit"
                      :title="`${d.spoof_today} photo or phone attempts today. See the activity log.`">
                  <AppIcon name="gpp_bad" :size="12" /> {{ d.spoof_today }}
                </span>
              </span>
              <span class="muted small">{{ d.last_ssid || d.mac }}</span>
            </span>
            <span v-if="d.online" class="chip chip-green">Online</span>
            <span v-else class="chip">Offline</span>
          </li>
        </ul>
        <EmptyState v-else icon="computer" title="No kiosks yet" />
      </section>
    </div>

    <section class="card">
      <div class="toolbar">
        <div class="filter-chips" role="tablist">
          <button
            v-for="f in filters"
            :key="f.key"
            class="filter-chip"
            :class="{ on: filter === f.key }"
            role="tab"
            :aria-selected="filter === f.key"
            @click="filter = f.key"
          >
            <AppIcon v-if="filter === f.key" name="check" :size="18" />
            {{ f.label }} · {{ f.count }}
          </button>
        </div>
        <div class="search tb-search">
          <AppIcon name="search" :size="20" />
          <input v-model="search" placeholder="Search students" />
        </div>
      </div>

      <div v-if="!data" class="skeletons">
        <div v-for="i in 4" :key="i" class="skeleton" style="height: 40px" />
      </div>
      <EmptyState v-else-if="!data.sessions.length" icon="calendar_today"
                  :title="isToday ? 'No sign-ins yet today' : 'No sign-ins on this day'" />
      <EmptyState v-else-if="!rows.length" icon="search" title="No matches" />
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Student</th>
              <th>Student ID</th>
              <th>Clock in</th>
              <th>Clock out</th>
              <th>Time in school</th>
              <th>Kiosk</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in rows" :key="s.id">
              <td>
                <div class="person">
                  <PersonAvatar :name="s.full_name" />
                  <span>{{ s.full_name }}</span>
                </div>
              </td>
              <td class="muted">{{ s.student_no }}</td>
              <td>{{ hhmm(s.clock_in) }}</td>
              <td :class="{ muted: !s.clock_out }">{{ hhmm(s.clock_out) }}</td>
              <td class="muted">{{ duration(s) }}</td>
              <td class="muted">
                {{ s.in_device || '—' }}<template v-if="s.out_device && s.out_device !== s.in_device">
                  → {{ s.out_device }}</template>
              </td>
              <td>
                <div class="chips">
                  <span v-if="!s.closed && isToday" class="chip chip-green">In school</span>
                  <span v-else-if="s.closed && !s.flags.length" class="chip">Complete</span>
                  <span
                    v-for="f in s.flags"
                    :key="f"
                    class="chip"
                    :class="f === 'No sign-out' ? 'chip-red' : 'chip-amber'"
                  >{{ f }}</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.day {
  margin-right: 4px;
}
.date-btn {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-2);
  cursor: pointer;
}
.date-btn:hover {
  background: var(--hover);
}
.date-btn input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.date-btn input::-webkit-calendar-picker-indicator {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
}
.spinning {
  animation: spin 0.8s linear infinite;
}
.stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
.rate {
  margin-top: 8px;
}
.rate > div {
  background: var(--green);
}
.mid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 16px;
}
.chart {
  position: relative;
  padding: 20px 24px 16px 48px;
}
.grid {
  position: absolute;
  inset: 20px 24px 40px 48px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.grid-line {
  position: relative;
  border-top: 1px solid var(--divider-soft);
}
.grid-line span {
  position: absolute;
  left: -32px;
  top: -9px;
  width: 24px;
  text-align: right;
  font-size: 12px;
  color: var(--text-2);
}
.bars {
  position: relative;
  height: 200px;
  display: grid;
  grid-template-columns: repeat(13, minmax(0, 1fr));
  gap: 10px;
  align-items: end;
}
.bar-col {
  height: 100%;
  display: flex;
  align-items: flex-end;
}
.bar-col:hover {
  background: var(--hover);
}
.bar {
  width: 100%;
  background: var(--primary);
  border-radius: 2px 2px 0 0;
  transition: height 0.4s var(--ease);
}
.labels {
  display: grid;
  grid-template-columns: repeat(13, minmax(0, 1fr));
  gap: 10px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-2);
  text-align: center;
}
.kiosk-list {
  list-style: none;
  margin: 0;
  padding: 8px 0;
}
.kiosk-list li {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 24px;
}
.kiosk-list li:hover {
  background: var(--hover);
}
.on {
  color: var(--green);
}
.off {
  color: var(--text-3);
}
.k-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.k-name {
  font-weight: 500;
}
.backlit {
  height: 20px;
  margin-left: 6px;
  vertical-align: 1px;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px 12px 24px;
  border-bottom: 1px solid var(--divider);
  flex-wrap: wrap;
}
.tb-search {
  width: 280px;
}
.skeletons {
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
@media (max-width: 1100px) {
  .stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .mid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .stats {
    grid-template-columns: 1fr;
  }
  .day {
    display: none;
  }
  .tb-search {
    width: 100%;
  }
  .bars,
  .labels {
    gap: 4px;
  }
}
</style>
