// Small fetch wrapper for the admin API. The login token lives in sessionStorage,
// so it is forgotten when the browser tab is closed.

const TOKEN_KEY = 'pas_admin_token'
let unauthorizedHandler = () => {}

export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // storage unavailable (private mode); the user will just need to log in again
  }
}

export function onUnauthorized(handler) {
  unauthorizedHandler = handler
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

function errorMessage(status, data) {
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail)) return 'Please check the form and try again.'
  return `Something went wrong (error ${status}).`
}

export async function api(path, { method = 'GET', body } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let res
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the attendance server. Is it running?')
  }

  let data = null
  try {
    data = await res.json()
  } catch {
    // empty or non-JSON body
  }
  if (!res.ok) {
    if (res.status === 401 && path !== '/api/admin/login') {
      setToken(null)
      unauthorizedHandler()
    }
    throw new ApiError(res.status, errorMessage(res.status, data))
  }
  return data
}

export function isKioskHost() {
  return ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
}

export function todayISO() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
