<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import PersonAvatar from '../components/PersonAvatar.vue'
import { api } from '../api.js'
import { confirmDialog, toast } from '../useToast.js'

const router = useRouter()
const students = ref([])
const search = ref('')
const error = ref('')
const loading = ref(true)

const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  if (!term) return students.value
  return students.value.filter((s) => `${s.student_no} ${s.full_name}`.toLowerCase().includes(term))
})

async function load() {
  loading.value = true
  try {
    students.value = await api('/api/admin/students')
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function recapture(student) {
  router.push({ name: 'register', query: { recapture: student.id, name: student.full_name } })
}

async function remove(student) {
  const ok = await confirmDialog({
    title: `Delete ${student.full_name}?`,
    message: 'Their attendance records will be deleted too. This can’t be undone.',
    confirmLabel: 'Delete',
    danger: true,
  })
  if (!ok) return
  try {
    await api(`/api/admin/students/${student.id}`, { method: 'DELETE' })
    toast(`${student.full_name} deleted`)
    await load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

function registered(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="Students" :count="loading ? null : students.length">
      <div class="search hs">
        <AppIcon name="search" :size="20" />
        <input v-model="search" placeholder="Search name or ID" />
      </div>
      <router-link :to="{ name: 'register' }" class="btn btn-filled">
        <AppIcon name="person_add" :size="18" /> Register
      </router-link>
    </PageHeader>

    <div v-if="error" class="banner banner-error"><AppIcon name="error" :size="20" />{{ error }}</div>

    <section class="card">
      <div v-if="loading" class="skeletons">
        <div v-for="i in 5" :key="i" class="skeleton" style="height: 40px" />
      </div>
      <EmptyState v-else-if="!students.length" icon="group" title="No students yet">
        <router-link :to="{ name: 'register' }" class="btn btn-tonal">Register a student</router-link>
      </EmptyState>
      <EmptyState v-else-if="!filtered.length" icon="search" title="No matches" />
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Student ID</th>
              <th>Face samples</th>
              <th>Registered</th>
              <th class="right" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in filtered" :key="s.id">
              <td>
                <div class="person">
                  <PersonAvatar :name="s.full_name" />
                  <span>{{ s.full_name }}</span>
                </div>
              </td>
              <td class="muted">{{ s.student_no }}</td>
              <td>
                <span class="chip" :class="s.samples >= 5 ? 'chip-green' : 'chip-amber'">{{ s.samples }}</span>
              </td>
              <td class="muted">{{ registered(s.created_at) }}</td>
              <td>
                <div class="row-actions">
                  <button class="icon-btn" data-tip="Re-capture face" :aria-label="`Re-capture ${s.full_name}`"
                          @click="recapture(s)">
                    <AppIcon name="photo_camera" :size="20" />
                  </button>
                  <button class="icon-btn danger" data-tip="Delete" :aria-label="`Delete ${s.full_name}`"
                          @click="remove(s)">
                    <AppIcon name="delete" :size="20" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.hs {
  width: 280px;
}
.skeletons {
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
@media (max-width: 600px) {
  .hs {
    width: 100%;
  }
}
</style>
