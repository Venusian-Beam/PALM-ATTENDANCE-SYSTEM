<script setup>
// Google Material Symbols (outlined, weight 400). Only the icons listed here are
// bundled, so the kiosks need no internet connection.
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 24 },
  fill: { type: Boolean, default: false },
})

const files = import.meta.glob(
  [
    '/node_modules/@material-symbols/svg-400/outlined/{dashboard,person_add,group,computer,settings,light_mode,dark_mode,logout,arrow_back,open_in_new,search,refresh,check,close,add,keyboard_arrow_down,menu,check_circle,cancel,warning,error,info,schedule,delete,verified_user,wifi,location_on,photo_camera,visibility,visibility_off,lock,content_copy,calendar_today,cloud_off,cloud_upload,key,description,bar_chart,how_to_reg,account_circle,edit,videocam,face,login,block,tune,gpp_bad}.svg',
    '/node_modules/@material-symbols/svg-400/outlined/{check_circle,cancel,error,warning,info,account_circle,face,lock,verified_user}-fill.svg',
  ],
  { eager: true, query: '?raw', import: 'default' },
)

const icons = {}
for (const [path, raw] of Object.entries(files)) {
  const key = path.split('/').pop().replace('.svg', '')
  icons[key] = raw.replace(/^<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '')
}

const body = computed(() => {
  const name = props.fill && icons[`${props.name}-fill`] ? `${props.name}-fill` : props.name
  return icons[name] || icons.info
})
</script>

<template>
  <svg
    class="msym"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 -960 960 960"
    fill="currentColor"
    :width="size"
    :height="size"
    aria-hidden="true"
    v-html="body"
  />
</template>

<style scoped>
.msym {
  flex: none;
  display: inline-block;
}
</style>
