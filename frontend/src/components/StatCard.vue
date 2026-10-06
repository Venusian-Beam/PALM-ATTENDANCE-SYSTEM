<script setup>
import AppIcon from './AppIcon.vue'

defineProps({
  label: { type: String, required: true },
  value: { type: [Number, String], default: null },
  icon: { type: String, default: 'bar_chart' },
  color: { type: String, default: 'blue' }, // blue | green | amber | red
  sub: { type: String, default: '' },
})
</script>

<template>
  <div class="stat card">
    <div class="top">
      <span class="label">{{ label }}</span>
      <AppIcon :name="icon" :size="20" class="icon" :class="color" />
    </div>
    <!-- When a number failed to load, show a dash, never 0. -->
    <div class="value">{{ value ?? '—' }}</div>
    <div v-if="sub || $slots.default" class="sub"><slot>{{ sub }}</slot></div>
  </div>
</template>

<style scoped>
.stat {
  padding: 16px 20px 18px;
}
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.label {
  font-size: 14px;
  color: var(--text-2);
  font-weight: 500;
}
.icon.blue {
  color: var(--primary);
}
.icon.green {
  color: var(--green);
}
.icon.amber {
  color: var(--amber);
}
.icon.red {
  color: var(--red);
}
.value {
  font-family: var(--font-display);
  font-size: 36px;
  line-height: 44px;
  margin-top: 8px;
}
.sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-2);
}
</style>
