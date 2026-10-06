<script setup>
defineProps({
  title: { type: String, required: true },
  count: { type: [Number, String], default: null },
})
</script>

<template>
  <header class="page-header">
    <h1>{{ title }}<span v-if="count != null" class="count">{{ count }}</span></h1>
    <div v-if="$slots.default" class="actions"><slot /></div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  min-height: 40px;
}
h1 {
  font-size: 22px;
  line-height: 28px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.count {
  font-size: 14px;
  color: var(--text-2);
  font-family: var(--font);
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
