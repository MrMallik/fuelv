<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import CameraModal from '../components/CameraModal.vue'
import KeySettingsModal from '../components/KeySettingsModal.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiInput from '../components/ui/UiInput.vue'
import {
  createSnapshot,
  extractFromImages,
  fetchLatestSnapshot,
  type ExtractedFuelData,
} from '../services/api'
import {
  blobToFile,
  clearDraftState,
  loadDraftState,
  saveDraftState,
} from '../services/draftStorage'
import { optimizeSnapshotImage } from '../services/imageOptimizer'

const router = useRouter()

// Image state
const odoFile = ref<File | null>(null)
const odoPreview = ref<string | null>(null)
const odoOptimizing = ref(false)

const pumpFile = ref<File | null>(null)
const pumpPreview = ref<string | null>(null)
const pumpOptimizing = ref(false)

// Lightbox preview for inspecting digits on Step 1 or Step 2
const previewLightbox = ref<{ url: string; title: string } | null>(null)

// Camera modal state ('odo' | 'pump' | null)
const activeCameraTarget = ref<'odo' | 'pump' | null>(null)
const showKeyModal = ref(false)

// Draft persistence state
const isHydratingDraft = ref(true)
const draftRestored = ref(false)
const lastSavedLocallyAt = ref<string | null>(null)
let isUnmounted = false
let odoGeneration = 0
let pumpGeneration = 0

// Extraction state
const extracting = ref(false)
const extractionWarning = ref<string | null>(null)
const extractorModel = ref<string | null>(null)
const rawExtractionJson = ref<ExtractedFuelData | null>(null)

// Previous odometer reading for telemetry efficiency
const previousOdometer = ref<number | null>(null)

// Form state
const step = ref<'capture' | 'confirm'>('capture')
const odometerReading = ref<string>('')
const totalExpense = ref<string>('')
const fuelDispensed = ref<string>('')
const capturedAt = ref<string>(toLocalDatetimeInput(new Date()))

// Optional extensible extra_data fields
const showExtra = ref(false)
const vehicleTag = ref<string>('My Bike')
const stationName = ref<string>('')
const notes = ref<string>('')

const saving = ref(false)
const saveError = ref<string | null>(null)

function toLocalDatetimeInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function revokeObjectUrl(url: string | null) {
  if (url && url.startsWith('blob:')) {
    try {
      URL.revokeObjectURL(url)
    } catch {
      // Ignore revoke errors
    }
  }
}

// Real-time computed fuel efficiency (km/L)
const liveEfficiency = computed(() => {
  const odo = parseFloat(odometerReading.value)
  const fuel = parseFloat(fuelDispensed.value)
  if (isNaN(odo) || isNaN(fuel) || fuel <= 0) return null

  if (previousOdometer.value === null) {
    return {
      isFirstLog: true,
      isInvalidOdo: false,
      distanceDelta: 0,
      kmPerL: null,
      previousOdometer: null,
    }
  }

  if (odo < previousOdometer.value) {
    return {
      isFirstLog: false,
      isInvalidOdo: true,
      distanceDelta: Math.round((odo - previousOdometer.value) * 10) / 10,
      kmPerL: null,
      previousOdometer: previousOdometer.value,
    }
  }

  const delta = Math.round((odo - previousOdometer.value) * 10) / 10
  const kmpl = Math.round((delta / fuel) * 100) / 100
  return {
    isFirstLog: false,
    isInvalidOdo: false,
    distanceDelta: delta,
    kmPerL: kmpl,
    previousOdometer: previousOdometer.value,
  }
})

const hasAnyImage = computed(() => Boolean(odoFile.value || pumpFile.value))

const hasActiveLocalDraft = computed(() =>
  Boolean(
    odoFile.value ||
      pumpFile.value ||
      odometerReading.value.trim() ||
      totalExpense.value.trim() ||
      fuelDispensed.value.trim()
  )
)

async function persistFormFieldsToDraft() {
  if (isHydratingDraft.value || isUnmounted) return
  const rawExtraction = rawExtractionJson.value ? { ...toRaw(rawExtractionJson.value) } : null
  await saveDraftState({
    step: step.value,
    odometerReading: odometerReading.value,
    totalExpense: totalExpense.value,
    fuelDispensed: fuelDispensed.value,
    capturedAt: capturedAt.value,
    extractorModel: extractorModel.value,
    rawExtractionJson: rawExtraction,
    extractionWarning: extractionWarning.value,
    vehicleTag: vehicleTag.value,
    stationName: stationName.value,
    notes: notes.value,
    showExtra: showExtra.value,
  })
  if (!isUnmounted) {
    lastSavedLocallyAt.value = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
}

watch(
  [
    step,
    odometerReading,
    totalExpense,
    fuelDispensed,
    capturedAt,
    extractorModel,
    rawExtractionJson,
    extractionWarning,
    vehicleTag,
    stationName,
    notes,
    showExtra,
  ],
  () => {
    persistFormFieldsToDraft()
  }
)

async function applyImageToSlot(slot: 'odo' | 'pump', file: File | null) {
  const isOdo = slot === 'odo'
  const gen = isOdo ? ++odoGeneration : ++pumpGeneration
  const fileRef = isOdo ? odoFile : pumpFile
  const previewRef = isOdo ? odoPreview : pumpPreview
  const optimizingRef = isOdo ? odoOptimizing : pumpOptimizing
  const currentGen = () => (isOdo ? odoGeneration : pumpGeneration)

  const buildDraftPatch = (blob: File | null, name: string | null, type: string | null) =>
    isOdo
      ? { odoBlob: blob, odoName: name, odoType: type }
      : { pumpBlob: blob, pumpName: name, pumpType: type }

  revokeObjectUrl(previewRef.value)
  if (!file) {
    fileRef.value = null
    previewRef.value = null
    optimizingRef.value = false
    await saveDraftState(buildDraftPatch(null, null, null))
    return
  }

  // 1. Immediately display and persist raw photo to IndexedDB so a tab reload can never lose it
  const rawPreviewUrl = URL.createObjectURL(file)
  if (isUnmounted) {
    revokeObjectUrl(rawPreviewUrl)
    return
  }
  fileRef.value = file
  previewRef.value = rawPreviewUrl

  await saveDraftState(
    buildDraftPatch(
      file,
      file.name || `${slot}_${Date.now()}.jpg`,
      file.type || 'image/jpeg'
    )
  )
  if (isUnmounted || gen !== currentGen()) {
    return
  }
  lastSavedLocallyAt.value = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  // 2. Optimize image size in background for fast 4G/5G extraction & R2 upload
  optimizingRef.value = true
  try {
    const optimized = await optimizeSnapshotImage(file, slot)
    if (isUnmounted || gen !== currentGen()) {
      return
    }
    if (optimized !== file) {
      const oldUrl = previewRef.value
      const newUrl = URL.createObjectURL(optimized)
      fileRef.value = optimized
      previewRef.value = newUrl
      if (previewLightbox.value && previewLightbox.value.url === oldUrl) {
        previewLightbox.value = { ...previewLightbox.value, url: newUrl }
      }
      revokeObjectUrl(oldUrl)
      await saveDraftState(buildDraftPatch(optimized, optimized.name, optimized.type))
    }
  } finally {
    if (gen === currentGen()) {
      optimizingRef.value = false
    }
  }
}

function setOdoFile(file: File | null) {
  return applyImageToSlot('odo', file)
}

function setPumpFile(file: File | null) {
  return applyImageToSlot('pump', file)
}

function onOdoFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0] || null
  if (f) setOdoFile(f)
  input.value = ''
}

function onPumpFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0] || null
  if (f) setPumpFile(f)
  input.value = ''
}

function onCameraCaptured(file: File) {
  if (activeCameraTarget.value === 'odo') {
    setOdoFile(file)
  } else if (activeCameraTarget.value === 'pump') {
    setPumpFile(file)
  }
  activeCameraTarget.value = null
}

async function discardDraft() {
  isHydratingDraft.value = true
  odoGeneration++
  pumpGeneration++
  odoOptimizing.value = false
  pumpOptimizing.value = false
  previewLightbox.value = null
  revokeObjectUrl(odoPreview.value)
  revokeObjectUrl(pumpPreview.value)
  odoFile.value = null
  odoPreview.value = null
  pumpFile.value = null
  pumpPreview.value = null
  step.value = 'capture'
  odometerReading.value = ''
  totalExpense.value = ''
  fuelDispensed.value = ''
  capturedAt.value = toLocalDatetimeInput(new Date())
  rawExtractionJson.value = null
  extractorModel.value = null
  extractionWarning.value = null
  saveError.value = null
  stationName.value = ''
  notes.value = ''
  draftRestored.value = false
  lastSavedLocallyAt.value = null
  try {
    await nextTick()
    await clearDraftState()
  } finally {
    isHydratingDraft.value = false
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onWindowKeydown)

  // 1. Restore any existing draft from IndexedDB across browser refreshes
  try {
    const draft = await loadDraftState()
    if (isUnmounted) return
    if (draft) {
      let restoredSomething = false
      if (draft.odoBlob) {
        const f = blobToFile(draft.odoBlob, draft.odoName || 'odo_restored.jpg', draft.odoType)
        odoFile.value = f
        odoPreview.value = URL.createObjectURL(f)
        restoredSomething = true
      }
      if (draft.pumpBlob) {
        const f = blobToFile(draft.pumpBlob, draft.pumpName || 'pump_restored.jpg', draft.pumpType)
        pumpFile.value = f
        pumpPreview.value = URL.createObjectURL(f)
        restoredSomething = true
      }
      if (draft.odometerReading) {
        odometerReading.value = draft.odometerReading
        restoredSomething = true
      }
      if (draft.totalExpense) {
        totalExpense.value = draft.totalExpense
        restoredSomething = true
      }
      if (draft.fuelDispensed) {
        fuelDispensed.value = draft.fuelDispensed
        restoredSomething = true
      }
      if (draft.capturedAt) {
        capturedAt.value = draft.capturedAt
      }
      if (draft.rawExtractionJson) {
        rawExtractionJson.value = draft.rawExtractionJson
      }
      if (draft.extractorModel) {
        extractorModel.value = draft.extractorModel
      }
      if (draft.extractionWarning) {
        extractionWarning.value = draft.extractionWarning
      }
      if (draft.vehicleTag) {
        vehicleTag.value = draft.vehicleTag
      }
      if (draft.stationName) {
        stationName.value = draft.stationName
      }
      if (draft.notes) {
        notes.value = draft.notes
      }
      showExtra.value = Boolean(draft.showExtra)
      if (restoredSomething) {
        step.value = draft.step || 'capture'
        draftRestored.value = true
      }
    }
    await nextTick()
  } finally {
    isHydratingDraft.value = false
  }

  // 2. Fetch latest snapshot for live km/L efficiency calculation
  try {
    const latest = await fetchLatestSnapshot()
    if (!isUnmounted && latest && latest.odometer_reading != null) {
      previousOdometer.value = latest.odometer_reading
    }
  } catch {
    // Non-blocking
  }
})

function onWindowKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    previewLightbox.value = null
    activeCameraTarget.value = null
  }
}

onBeforeUnmount(() => {
  isUnmounted = true
  odoGeneration++
  pumpGeneration++
  window.removeEventListener('keydown', onWindowKeydown)
  revokeObjectUrl(odoPreview.value)
  revokeObjectUrl(pumpPreview.value)
})

async function runExtraction() {
  if (!hasAnyImage.value) return
  extracting.value = true
  extractionWarning.value = null

  try {
    const res = await extractFromImages(odoFile.value, pumpFile.value)
    if (isUnmounted) return
    rawExtractionJson.value = res.extraction
    extractorModel.value = res.meta.extractor_model
    extractionWarning.value = res.meta.warning

    if (res.efficiency?.previous_odometer != null) {
      previousOdometer.value = res.efficiency.previous_odometer
    }

    if (res.extraction.odometer_reading !== null) {
      odometerReading.value = String(res.extraction.odometer_reading)
    }
    if (res.extraction.total_expense !== null) {
      totalExpense.value = String(res.extraction.total_expense)
    }
    if (res.extraction.fuel_dispensed !== null) {
      fuelDispensed.value = String(res.extraction.fuel_dispensed)
    }

    step.value = 'confirm'
    await persistFormFieldsToDraft()
  } catch (err: unknown) {
    if (isUnmounted) return
    if ((err as any)?.isAuthError) {
      showKeyModal.value = true
    }
    extractionWarning.value =
      err instanceof Error
        ? `${err.message}. Switched to manual fallback — your photos remain saved on your device.`
        : 'Extraction failed. Switched to manual fallback — your photos remain saved on your device.'
    step.value = 'confirm'
    await persistFormFieldsToDraft()
  } finally {
    if (!isUnmounted) {
      extracting.value = false
    }
  }
}

function skipToManual() {
  rawExtractionJson.value = null
  extractionWarning.value = null
  step.value = 'confirm'
}

function formatFileSize(bytes: number | undefined): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  const kb = Math.round(bytes / 1024)
  if (kb < 1024) return `${kb} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function submitSnapshot() {
  saveError.value = null
  const odoVal = parseFloat(odometerReading.value)
  const expVal = parseFloat(totalExpense.value)
  const fuelVal = parseFloat(fuelDispensed.value)

  if (isNaN(odoVal) || odoVal < 0) {
    saveError.value = 'Please enter a valid odometer reading.'
    return
  }
  if (isNaN(expVal) || expVal < 0) {
    saveError.value = 'Please enter a valid total fuel expense.'
    return
  }
  if (isNaN(fuelVal) || fuelVal < 0) {
    saveError.value = 'Please enter a valid fuel dispensed volume.'
    return
  }

  saving.value = true
  try {
    const extra: Record<string, unknown> = {}
    if (vehicleTag.value.trim()) extra.vehicle = vehicleTag.value.trim()
    if (stationName.value.trim()) extra.station = stationName.value.trim()
    if (notes.value.trim()) extra.notes = notes.value.trim()

    if (liveEfficiency.value && !liveEfficiency.value.isFirstLog && !liveEfficiency.value.isInvalidOdo) {
      if (liveEfficiency.value.kmPerL != null) {
        extra.km_per_l = liveEfficiency.value.kmPerL
      }
      extra.distance_delta = liveEfficiency.value.distanceDelta
      extra.previous_odometer = liveEfficiency.value.previousOdometer
    }

    const parsedDate = capturedAt.value ? new Date(capturedAt.value) : new Date()
    if (isNaN(parsedDate.getTime())) {
      saveError.value = 'Please enter a valid date and time.'
      return
    }
    const isoTime = parsedDate.toISOString()

    // Upload retained client images to Cloudflare R2 (public-bucket/fvs/) and persist log atomically
    await createSnapshot(
      {
        captured_at: isoTime,
        odometer_reading: odoVal,
        total_expense: expVal,
        fuel_dispensed: fuelVal,
        extractor_model: extractorModel.value,
        raw_extraction_json: rawExtractionJson.value ? { ...toRaw(rawExtractionJson.value) } : null,
        extra_data: extra,
      },
      odoFile.value,
      pumpFile.value
    )

    // Prevent in-flight background optimization or watchers from recreating the draft after submit
    isHydratingDraft.value = true
    odoGeneration++
    pumpGeneration++
    // Clear client IndexedDB draft ONLY after R2 upload + SQLite commit succeeds
    await clearDraftState()
    router.push('/')
  } catch (err: unknown) {
    if ((err as any)?.isAuthError) {
      showKeyModal.value = true
    }
    saveError.value =
      err instanceof Error
        ? `${err.message} (Your photos & numbers remain safely saved on this device.)`
        : 'Failed to save fuel log. Your photos & numbers remain safely saved on this device.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section>
    <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 1rem">
      <RouterLink to="/" class="text-link" style="display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.8125rem">
        <!-- Arrow Left -->
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 19-7-7 7-7"/><path d="M19 12H5"/></svg>
        Back to Dashboard
      </RouterLink>

      <!-- Device Draft Retention Pill -->
      <div
        v-if="hasActiveLocalDraft"
        style="display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.75rem; color: #10b981; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.25); padding: 0.25rem 0.65rem; border-radius: 9999px"
      >
        <span>✓ {{ draftRestored ? 'Draft restored from device' : 'Saved on device (safe across refreshes)' }}</span>
        <button
          type="button"
          style="background: none; border: none; color: var(--muted-foreground); font-size: 0.75rem; text-decoration: underline; cursor: pointer; padding: 0 0 0 0.25rem"
          @click="discardDraft"
        >
          Clear draft
        </button>
      </div>
    </div>

    <header class="page-header">
      <p class="page-eyebrow">FuelV</p>
      <div style="display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 1.5rem">
        <div>
          <h1 class="page-title">Log Fuel</h1>
          <p class="page-description">
            Capture odometer and pump photos. Stored safely on your device until you confirm and save.
          </p>
        </div>

        <div class="ui-tabs-list" role="tablist">
          <button
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: step === 'capture' }"
            @click="step = 'capture'"
          >
            1. Photos
          </button>
          <button
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: step === 'confirm' }"
            @click="step = 'confirm'"
          >
            2. Confirm
          </button>
        </div>
      </div>
    </header>

    <!-- STEP 1: Dual Image Upload / Camera Capture -->
    <div v-if="step === 'capture'">
      <div class="capture-grid">
        <!-- Slot 1: Odometer -->
        <div class="upload-slot" :class="{ 'has-file': odoPreview }" style="cursor: default">
          <template v-if="odoPreview">
            <img
              :src="odoPreview"
              alt="Odometer preview"
              class="upload-preview"
              style="cursor: zoom-in"
              title="Tap to enlarge odometer photo"
              @click="previewLightbox = { url: odoPreview, title: 'Odometer Photo (Stored on Device)' }"
            />
            <span style="font-size: 0.75rem; color: #10b981; font-weight: 500">
              ✓ 1. Odometer saved on device
              <span v-if="odoFile" style="color: var(--muted-foreground); font-weight: 400">
                ({{ odoOptimizing ? 'optimizing…' : formatFileSize(odoFile.size) }})
              </span>
            </span>
          </template>
          <template v-else>
            <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" style="color: var(--muted-foreground)"><circle cx="12" cy="12" r="10"/><path d="m14.31 8 5.74 9.94"/><path d="M9.69 8h11.48"/><path d="m7.38 12 5.74-9.94"/></svg>
            <strong style="font-size: 0.9375rem; font-weight: 500">1. Odometer Photo</strong>
            <span style="font-size: 0.8125rem; color: var(--muted-foreground)">
              Vehicle dashboard odometer reading
            </span>
          </template>

          <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 0.5rem; margin-top: 0.75rem">
            <!-- Primary Action: One-Tap Native 1.0x Camera (Fixes Samsung 0.6x WebRTC lens selection) -->
            <label class="ui-btn ui-btn-default ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                class="sr-only"
                @change="onOdoFileChange"
              />
              <!-- Camera icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
              {{ odoPreview ? 'Retake photo' : 'Take photo' }}
            </label>

            <!-- Mode 2: Select from gallery / files -->
            <label class="ui-btn ui-btn-outline ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                class="sr-only"
                @change="onOdoFileChange"
              />
              <!-- Upload icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
              Gallery
            </label>

            <!-- Mode 3: In-browser 1.0x WebRTC viewfinder modal -->
            <UiButton
              variant="secondary"
              size="sm"
              @click="activeCameraTarget = 'odo'"
            >
              Web Cam
            </UiButton>

            <UiButton
              v-if="odoPreview"
              variant="ghost"
              size="sm"
              @click="setOdoFile(null)"
            >
              Clear
            </UiButton>
          </div>
        </div>

        <!-- Slot 2: Fuel Pump -->
        <div class="upload-slot" :class="{ 'has-file': pumpPreview }" style="cursor: default">
          <template v-if="pumpPreview">
            <img
              :src="pumpPreview"
              alt="Fuel pump preview"
              class="upload-preview"
              style="cursor: zoom-in"
              title="Tap to enlarge fuel pump photo"
              @click="previewLightbox = { url: pumpPreview, title: 'Fuel Pump Photo (Stored on Device)' }"
            />
            <span style="font-size: 0.75rem; color: #10b981; font-weight: 500">
              ✓ 2. Fuel pump saved on device
              <span v-if="pumpFile" style="color: var(--muted-foreground); font-weight: 400">
                ({{ pumpOptimizing ? 'optimizing…' : formatFileSize(pumpFile.size) }})
              </span>
            </span>
          </template>
          <template v-else>
            <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" style="color: var(--muted-foreground)"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>
            <strong style="font-size: 0.9375rem; font-weight: 500">2. Fuel Pump Display</strong>
            <span style="font-size: 0.8125rem; color: var(--muted-foreground)">
              Total expense &amp; fuel dispensed
            </span>
          </template>

          <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 0.5rem; margin-top: 0.75rem">
            <!-- Primary Action: One-Tap Native 1.0x Camera -->
            <label class="ui-btn ui-btn-default ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                class="sr-only"
                @change="onPumpFileChange"
              />
              <!-- Camera icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
              {{ pumpPreview ? 'Retake photo' : 'Take photo' }}
            </label>

            <!-- Mode 2: Select from gallery / files -->
            <label class="ui-btn ui-btn-outline ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                class="sr-only"
                @change="onPumpFileChange"
              />
              <!-- Upload icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
              Gallery
            </label>

            <!-- Mode 3: In-browser 1.0x WebRTC viewfinder modal -->
            <UiButton
              variant="secondary"
              size="sm"
              @click="activeCameraTarget = 'pump'"
            >
              Web Cam
            </UiButton>

            <UiButton
              v-if="pumpPreview"
              variant="ghost"
              size="sm"
              @click="setPumpFile(null)"
            >
              Clear
            </UiButton>
          </div>
        </div>
      </div>

      <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border)">
        <UiButton variant="outline" :disabled="extracting" @click="skipToManual">
          Skip to manual entry
        </UiButton>

        <UiButton
          variant="default"
          :disabled="!hasAnyImage || extracting || odoOptimizing || pumpOptimizing"
          @click="runExtraction"
        >
          <span v-if="extracting">Analyzing photos with AI…</span>
          <span v-else>Extract from photos</span>
        </UiButton>
      </div>
    </div>

    <!-- STEP 2: Review Extracted JSON / Manual Fallback Form -->
    <form v-else @submit.prevent="submitSnapshot">
      <div v-if="odoPreview || pumpPreview" style="display: flex; flex-wrap: wrap; align-items: center; gap: 0.85rem; padding-bottom: 1.25rem; margin-bottom: 1.5rem; border-bottom: 1px solid var(--border)">
        <button
          v-if="odoPreview"
          type="button"
          class="thumb-card"
          title="Tap to inspect odometer photo"
          @click="previewLightbox = { url: odoPreview, title: 'Odometer Photo (Tap to verify digits)' }"
        >
          <img :src="odoPreview" alt="Odometer" />
          <span>Odo (Zoom)</span>
        </button>
        <button
          v-if="pumpPreview"
          type="button"
          class="thumb-card"
          title="Tap to inspect fuel pump photo"
          @click="previewLightbox = { url: pumpPreview, title: 'Fuel Pump Photo (Tap to verify digits)' }"
        >
          <img :src="pumpPreview" alt="Fuel Pump" />
          <span>Pump (Zoom)</span>
        </button>

        <div style="display: flex; align-items: center; gap: 0.5rem">
          <UiButton variant="outline" size="sm" type="button" @click="step = 'capture'">
            Change photos
          </UiButton>
          <UiButton
            v-if="hasAnyImage"
            variant="secondary"
            size="sm"
            type="button"
            :disabled="extracting || odoOptimizing || pumpOptimizing"
            @click="runExtraction"
          >
            {{ extracting ? 'Re-extracting…' : 'Re-run AI extraction' }}
          </UiButton>
        </div>
      </div>

      <div v-if="extractionWarning" class="notice-banner">
        <strong>Manual fallback:</strong> {{ extractionWarning }}
      </div>

      <div v-if="rawExtractionJson" class="code-block">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem">
          <span style="font-size: 0.75rem; color: var(--muted-foreground)">Extracted JSON Payload</span>
          <UiBadge v-if="extractorModel" variant="outline">{{ extractorModel }}</UiBadge>
        </div>
        <pre style="margin: 0">{{ JSON.stringify(rawExtractionJson, null, 2) }}</pre>
      </div>

      <!-- Real-time Fuel Efficiency Card -->
      <div v-if="liveEfficiency" class="efficiency-banner">
        <div v-if="liveEfficiency.isFirstLog" style="font-size: 0.8125rem; color: var(--muted-foreground)">
          ⚡ <strong>First Log Baseline:</strong> Sets initial odometer at {{ odometerReading || '0' }} km. Fuel efficiency (km/L) will start calculating on your next fill-up.
        </div>
        <div v-else-if="liveEfficiency.isInvalidOdo" style="font-size: 0.8125rem; color: #ef4444">
          ⚠️ Current odometer ({{ odometerReading }} km) is lower than previous log ({{ liveEfficiency.previousOdometer }} km).
        </div>
        <div v-else class="efficiency-content">
          <div class="efficiency-main">
            <span class="efficiency-label">Estimated Fuel Efficiency</span>
            <strong class="efficiency-value">{{ liveEfficiency.kmPerL }} <span class="efficiency-unit">km/L</span></strong>
          </div>
          <div class="efficiency-sub">
            +{{ liveEfficiency.distanceDelta }} km since last log ({{ liveEfficiency.previousOdometer }} km) · {{ fuelDispensed }} L dispensed
          </div>
        </div>
      </div>

      <div class="form-grid">
        <div class="field-group">
          <label for="odo-input" class="field-label">Odometer Reading (km)</label>
          <UiInput
            id="odo-input"
            v-model="odometerReading"
            type="number"
            step="any"
            min="0"
            placeholder="e.g. 9857.0"
            required
          />
        </div>

        <div class="field-group">
          <label for="expense-input" class="field-label">Total Fuel Expense (₹)</label>
          <UiInput
            id="expense-input"
            v-model="totalExpense"
            type="number"
            step="any"
            min="0"
            placeholder="e.g. 950.97"
            required
          />
        </div>

        <div class="field-group">
          <label for="fuel-input" class="field-label">Fuel Dispensed (L)</label>
          <UiInput
            id="fuel-input"
            v-model="fuelDispensed"
            type="number"
            step="any"
            min="0"
            placeholder="e.g. 8.22"
            required
          />
        </div>

        <div class="field-group">
          <label for="date-input" class="field-label">Date &amp; Time</label>
          <UiInput
            id="date-input"
            v-model="capturedAt"
            type="datetime-local"
            required
          />
        </div>
      </div>

      <!-- Optional extensible extra_data fields -->
      <div style="margin-bottom: 1.75rem">
        <button
          type="button"
          class="text-link"
          style="background: none; border: none; padding: 0; font-size: 0.8125rem; color: var(--muted-foreground); cursor: pointer; margin-bottom: 1rem"
          @click="showExtra = !showExtra"
        >
          {{ showExtra ? 'Hide optional metadata' : '+ Add optional metadata (vehicle, station, notes)' }}
        </button>

        <div v-if="showExtra" class="form-grid">
          <div class="field-group">
            <label for="vehicle-input" class="field-label">Vehicle</label>
            <UiInput
              id="vehicle-input"
              v-model="vehicleTag"
              placeholder="My Bike"
            />
          </div>

          <div class="field-group">
            <label for="station-input" class="field-label">Station</label>
            <UiInput
              id="station-input"
              v-model="stationName"
              placeholder="e.g. IndianOil / Shell"
            />
          </div>

          <div class="field-group full">
            <label for="notes-input" class="field-label">Notes</label>
            <UiInput
              id="notes-input"
              v-model="notes"
              placeholder="Full tank, highway trip, etc."
            />
          </div>
        </div>
      </div>

      <div v-if="saveError" class="notice-banner" style="border-color: #ef4444; color: #ef4444">
        {{ saveError }}
      </div>

      <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; padding-top: 1rem; border-top: 1px solid var(--border)">
        <UiButton variant="outline" type="button" @click="step = 'capture'">
          Back to photos
        </UiButton>

        <UiButton type="submit" variant="default" :disabled="saving">
          {{ saving ? 'Uploading to R2 & saving…' : 'Save fuel log' }}
        </UiButton>
      </div>
    </form>

    <!-- Full-Screen Photo Lightbox for Digit Inspection -->
    <div v-if="previewLightbox" class="dialog-overlay" @click="previewLightbox = null">
      <div class="dialog-content" @click.stop>
        <header class="dialog-header">
          <span>{{ previewLightbox.title }}</span>
          <UiButton variant="ghost" size="icon" @click="previewLightbox = null">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </UiButton>
        </header>
        <img :src="previewLightbox.url" :alt="previewLightbox.title" class="dialog-img" />
      </div>
    </div>

    <!-- Live Camera Capture Modal -->
    <CameraModal
      v-if="activeCameraTarget"
      :title="activeCameraTarget === 'odo' ? 'Capture Odometer Photo (1.0x)' : 'Capture Fuel Pump Display (1.0x)'"
      :filename-prefix="activeCameraTarget"
      @capture="onCameraCaptured"
      @close="activeCameraTarget = null"
    />

    <!-- Admin Access Key Modal -->
    <KeySettingsModal
      v-if="showKeyModal"
      @close="showKeyModal = false"
    />
  </section>
</template>
