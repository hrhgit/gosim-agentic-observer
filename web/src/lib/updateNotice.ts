import { ref } from 'vue'

/** Set when a deploy has replaced the running build's chunks or index.html underneath an open tab. */
export const updateAvailable = ref(false)

export function markUpdateAvailable() {
  updateAvailable.value = true
}

export function reloadForUpdate() {
  location.reload()
}
