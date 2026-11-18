export type GroupByPeriod = 'day' | 'week' | 'month' | 'year'

export interface ExtractedFuelData {
  odometer_reading: number | null
  total_expense: number | null
  fuel_dispensed: number | null
}

export interface ExtractionEfficiency {
  previous_odometer: number | null
  distance_delta: number | null
  km_per_l: number | null
}

export interface ExtractionResponse {
  extraction: ExtractedFuelData
  efficiency?: ExtractionEfficiency | null
  meta: {
    extractor_model: string
    odo_image_path: string | null
    pump_image_path: string | null
    odo_image_url: string | null
    pump_image_url: string | null
    warning: string | null
  }
}

export interface FuelSnapshot {
  id: string
  captured_at: string
  odo_image_path: string | null
  pump_image_path: string | null
  odo_image_url: string | null
  pump_image_url: string | null
  odometer_reading: number
  total_expense: number
  fuel_dispensed: number
  entry_mode: 'vision' | 'manual' | 'vision_edited'
  extractor_model: string | null
  raw_extraction_json: ExtractedFuelData | null
  schema_version: number
  extra_data: Record<string, unknown>
  created_at: string
}

export interface SnapshotPeriodGroup {
  period: string
  snapshot_count: number
  total_expense: number
  total_fuel_dispensed: number
  min_odometer: number
  max_odometer: number
  snapshots: FuelSnapshot[]
}

export interface GroupedSnapshotsResponse {
  group_by: GroupByPeriod
  total_snapshots: number
  total_logs?: number
  overall_expense: number
  overall_fuel_dispensed: number
  overall_distance?: number
  groups: SnapshotPeriodGroup[]
}

export type FuelLog = FuelSnapshot

export interface CreateSnapshotPayload {
  captured_at?: string
  odometer_reading: number
  total_expense: number
  fuel_dispensed: number
  odo_image_path?: string | null
  pump_image_path?: string | null
  entry_mode?: 'vision' | 'manual' | 'vision_edited'
  extractor_model?: string | null
  raw_extraction_json?: ExtractedFuelData | null
  extra_data?: Record<string, unknown>
}

export interface HealthStatus {
  status: string
  extractor_backend: string
  extractor_model: string
  gemini_key_configured: boolean
  write_protection_enabled?: boolean
}

const STORAGE_WRITE_KEY = 'fuelv_write_key'

export function getStoredWriteKey(): string {
  if (typeof window === 'undefined') return ''
  return window.localStorage.getItem(STORAGE_WRITE_KEY) || ''
}

export function setStoredWriteKey(key: string): void {
  if (typeof window === 'undefined') return
  const trimmed = key.trim()
  if (trimmed) {
    window.localStorage.setItem(STORAGE_WRITE_KEY, trimmed)
  } else {
    window.localStorage.removeItem(STORAGE_WRITE_KEY)
  }
}

function getWriteHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const key = getStoredWriteKey()
  const headers: Record<string, string> = { ...extra }
  if (key) {
    headers['X-FuelV-Key'] = key
  }
  return headers
}

const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ||
  import.meta.env.BASE_URL ||
  ''
).replace(/\/$/, '')

export function resolveApiUrl(path: string | null): string | null {
  if (!path) return null
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  return `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API_BASE}/api/health`)
  if (!res.ok) {
    throw new Error(`Health check failed (${res.status})`)
  }
  return res.json()
}

export async function extractFromImages(
  odoFile: File | null,
  pumpFile: File | null
): Promise<ExtractionResponse> {
  const formData = new FormData()
  if (odoFile) formData.append('odo_image', odoFile)
  if (pumpFile) formData.append('pump_image', pumpFile)

  const headers = getWriteHeaders()
  const res = await fetch(`${API_BASE}/api/extract`, {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}))
    const err = new Error(errBody.detail || `Extraction failed (${res.status})`)
    if (res.status === 401) {
      ;(err as any).isAuthError = true
    }
    throw err
  }
  return res.json()
}

export async function createSnapshot(payload: CreateSnapshotPayload): Promise<FuelSnapshot> {
  const headers = getWriteHeaders({ 'Content-Type': 'application/json' })
  const res = await fetch(`${API_BASE}/api/snapshots`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}))
    const err = new Error(errBody.detail || `Failed to save snapshot (${res.status})`)
    if (res.status === 401) {
      ;(err as any).isAuthError = true
    }
    throw err
  }
  return res.json()
}

export async function fetchGroupedSnapshots(
  by: GroupByPeriod = 'month'
): Promise<GroupedSnapshotsResponse> {
  const res = await fetch(`${API_BASE}/api/snapshots/grouped?by=${encodeURIComponent(by)}`)
  if (!res.ok) {
    throw new Error(`Failed to fetch snapshots (${res.status})`)
  }
  return res.json()
}

export async function deleteSnapshot(id: string): Promise<void> {
  const headers = getWriteHeaders()
  const res = await fetch(`${API_BASE}/api/snapshots/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers,
  })
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}))
    const err = new Error(errBody.detail || `Failed to delete snapshot (${res.status})`)
    if (res.status === 401) {
      ;(err as any).isAuthError = true
    }
    throw err
  }
}

export interface UpdateSnapshotPayload {
  captured_at?: string
  odometer_reading?: number
  total_expense?: number
  fuel_dispensed?: number
  entry_mode?: 'vision' | 'manual' | 'vision_edited'
  extra_data?: Record<string, unknown>
}

export async function updateSnapshot(
  id: string,
  payload: UpdateSnapshotPayload
): Promise<FuelSnapshot> {
  const headers = getWriteHeaders({ 'Content-Type': 'application/json' })
  const res = await fetch(`${API_BASE}/api/snapshots/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(payload),
  })

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}))
    const err = new Error(errBody.detail || `Failed to update snapshot (${res.status})`)
    if (res.status === 401) {
      ;(err as any).isAuthError = true
    }
    throw err
  }
  return res.json()
}

export async function fetchLatestSnapshot(): Promise<FuelSnapshot | null> {
  const res = await fetch(`${API_BASE}/api/snapshots/latest`)
  if (!res.ok) return null
  const data = await res.json()
  return data.latest || null
}
