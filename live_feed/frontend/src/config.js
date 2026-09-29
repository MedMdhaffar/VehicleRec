const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:5034'
export const API_BASE = BASE_URL
const BASE_WS_URL = BASE_URL.replace(/^http/, 'ws')
const raw = import.meta.env.VITE_CAMERAS

if (!raw) {
  console.error('VITE_CAMERAS is not set. Set it in .env, e.g. VITE_CAMERAS=4,1')
}

export const CAMERAS = (raw || '4').split(',').map(entry => {
  const id = entry.trim()
  return { id, label: `CH${id}` }
})

export const DEFAULT_CAMERA = CAMERAS[0]

// Appends ?cam=<id> (or &cam=<id> if path already contains ?)
export function camUrl(camera, path) {
  const sep = path.includes('?') ? '&' : '?'
  return `${BASE_URL}${path}${sep}cam=${camera.id}`
}

export function wsUrl(camera) {
  return `${BASE_WS_URL}/ws?cam=${camera.id}`
}

export function streamUrl(camera) {
  return `${BASE_URL}/stream.mjpg?cam=${camera.id}`
}
