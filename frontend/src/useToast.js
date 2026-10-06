import { ref } from 'vue'

// App-wide toasts (bottom-right), shown by <AppToasts/> in App.vue.
const toasts = ref([])
let nextId = 1

export function toast(message, tone = 'success', ms = 4000) {
  const id = nextId++
  toasts.value.push({ id, message, tone })
  setTimeout(() => dismiss(id), ms)
}

export function dismiss(id) {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

export function useToasts() {
  return toasts
}

// Promise-based confirm dialog, shown by <ConfirmDialog/> in App.vue.
export const confirmState = ref(null)

export function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    confirmState.value = { title, message, confirmLabel, danger, resolve }
  })
}
