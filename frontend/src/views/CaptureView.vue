<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import CameraModal from '../components/CameraModal.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiInput from '../components/ui/UiInput.vue'
import {
  createSnapshot,
  extractFromImages,
  type ExtractedFuelData,
} from '../services/api'

const router = useRouter()

// Image state
const odoFile = ref<File | null>(null)
const odoPreview = ref<string | null>(null)
const pumpFile = ref<File | null>(null)
const pumpPreview = ref<string | null>(null)

// Camera modal state ('odo' | 'pump' | null)
const activeCameraTarget = ref<'odo' | 'pump' | null>(null)

// Extraction state
const extracting = ref(false)
const extractionWarning = ref<string | null>(null)
const extractorModel = ref<string | null>(null)
const rawExtractionJson = ref<ExtractedFuelData | null>(null)
const savedOdoPath = ref<string | null>(null)
const savedPumpPath = ref<string | null>(null)

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

function setOdoFile(file: File | null) {
  odoFile.value = file
  odoPreview.value = file ? URL.createObjectURL(file) : null
}

function setPumpFile(file: File | null) {
  pumpFile.value = file
  pumpPreview.value = file ? URL.createObjectURL(file) : null
}

function onOdoFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  setOdoFile(input.files?.[0] || null)
}

function onPumpFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  setPumpFile(input.files?.[0] || null)
}

function onCameraCaptured(file: File) {
  if (activeCameraTarget.value === 'odo') {
    setOdoFile(file)
  } else if (activeCameraTarget.value === 'pump') {
    setPumpFile(file)
  }
  activeCameraTarget.value = null
}

const hasAnyImage = computed(() => Boolean(odoFile.value || pumpFile.value))

async function runExtraction() {
  if (!hasAnyImage.value) return
  extracting.value = true
  extractionWarning.value = null

  try {
    const res = await extractFromImages(odoFile.value, pumpFile.value)
    rawExtractionJson.value = res.extraction
    extractorModel.value = res.meta.extractor_model
    savedOdoPath.value = res.meta.odo_image_path
    savedPumpPath.value = res.meta.pump_image_path
    extractionWarning.value = res.meta.warning

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
  } catch (err: unknown) {
    extractionWarning.value =
      err instanceof Error
        ? `${err.message}. Switched to manual fallback.`
        : 'Extraction failed. Switched to manual fallback.'
    step.value = 'confirm'
  } finally {
    extracting.value = false
  }
}

function skipToManual() {
  rawExtractionJson.value = null
  extractionWarning.value = null
  step.value = 'confirm'
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

    const isoTime = capturedAt.value
      ? new Date(capturedAt.value).toISOString()
      : new Date().toISOString()

    await createSnapshot({
      captured_at: isoTime,
      odometer_reading: odoVal,
      total_expense: expVal,
      fuel_dispensed: fuelVal,
      odo_image_path: savedOdoPath.value,
      pump_image_path: savedPumpPath.value,
      extractor_model: extractorModel.value,
      raw_extraction_json: rawExtractionJson.value,
      extra_data: extra,
    })

    router.push('/')
  } catch (err: unknown) {
    saveError.value = err instanceof Error ? err.message : 'Failed to save snapshot'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section>
    <header class="page-header">
      <p class="page-eyebrow">FuelV</p>
      <div style="display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 1.5rem">
        <div>
          <h1 class="page-title">Capture Snapshot</h1>
          <p class="page-description">
            Select from files or capture using your camera for both the odometer and fuel pump display.
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
            <img :src="odoPreview" alt="Odometer preview" class="upload-preview" />
            <span style="font-size: 0.75rem; color: var(--muted-foreground)">
              1. Odometer photo attached
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
            <!-- Mode 1: Select from files -->
            <label class="ui-btn ui-btn-outline ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                class="sr-only"
                @change="onOdoFileChange"
              />
              <!-- Upload icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
              Select file
            </label>

            <!-- Mode 2: Capture using camera -->
            <UiButton
              variant="secondary"
              size="sm"
              @click="activeCameraTarget = 'odo'"
            >
              <!-- Camera icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
              Use camera
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
            <img :src="pumpPreview" alt="Fuel pump preview" class="upload-preview" />
            <span style="font-size: 0.75rem; color: var(--muted-foreground)">
              2. Fuel pump photo attached
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
            <!-- Mode 1: Select from files -->
            <label class="ui-btn ui-btn-outline ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                class="sr-only"
                @change="onPumpFileChange"
              />
              <!-- Upload icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
              Select file
            </label>

            <!-- Mode 2: Capture using camera -->
            <UiButton
              variant="secondary"
              size="sm"
              @click="activeCameraTarget = 'pump'"
            >
              <!-- Camera icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
              Use camera
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
          :disabled="!hasAnyImage || extracting"
          @click="runExtraction"
        >
          <span v-if="extracting">Extracting JSON…</span>
          <span v-else>Extract from photos</span>
        </UiButton>
      </div>
    </div>

    <!-- STEP 2: Review Extracted JSON / Manual Fallback Form -->
    <form v-else @submit.prevent="submitSnapshot">
      <div v-if="odoPreview || pumpPreview" style="display: flex; align-items: center; gap: 1rem; padding-bottom: 1.25rem; margin-bottom: 1.5rem; border-bottom: 1px solid var(--border)">
        <div v-if="odoPreview" class="thumb-card" style="cursor: default">
          <img :src="odoPreview" alt="Odometer" />
          <span>Odo</span>
        </div>
        <div v-if="pumpPreview" class="thumb-card" style="cursor: default">
          <img :src="pumpPreview" alt="Fuel Pump" />
          <span>Pump</span>
        </div>
        <UiButton variant="ghost" size="sm" @click="step = 'capture'">
          Change photos
        </UiButton>
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

      <div v-if="saveError" class="notice-banner">
        {{ saveError }}
      </div>

      <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; padding-top: 1rem; border-top: 1px solid var(--border)">
        <UiButton variant="outline" @click="step = 'capture'">
          Back
        </UiButton>

        <UiButton type="submit" variant="default" :disabled="saving">
          {{ saving ? 'Saving…' : 'Save snapshot' }}
        </UiButton>
      </div>
    </form>

    <!-- Live Camera Capture Modal -->
    <CameraModal
      v-if="activeCameraTarget"
      :title="activeCameraTarget === 'odo' ? 'Capture Odometer Photo' : 'Capture Fuel Pump Display'"
      :filename-prefix="activeCameraTarget"
      @capture="onCameraCaptured"
      @close="activeCameraTarget = null"
    />
  </section>
</template>
