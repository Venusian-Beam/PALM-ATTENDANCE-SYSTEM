import { onMounted, onUnmounted, ref } from 'vue'

// Live kiosk state pushed by the backend. Reconnects automatically.
export function useKioskSocket() {
  const state = ref(null)
  const connected = ref(false)
  let socket = null
  let retry = null
  let stopped = false

  function connect() {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${proto}://${window.location.host}/api/ws`)
    socket.onopen = () => {
      connected.value = true
    }
    socket.onmessage = (event) => {
      state.value = JSON.parse(event.data)
    }
    socket.onclose = () => {
      connected.value = false
      if (!stopped) retry = setTimeout(connect, 1500)
    }
    socket.onerror = () => socket.close()
  }

  onMounted(connect)
  onUnmounted(() => {
    stopped = true
    clearTimeout(retry)
    socket?.close()
  })

  return { state, connected }
}
