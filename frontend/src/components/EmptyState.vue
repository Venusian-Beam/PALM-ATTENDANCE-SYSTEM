<script setup>
import AppIcon from './AppIcon.vue'

defineProps({
  icon: { type: String, default: 'info' },
  title: { type: String, required: true },
})
</script>

<template>
  <div class="empty">
    <AppIcon :name="icon" :size="48" class="icon" />
    <p>{{ title }}</p>
    <slot />
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px 24px;
  text-align: center;
  color: var(--text-2);
}
.icon {
  color: var(--text-3);
  opacity: 0.7;
}
p {
  font-family: var(--font-display);
  font-size: 16px;
}
</style>
