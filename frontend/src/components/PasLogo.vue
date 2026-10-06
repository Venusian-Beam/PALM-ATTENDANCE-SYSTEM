<script setup>
import crest from '../assets/crest.png'

defineProps({
  size: { type: Number, default: 32 },
  wordmark: { type: Boolean, default: true },
})
</script>

<template>
  <span class="logo">
    <img :src="crest" :width="size" :height="size" alt="Palm University College" />
    <span v-if="wordmark" class="word"><b>PAS</b> Attendance</span>
  </span>
</template>

<style scoped>
.logo {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
img {
  flex: none;
  object-fit: contain;
}
.word {
  font-family: var(--font-display);
  font-size: 22px;
  line-height: 28px;
  color: var(--text-2);
  white-space: nowrap;
}
.word b {
  font-weight: 500;
  color: var(--text);
}
</style>
