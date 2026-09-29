/**
 * Session storage for the bearer token.
 *
 * WHY THIS FILE EXISTS INSTEAD OF A LOGIN FORM
 * --------------------------------------------
 * The backend exposes no `/login`, `/register`, `/refresh` or `/me` endpoint.
 * Access tokens are minted by an operator running
 *   `python -m app.cli.manage_access ...`
 * and presented as `Authorization: Bearer <jwt>`. There is nothing to POST a
 * password to, so this app does not pretend otherwise: the sign-in screen
 * accepts an issued access token and validates it by calling `GET /patients`.
 *
 * SECURITY NOTES
 *  - The token is a bearer credential, so it is kept in `sessionStorage`, not
 *    `localStorage`: it does not survive a tab close, which is the right
 *    default for shared/home devices. Documented as a deliberate trade-off;
 *    the real fix is a backend login endpoint.
 *  - The token is never written to the console, never put in a URL, and never
 *    sent anywhere except the configured API base URL.
 */

const STORAGE_KEY = 'careloop.access_token'
const PATIENT_KEY = 'careloop.active_patient_id'

function safeStorage(): Storage | null {
  try {
    const probe = '__careloop_probe__'
    window.sessionStorage.setItem(probe, '1')
    window.sessionStorage.removeItem(probe)
    return window.sessionStorage
  } catch {
    // Private-mode / storage-disabled browsers: fall back to memory only.
    return null
  }
}

let memoryToken: string | null = null
let memoryPatientId: string | null = null
const storage = typeof window === 'undefined' ? null : safeStorage()

export function readToken(): string | null {
  if (storage) return storage.getItem(STORAGE_KEY)
  return memoryToken
}

export function writeToken(token: string | null): void {
  memoryToken = token
  if (!storage) return
  if (token) storage.setItem(STORAGE_KEY, token)
  else storage.removeItem(STORAGE_KEY)
}

export function readActivePatientId(): string | null {
  if (storage) return storage.getItem(PATIENT_KEY)
  return memoryPatientId
}

export function writeActivePatientId(patientId: string | null): void {
  memoryPatientId = patientId
  if (!storage) return
  if (patientId) storage.setItem(PATIENT_KEY, patientId)
  else storage.removeItem(PATIENT_KEY)
}

/** Clears every credential this app holds. Called on sign-out and on a 401. */
export function clearSession(): void {
  writeToken(null)
  writeActivePatientId(null)
}
