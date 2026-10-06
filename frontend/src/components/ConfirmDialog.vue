<script setup>
import { onMounted, onUnmounted } from 'vue'
import { confirmState } from '../useToast.js'

function close(result) {
  confirmState.value?.resolve(result)
  confirmState.value = null
}

function onKey(e) {
  if (e.key === 'Escape' && confirmState.value) close(false)
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="confirmState" class="scrim" @click.self="close(false)">
        <div class="dialog" role="alertdialog" aria-modal="true" :aria-label="confirmState.title">
          <h2>{{ confirmState.title }}</h2>
          <p v-if="confirmState.message">{{ confirmState.message }}</p>
          <div class="actions">
            <button class="btn" @click="close(false)">Cancel</button>
            <button class="btn" :class="{ 'btn-danger': confirmState.danger }" autofocus @click="close(true)">
              {{ confirmState.confirmLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
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
  width: min(100%, 400px);
  background: var(--surface);
  border-radius: 8px;
  padding: 24px 24px 8px;
  box-shadow: var(--elev-3);
}
:global(.dark) .dialog {
  background: var(--surface-2);
}
h2 {
  font-size: 22px;
  line-height: 28px;
  margin-bottom: 12px;
}
p {
  color: var(--text-2);
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: 24px -16px 0 0;
  padding-bottom: 8px;
}
</style>
