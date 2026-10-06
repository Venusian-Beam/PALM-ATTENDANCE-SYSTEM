<script setup>
import AppToasts from './components/AppToasts.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
</script>

<template>
  <router-view />
  <AppToasts />
  <ConfirmDialog />
</template>
