<script setup>
import AppIcon from './AppIcon.vue'

defineProps({
  checking: { type: Boolean, default: false },
  checks: { type: Array, default: () => [] },
  macs: { type: Array, default: () => [] },
})
defineEmits(['recheck'])

const labels = { device: 'Approved device', wifi: 'School Wi-Fi', location: 'School location' }
const icons = { device: 'computer', wifi: 'wifi', location: 'location_on' }
</script>

<template>
  <div class="overlay">
    <div v-if="checking" class="box">
      <div class="spinner big" />
      <h1>Checking this kiosk…</h1>
    </div>

    <div v-else class="box">
      <AppIcon name="lock" :size="56" class="lock" />
      <h1>Attendance unavailable</h1>
      <ul class="checks card">
        <li v-for="c in checks" :key="c.name">
          <AppIcon :name="icons[c.name] || 'info'" :size="24" class="c-icon" />
          <span class="c-text">
            <span class="c-label">{{ labels[c.name] || c.name }}</span>
            <span class="muted small">{{ c.detail }}</span>
          </span>
          <AppIcon :name="c.ok ? 'check_circle' : 'cancel'" :size="24" fill :class="c.ok ? 'ok' : 'bad'" />
        </li>
      </ul>
      <div v-if="macs.length && checks.some((c) => c.name === 'device' && !c.ok)" class="macs">
        <span class="muted small">This laptop's MAC addresses</span>
        <div class="mac-list">
          <span v-for="m in macs" :key="m" class="chip mono">{{ m }}</span>
        </div>
      </div>
      <button class="btn btn-filled btn-lg" @click="$emit('recheck')">
        <AppIcon name="refresh" :size="20" /> Check again
      </button>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--bg);
  overflow-y: auto;
}
.box {
  width: min(100%, 520px);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 24px;
}
.spinner.big {
  width: 48px;
  height: 48px;
  border-width: 4px;
}
.lock {
  color: var(--text-2);
}
h1 {
  font-size: 32px;
  line-height: 40px;
}
.checks {
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
  text-align: left;
}
.checks li {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--divider-soft);
}
.checks li:last-child {
  border-bottom: none;
}
.c-icon {
  color: var(--text-2);
}
.c-text {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.c-label {
  font-weight: 500;
  font-size: 16px;
}
.ok {
  color: var(--green);
}
.bad {
  color: var(--red);
}
.macs {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.mac-list {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}
.mac-list .chip {
  height: 32px;
  padding: 0 12px;
  font-size: 14px;
  border-radius: 16px;
}
</style>
