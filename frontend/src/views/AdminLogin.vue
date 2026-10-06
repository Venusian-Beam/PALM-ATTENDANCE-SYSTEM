<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import PasLogo from '../components/PasLogo.vue'
import { api, setToken } from '../api.js'

const router = useRouter()
const route = useRoute()
const password = ref('')
const show = ref(false)
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    const { token } = await api('/api/admin/login', {
      method: 'POST',
      body: { password: password.value },
    })
    setToken(token)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/admin')
    router.replace(next ? route.query.next : '/admin')
  } catch (e) {
    error.value = e.message
    password.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="wrap">
    <form class="signin" @submit.prevent="submit">
      <div v-if="busy" class="linear indeterminate top-bar"><div /></div>
      <PasLogo :size="80" :wordmark="false" />
      <h1>Sign in</h1>
      <p class="muted">PAS Attendance admin</p>

      <div class="tf pw" :class="{ invalid: error }">
        <input
          id="pw"
          v-model="password"
          :type="show ? 'text' : 'password'"
          autocomplete="current-password"
          placeholder=" "
          autofocus
        />
        <label for="pw">Password</label>
        <span class="tf-trail">
          <button type="button" class="icon-btn" :aria-label="show ? 'Hide password' : 'Show password'"
                  @click="show = !show">
            <AppIcon :name="show ? 'visibility_off' : 'visibility'" :size="22" />
          </button>
        </span>
      </div>
      <p v-if="error" class="err"><AppIcon name="error" :size="16" fill /> {{ error }}</p>

      <div class="actions">
        <router-link to="/" class="btn">Back to kiosk</router-link>
        <button class="btn btn-filled" type="submit" :disabled="busy || !password">Next</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--surface-2);
}
:global(.dark) .wrap {
  background: var(--bg);
}
.signin {
  position: relative;
  overflow: hidden;
  width: min(100%, 448px);
  padding: 48px 40px 36px;
  border: 1px solid var(--divider);
  border-radius: 8px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.top-bar {
  position: absolute;
  inset: 0 0 auto 0;
  border-radius: 0;
}
h1 {
  font-size: 24px;
  line-height: 32px;
  margin-top: 16px;
}
.signin > .muted {
  margin-top: 8px;
  font-size: 16px;
  margin-bottom: 32px;
}
.pw {
  width: 100%;
  text-align: left;
}
.pw input {
  padding-right: 52px;
}
.invalid input {
  border-color: var(--red) !important;
  box-shadow: inset 0 0 0 1px var(--red) !important;
}
.err {
  align-self: stretch;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--red-text);
  text-align: left;
}
.actions {
  align-self: stretch;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 40px;
}
.actions .btn:first-child {
  margin-left: -16px;
}
@media (max-width: 480px) {
  .wrap {
    background: var(--surface);
    padding: 0;
    align-items: flex-start;
  }
  .signin {
    border: none;
    padding: 48px 24px;
  }
}
</style>
