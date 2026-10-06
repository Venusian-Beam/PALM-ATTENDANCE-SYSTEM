<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  view: { type: Object, default: null },
})

const tone = computed(() => props.view?.tone || 'idle')
const icon = computed(
  () => ({ idle: 'face', info: 'visibility', success: 'check_circle', warn: 'warning', error: 'cancel' })[tone.value] || 'face',
)
</script>

<template>
  <div class="status" :class="tone" aria-live="polite">
    <div class="line">
      <AppIcon :name="icon" :size="36" :fill="tone === 'success' || tone === 'error'" class="icon" />
      <span class="headline">{{ view?.headline || 'Starting camera…' }}</span>
    </div>
    <div v-if="view?.detail" class="detail">{{ view.detail }}</div>

    <div v-if="view?.blinks" class="blinks" aria-label="Blink count"
         :title="`${view.blinks.count} of ${view.blinks.required}`">
      <span v-for="i in view.blinks.required" :key="i" class="blink" :class="{ done: i <= view.blinks.count }" />
    </div>

    <div v-if="view?.progress != null" class="linear bar">
      <div :style="{ width: `${Math.round(view.progress * 100)}%` }" />
    </div>
  </div>
</template>

<style scoped>
.status {
  --tone: var(--text-2);
  text-align: center;
}
.info {
  --tone: var(--primary);
}
.success {
  --tone: var(--green);
}
.warn {
  --tone: var(--amber);
}
.error {
  --tone: var(--red);
}
.line {
  display: inline-flex;
  align-items: center;
  gap: 14px;
}
.icon {
  color: var(--tone);
}
.headline {
  font-family: var(--font-display);
  font-size: clamp(24px, 2.8vw, 36px);
  line-height: 1.25;
  color: var(--text);
}
.detail {
  margin-top: 6px;
  font-size: 18px;
  line-height: 24px;
  color: var(--text-2);
}
.blinks {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 16px;
}
.blink {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--divider);
  transition: all 0.2s var(--ease);
}
.blink.done {
  border-color: var(--primary);
  background: var(--primary);
}
.bar {
  max-width: 360px;
  margin: 16px auto 0;
}
</style>
