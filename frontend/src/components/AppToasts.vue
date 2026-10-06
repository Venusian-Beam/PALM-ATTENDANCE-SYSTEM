<script setup>
import AppIcon from './AppIcon.vue'
import { dismiss, useToasts } from '../useToast.js'

const toasts = useToasts()
</script>

<template>
  <Teleport to="body">
    <TransitionGroup tag="div" name="snack" class="snacks" aria-live="polite">
      <div v-for="t in toasts" :key="t.id" class="snack" :class="t.tone" role="status">
        <span class="msg">{{ t.message }}</span>
        <button class="close" aria-label="Dismiss" @click="dismiss(t.id)">
          <AppIcon name="close" :size="20" />
        </button>
      </div>
    </TransitionGroup>
  </Teleport>
</template>

<style scoped>
.snacks {
  position: fixed;
  left: 24px;
  bottom: 24px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: min(560px, calc(100vw - 48px));
}
.snack {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 6px 8px 6px 16px;
  border-radius: 4px;
  background: #323232;
  color: #f1f3f4;
  box-shadow: var(--elev-3);
  font-size: 14px;
}
.dark .snack,
:global(.dark) .snack {
  background: #e8eaed;
  color: #202124;
}
.msg {
  flex: 1;
}
.snack.error .msg::before {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f28b82;
  margin-right: 10px;
  vertical-align: 1px;
}
.close {
  border: none;
  background: none;
  color: inherit;
  opacity: 0.7;
  cursor: pointer;
  padding: 6px;
  border-radius: 50%;
  display: flex;
}
.close:hover {
  opacity: 1;
  background: rgba(255, 255, 255, 0.1);
}
.snack-enter-active,
.snack-leave-active {
  transition: all 0.2s var(--ease);
}
.snack-enter-from,
.snack-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
