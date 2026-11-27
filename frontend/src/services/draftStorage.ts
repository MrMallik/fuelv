import type { ExtractedFuelData } from './api'

const DB_NAME = 'fuelv_client_db'
const DB_VERSION = 1
const STORE_NAME = 'capture_drafts'
const DRAFT_KEY = 'active_draft'

export interface CaptureDraftRecord {
  id: string
  updatedAt: string
  step: 'capture' | 'confirm'
  odoBlob: Blob | null
  odoName: string | null
  odoType: string | null
  pumpBlob: Blob | null
  pumpName: string | null
  pumpType: string | null
  odometerReading: string
  totalExpense: string
  fuelDispensed: string
  capturedAt: string
  extractorModel: string | null
  rawExtractionJson: ExtractedFuelData | null
  extractionWarning: string | null
  vehicleTag: string
  stationName: string
  notes: string
  showExtra: boolean
}

let memoryFallbackDraft: CaptureDraftRecord | null = null
let dbOpQueue: Promise<void> = Promise.resolve()

function enqueueDbOp<T>(op: () => Promise<T>): Promise<T> {
  const result = dbOpQueue.then(op)
  dbOpQueue = result.then(
    () => undefined,
    () => undefined
  )
  return result
}

function sanitizeDraftForStorage(record: CaptureDraftRecord): CaptureDraftRecord {
  const safeRawExtraction = record.rawExtractionJson
    ? (JSON.parse(JSON.stringify(record.rawExtractionJson)) as ExtractedFuelData)
    : null
  return {
    ...record,
    rawExtractionJson: safeRawExtraction,
  }
}

function createDefaultDraft(): CaptureDraftRecord {
  return {
    id: DRAFT_KEY,
    updatedAt: new Date().toISOString(),
    step: 'capture',
    odoBlob: null,
    odoName: null,
    odoType: null,
    pumpBlob: null,
    pumpName: null,
    pumpType: null,
    odometerReading: '',
    totalExpense: '',
    fuelDispensed: '',
    capturedAt: '',
    extractorModel: null,
    rawExtractionJson: null,
    extractionWarning: null,
    vehicleTag: 'My Bike',
    stationName: '',
    notes: '',
    showExtra: false,
  }
}

function openDraftDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof window === 'undefined' || !window.indexedDB) {
      reject(new Error('IndexedDB not available'))
      return
    }
    const req = window.indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => {
      const db = req.result
      db.onversionchange = () => {
        db.close()
      }
      resolve(db)
    }
    req.onblocked = () => reject(new Error('IndexedDB open blocked by another tab'))
    req.onerror = () => reject(req.error || new Error('Failed to open IndexedDB'))
  })
}

async function readDraftUnlocked(): Promise<CaptureDraftRecord | null> {
  try {
    const db = await openDraftDb()
    return await new Promise<CaptureDraftRecord | null>((resolve, reject) => {
      let settled = false
      const safeClose = () => {
        try {
          db.close()
        } catch {
          // Ignore close errors
        }
      }
      try {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const store = tx.objectStore(STORE_NAME)
        const req = store.get(DRAFT_KEY)
        let loaded: CaptureDraftRecord | null = null
        req.onsuccess = () => {
          loaded = (req.result as CaptureDraftRecord) || null
        }
        req.onerror = () => {
          if (!settled) {
            settled = true
            safeClose()
            reject(req.error || new Error('IndexedDB read failed'))
          }
        }
        tx.oncomplete = () => {
          if (!settled) {
            settled = true
            safeClose()
            resolve(loaded)
          }
        }
        tx.onerror = () => {
          if (!settled) {
            settled = true
            safeClose()
            reject(tx.error || new Error('IndexedDB read transaction failed'))
          }
        }
        tx.onabort = () => {
          if (!settled) {
            settled = true
            safeClose()
            reject(tx.error || new Error('IndexedDB read transaction aborted'))
          }
        }
      } catch (err) {
        safeClose()
        reject(err)
      }
    })
  } catch {
    return memoryFallbackDraft
  }
}

export function loadDraftState(): Promise<CaptureDraftRecord | null> {
  return enqueueDbOp(() => readDraftUnlocked())
}

export function saveDraftState(partial: Partial<CaptureDraftRecord>): Promise<void> {
  return enqueueDbOp(async () => {
    const existing = (await readDraftUnlocked()) || createDefaultDraft()
    const merged = sanitizeDraftForStorage({
      ...existing,
      ...partial,
      id: DRAFT_KEY,
      updatedAt: new Date().toISOString(),
    })
    memoryFallbackDraft = merged

    try {
      const db = await openDraftDb()
      await new Promise<void>((resolve, reject) => {
        let settled = false
        const safeClose = () => {
          try {
            db.close()
          } catch {
            // Ignore close errors
          }
        }
        try {
          const tx = db.transaction(STORE_NAME, 'readwrite')
          const store = tx.objectStore(STORE_NAME)
          store.put(merged)
          tx.oncomplete = () => {
            if (!settled) {
              settled = true
              safeClose()
              resolve()
            }
          }
          tx.onerror = () => {
            if (!settled) {
              settled = true
              safeClose()
              reject(tx.error || new Error('IndexedDB write transaction failed'))
            }
          }
          tx.onabort = () => {
            if (!settled) {
              settled = true
              safeClose()
              reject(tx.error || new Error('IndexedDB write transaction aborted'))
            }
          }
        } catch (err) {
          safeClose()
          reject(err)
        }
      })
    } catch {
      // Kept in memoryFallbackDraft if IndexedDB is restricted or quota-limited
    }
  })
}

export function clearDraftState(): Promise<void> {
  return enqueueDbOp(async () => {
    memoryFallbackDraft = null
    try {
      const db = await openDraftDb()
      await new Promise<void>((resolve, reject) => {
        let settled = false
        const safeClose = () => {
          try {
            db.close()
          } catch {
            // Ignore close errors
          }
        }
        try {
          const tx = db.transaction(STORE_NAME, 'readwrite')
          const store = tx.objectStore(STORE_NAME)
          store.delete(DRAFT_KEY)
          tx.oncomplete = () => {
            if (!settled) {
              settled = true
              safeClose()
              resolve()
            }
          }
          tx.onerror = () => {
            if (!settled) {
              settled = true
              safeClose()
              reject(tx.error || new Error('IndexedDB delete transaction failed'))
            }
          }
          tx.onabort = () => {
            if (!settled) {
              settled = true
              safeClose()
              reject(tx.error || new Error('IndexedDB delete transaction aborted'))
            }
          }
        } catch (err) {
          safeClose()
          reject(err)
        }
      })
    } catch {
      // Ignore cleanup errors
    }
  })
}

export async function getActiveDraftSummary(): Promise<{
  hasDraft: boolean
  photoCount: number
  hasNumbers: boolean
  updatedAt: string | null
}> {
  const draft = await loadDraftState()
  if (!draft) {
    return { hasDraft: false, photoCount: 0, hasNumbers: false, updatedAt: null }
  }
  const photoCount = (draft.odoBlob ? 1 : 0) + (draft.pumpBlob ? 1 : 0)
  const hasNumbers = Boolean(
    draft.odometerReading.trim() || draft.totalExpense.trim() || draft.fuelDispensed.trim()
  )
  const hasDraft = photoCount > 0 || hasNumbers
  return {
    hasDraft,
    photoCount,
    hasNumbers,
    updatedAt: hasDraft ? draft.updatedAt : null,
  }
}

export function blobToFile(blob: Blob, filename: string, mimeType?: string | null): File {
  if (blob instanceof File && blob.name === filename) {
    return blob
  }
  return new File([blob], filename, {
    type: mimeType || blob.type || 'image/jpeg',
    lastModified: Date.now(),
  })
}
