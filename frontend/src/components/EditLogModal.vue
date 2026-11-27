<script setup lang="ts">
import { computed, ref } from 'vue'
import { updateSnapshot, type FuelSnapshot } from '../services/api'
import UiButton from './ui/UiButton.vue'
import UiInput from './ui/UiInput.vue'

const props = defineProps<{
  log: FuelSnapshot
  previousOdometer?: number | null
}>()

const emit = defineEmits<{
  close: []
  updated: [log: FuelSnapshot]
}>()

function toLocalDatetimeInput(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const odometerReading = ref(String(props.log.odometer_reading))
const totalExpense = ref(String(props.log.total_expense))
const fuelDispensed = ref(String(props.log.fuel_dispensed))
const capturedAt = ref(toLocalDatetimeInput(props.log.captured_at))

const vehicleTag = ref(String(props.log.extra_data?.vehicle || ''))
const stationName = ref(String(props.log.extra_data?.station || ''))
const notes = ref(String(props.log.extra_data?.notes || ''))

const saving = ref(false)
const error = ref<string | null>(null)

// Live calculated efficiency inside the edit modal
const liveEfficiency = computed(() => {
  const odo = parseFloat(odometerReading.value)
  const fuel = parseFloat(fuelDispensed.value)
  if (isNaN(odo) || isNaN(fuel) || fuel <= 0) return null
  if (props.previousOdometer == null) return null
  if (odo < props.previousOdometer) {
    return {
      isInvalidOdo: true,
      distanceDelta: Math.round((odo - props.previousOdometer) * 10) / 10,
      kmPerL: null,
    }
  }
  const delta = Math.round((odo - props.previousOdometer) * 10) / 10
  const kmpl = Math.round((delta / fuel) * 100) / 100
  return {
    isInvalidOdo: false,
    distanceDelta: delta,
    kmPerL: kmpl,
  }
})

async function save() {
  error.value = null
  const odoVal = parseFloat(odometerReading.value)
  const expVal = parseFloat(totalExpense.value)
  const fuelVal = parseFloat(fuelDispensed.value)

  if (isNaN(odoVal) || odoVal < 0) {
    error.value = 'Please enter a valid odometer reading.'
    return
  }
  if (isNaN(expVal) || expVal < 0) {
    error.value = 'Please enter a valid total fuel expense.'
    return
  }
  if (isNaN(fuelVal) || fuelVal <= 0) {
    error.value = 'Please enter a valid fuel volume dispensed.'
    return
  }

  saving.value = true
  try {
    const extra: Record<string, unknown> = { ...(props.log.extra_data || {}) }
    if (vehicleTag.value.trim()) extra.vehicle = vehicleTag.value.trim()
    else delete extra.vehicle

    if (stationName.value.trim()) extra.station = stationName.value.trim()
    else delete extra.station

    if (notes.value.trim()) extra.notes = notes.value.trim()
    else delete extra.notes

    if (liveEfficiency.value && !liveEfficiency.value.isInvalidOdo && liveEfficiency.value.kmPerL != null) {
      extra.km_per_l = liveEfficiency.value.kmPerL
      extra.distance_delta = liveEfficiency.value.distanceDelta
      if (props.previousOdometer != null) {
        extra.previous_odometer = props.previousOdometer
      }
    }

    let isoTime = props.log.captured_at
    if (capturedAt.value) {
      const parsedDate = new Date(capturedAt.value)
      if (isNaN(parsedDate.getTime())) {
        error.value = 'Please enter a valid date and time.'
        saving.value = false
        return
      }
      isoTime = parsedDate.toISOString()
    }

    const updated = await updateSnapshot(props.log.id, {
      odometer_reading: odoVal,
      total_expense: expVal,
      fuel_dispensed: fuelVal,
      captured_at: isoTime,
      extra_data: extra,
    })

    emit('updated', updated)
    emit('close')
  } catch (err: unknown) {
    if ((err as any)?.isAuthError) {
      error.value = 'Unauthorized: Admin Access Key (UUID) required. Configure in the top navbar.'
    } else {
      error.value = err instanceof Error ? err.message : 'Failed to update fuel log'
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="dialog-overlay" @click="emit('close')">
    <div class="dialog-content" style="max-width: 32rem" @click.stop>
      <header class="dialog-header">
        <span style="font-weight: 600">Edit Fuel Log</span>
        <UiButton variant="ghost" size="icon" @click="emit('close')">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </UiButton>
      </header>

      <form style="padding: 1.25rem" @submit.prevent="save">
        <div v-if="error" class="notice-banner" style="margin-bottom: 1rem; color: #ef4444">
          {{ error }}
        </div>

        <!-- Live Efficiency Recomputation in Modal -->
        <div v-if="liveEfficiency && !liveEfficiency.isInvalidOdo" class="efficiency-banner" style="margin-bottom: 1rem; padding: 0.65rem 0.95rem">
          <div class="efficiency-content">
            <div class="efficiency-main">
              <span class="efficiency-label" style="font-size: 0.625rem">Recalculated Fuel Efficiency</span>
              <strong class="efficiency-value" style="font-size: 1.1rem">{{ liveEfficiency.kmPerL }} <span class="efficiency-unit">km/L</span></strong>
            </div>
            <div class="efficiency-sub" style="font-size: 0.6875rem">
              +{{ liveEfficiency.distanceDelta }} km · {{ fuelDispensed }} L
            </div>
          </div>
        </div>

        <div class="form-grid" style="gap: 0.85rem; margin-bottom: 1.25rem">
          <div class="field-group">
            <label class="field-label" for="edit-odo">Odometer Reading (km)</label>
            <UiInput id="edit-odo" v-model="odometerReading" type="number" step="any" min="0" required />
          </div>

          <div class="field-group">
            <label class="field-label" for="edit-exp">Total Expense (₹)</label>
            <UiInput id="edit-exp" v-model="totalExpense" type="number" step="any" min="0" required />
          </div>

          <div class="field-group">
            <label class="field-label" for="edit-fuel">Fuel Dispensed (L)</label>
            <UiInput id="edit-fuel" v-model="fuelDispensed" type="number" step="any" min="0" required />
          </div>

          <div class="field-group">
            <label class="field-label" for="edit-date">Date &amp; Time</label>
            <UiInput id="edit-date" v-model="capturedAt" type="datetime-local" required />
          </div>

          <div class="field-group full">
            <label class="field-label" for="edit-vehicle">Vehicle Tag / Description</label>
            <UiInput id="edit-vehicle" v-model="vehicleTag" placeholder="e.g. My Bike, Car" />
          </div>
        </div>

        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 0.5rem; padding-top: 0.75rem; border-top: 1px solid var(--border)">
          <UiButton variant="outline" type="button" @click="emit('close')">
            Cancel
          </UiButton>
          <UiButton variant="default" type="submit" :disabled="saving">
            <span v-if="saving">Saving…</span>
            <span v-else>Update Log</span>
          </UiButton>
        </div>
      </form>
    </div>
  </div>
</template>
