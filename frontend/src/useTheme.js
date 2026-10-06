import { ref } from 'vue'

// Light/dark theme for the admin portal, like Tailorni's toggle: a `dark` class on
// <html>, remembered in localStorage. index.html applies it before first paint.
const KEY = 'pas_theme'
function stored() {
  try {
    return localStorage.getItem(KEY) === 'dark'
  } catch {
    return false
  }
}
const dark = ref(stored())

function apply(value) {
  const root = document.documentElement
  root.classList.add('theme-switching')
  root.classList.toggle('dark', value)
  setTimeout(() => root.classList.remove('theme-switching'), 300)
  dark.value = value
  try {
    localStorage.setItem(KEY, value ? 'dark' : 'light')
  } catch {
    // storage unavailable; the choice just won't be remembered
  }
}

export function useTheme() {
  return { dark, toggle: () => apply(!dark.value) }
}
