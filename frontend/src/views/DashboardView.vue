<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import EditLogModal from '../components/EditLogModal.vue'
import ExpenseHeatmap from '../components/ExpenseHeatmap.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiTable from '../components/ui/UiTable.vue'
import UiTableBody from '../components/ui/UiTableBody.vue'
import UiTableCell from '../components/ui/UiTableCell.vue'
import UiTableHead from '../components/ui/UiTableHead.vue'
import UiTableHeader from '../components/ui/UiTableHeader.vue'
import UiTableRow from '../components/ui/UiTableRow.vue'
import {
  deleteSnapshot,
  fetchGroupedSnapshots,
  resolveApiUrl,
  type FuelSnapshot,
  type GroupByPeriod,
  type GroupedSnapshotsResponse,
} from '../services/api'

const STORAGE_MODE_KEY = 'fuelv_timeline_mode'
const STORAGE_METRIC_KEY = 'fuelv_heatmap_metric'

export type HeatmapMetric = 'expense' | 'distance'

// Heatmap state remembered from localStorage
const initialMode = (
  typeof window !== 'undefined'
    ? (window.localStorage.getItem(STORAGE_MODE_KEY) as GroupByPeriod) || 'month'
    : 'month'
)
const groupBy = ref<GroupByPeriod>(['day', 'week', 'month', 'year'].includes(initialMode) ? initialMode : 'month')

// Metric state remembered from localStorage (expense | distance)
const initialMetric = (
  typeof window !== 'undefined'
    ? (window.localStorage.getItem(STORAGE_METRIC_KEY) as HeatmapMetric) || 'expense'
    : 'expense'
)
const heatmapMetric = ref<HeatmapMetric>(['expense', 'distance'].includes(initialMetric) ? initialMetric : 'expense')

watch(heatmapMetric, (newMetric) => {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(STORAGE_METRIC_KEY, newMetric)
  }
})

const data = ref<GroupedSnapshotsResponse | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const previewImage = ref<{ url: string; title: string } | null>(null)

// Selected period for aggregation and drilldown
const selectedPeriod = ref<string | null>(null)

// Pagination state
const currentPage = ref(1)
const pageSize = ref(10)

// Active log being edited
const editingLog = ref<FuelSnapshot | null>(null)

const allLogs = computed<FuelSnapshot[]>(() => {
  if (!data.value) return []
  return data.value.groups.flatMap((g) => g.snapshots)
})

// Total distance calculated across all logs
const overallDistance = computed<number>(() => {
  if (data.value?.overall_distance !== undefined && data.value.overall_distance > 0) {
    return data.value.overall_distance
  }
  const odos = allLogs.value
    .map((l) => l.odometer_reading)
    .filter((n) => typeof n === 'number' && !isNaN(n))
  if (odos.length < 2) return 0
  return Math.max(...odos) - Math.min(...odos)
})

// Filtered logs if a period is selected from the heatmap
const filteredLogs = computed<FuelSnapshot[]>(() => {
  if (!selectedPeriod.value) return allLogs.value

  const period = selectedPeriod.value
  return allLogs.value.filter((l) => {
    const captured = l.captured_at
    if (!captured) return false

    if (groupBy.value === 'day') {
      return captured.slice(0, 10) === period
    }
    if (groupBy.value === 'month') {
      return captured.slice(0, 7) === period
    }
    if (groupBy.value === 'year') {
      return captured.slice(0, 4) === period
    }
    if (groupBy.value === 'week') {
      // Find matching group
      const grp = data.value?.groups.find((g) => g.period === period)
      return grp ? grp.snapshots.some((s) => s.id === l.id) : false
    }
    return true
  })
})

// Aggregation for the currently selected period
const selectedPeriodAggregation = computed(() => {
  if (!selectedPeriod.value) return null
  const logs = filteredLogs.value
  if (!logs.length) return null

  const totalExpense = logs.reduce((sum, l) => sum + (l.total_expense || 0), 0)
  const totalFuel = logs.reduce((sum, l) => sum + (l.fuel_dispensed || 0), 0)
  const odos = logs.map((l) => l.odometer_reading).filter((n) => typeof n === 'number' && !isNaN(n))
  const distanceSpan = odos.length > 1 ? Math.max(...odos) - Math.min(...odos) : 0

  return {
    period: selectedPeriod.value,
    count: logs.length,
    totalExpense,
    totalFuel,
    distanceSpan,
  }
})

// Paginated logs
const totalPages = computed(() => Math.max(1, Math.ceil(filteredLogs.value.length / pageSize.value)))

const paginatedLogs = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredLogs.value.slice(start, start + pageSize.value)
})

// Map of distance delta since the previous fuel log (chronologically)
const distanceDeltas = computed<Map<string, number>>(() => {
  const map = new Map<string, number>()
  // Sort ascending by captured_at to calculate deltas
  const chronological = [...allLogs.value].sort(
    (a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime()
  )

  for (let i = 1; i < chronological.length; i++) {
    const prev = chronological[i - 1]
    const curr = chronological[i]
    if (curr.odometer_reading > prev.odometer_reading) {
      map.set(curr.id, curr.odometer_reading - prev.odometer_reading)
    }
  }
  return map
})

function getPreviousOdometerForLog(logId: string): number | null {
  const chronological = [...allLogs.value].sort(
    (a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime()
  )
  const idx = chronological.findIndex((s) => s.id === logId)
  if (idx > 0) {
    return chronological[idx - 1].odometer_reading
  }
  return null
}

async function loadData() {
  loading.value = true
  error.value = null
  try {
    data.value = await fetchGroupedSnapshots(groupBy.value)
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'Failed to load fuel logs'
  } finally {
    loading.value = false
  }
}

watch(groupBy, (newMode) => {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(STORAGE_MODE_KEY, newMode)
  }
  selectedPeriod.value = null
  currentPage.value = 1
  loadData()
})

onMounted(() => {
  loadData()
})

function onHeatmapSelectPeriod(periodKey: string) {
  if (selectedPeriod.value === periodKey) {
    // Toggle off if already selected
    selectedPeriod.value = null
  } else {
    selectedPeriod.value = periodKey
  }
  currentPage.value = 1
}

function clearSelection() {
  selectedPeriod.value = null
  currentPage.value = 1
}

function formatPeriodLabel(period: string, mode: GroupByPeriod): string {
  if (mode === 'year') return `Year ${period}`
  if (mode === 'week') return period.replace('-W', ' · Week ')
  if (mode === 'day') {
    const dt = new Date(period + 'T12:00:00')
    if (!isNaN(dt.getTime())) {
      return dt.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
    }
    return period
  }
  const [yr, mo] = period.split('-')
  if (yr && mo) {
    const dt = new Date(Number(yr), Number(mo) - 1, 1)
    if (!isNaN(dt.getTime())) {
      return dt.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    }
  }
  return period
}

function formatDateTime(iso: string): string {
  const dt = new Date(iso)
  if (isNaN(dt.getTime())) return iso
  return dt.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function formatTime(iso: string): string {
  const dt = new Date(iso)
  if (isNaN(dt.getTime())) return ''
  return dt.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function entryBadgeLabel(mode: FuelSnapshot['entry_mode']): string {
  if (mode === 'vision') return 'Vision'
  if (mode === 'vision_edited') return 'Edited'
  return 'Manual'
}

async function handleDelete(id: string) {
  if (!confirm('Delete this fuel log?')) return
  try {
    await deleteSnapshot(id)
    await loadData()
  } catch (err: unknown) {
    if ((err as any)?.isAuthError) {
      alert('Unauthorized: Configure your Admin Access Key (UUID) in the top navbar to delete entries.')
    } else {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }
}

function openImage(url: string | null, title: string) {
  const resolved = resolveApiUrl(url)
  if (resolved) {
    previewImage.value = { url: resolved, title }
  }
}
</script>

<template>
  <section>
    <!-- Distinct Header Label -->
    <header class="page-header" style="margin-bottom: 1.5rem">
      <h1 class="page-title">Dashboard</h1>
    </header>

    <!-- ROW 1: Executive Summary -->
    <div v-if="data && data.total_snapshots > 0" class="executive-summary">
      <div class="executive-card">
        <span class="executive-card-label">Total Expense</span>
        <strong class="executive-card-value">
          ₹{{ data.overall_expense.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
        </strong>
        <span class="executive-card-sub">{{ data.overall_fuel_dispensed.toFixed(1) }} L dispensed</span>
      </div>

      <div class="executive-card">
        <span class="executive-card-label">Distance Covered</span>
        <strong class="executive-card-value">
          {{ overallDistance.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) }} km
        </strong>
        <span class="executive-card-sub">
          {{ (data.overall_fuel_dispensed > 0 ? (overallDistance / data.overall_fuel_dispensed).toFixed(1) : '—') }} km/L avg
        </span>
      </div>

      <div class="executive-card">
        <span class="executive-card-label">Total Logs</span>
        <strong class="executive-card-value">
          {{ data.total_snapshots }}
        </strong>
        <span class="executive-card-sub">{{ data.groups.length }} active {{ groupBy }}s</span>
      </div>
    </div>

    <!-- Timeline & Metric Controls -->
    <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 1rem">
      <div>
        <h2 class="section-heading" style="margin: 0">Heatmap</h2>
        <p style="margin: 0.15rem 0 0; font-size: 0.75rem; color: var(--muted-foreground)">
          Click any cell to filter and aggregate logs by period
        </p>
      </div>

      <div class="heatmap-controls-bar">
        <!-- Metric Dropdown Selector -->
        <div class="metric-selector-group">
          <label for="metric-select" class="selector-label">Metric</label>
          <select
            id="metric-select"
            v-model="heatmapMetric"
            class="ui-select"
          >
            <option value="expense">Expense (₹)</option>
            <option value="distance">Distance (km)</option>
          </select>
        </div>

        <!-- Remembered Heatmap State Tabs -->
        <div class="ui-tabs-list" role="tablist" aria-label="Timeline aggregation mode">
          <button
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: groupBy === 'day' }"
            @click="groupBy = 'day'"
          >
            Day
          </button>
          <button
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: groupBy === 'week' }"
            @click="groupBy = 'week'"
          >
            Week
          </button>
          <button
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: groupBy === 'month' }"
            @click="groupBy = 'month'"
          >
            Month
          </button>
          <button
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: groupBy === 'year' }"
            @click="groupBy = 'year'"
          >
            Year
          </button>
        </div>
      </div>
    </div>

    <!-- Interactive Heatmap Component -->
    <ExpenseHeatmap
      v-if="data && data.total_snapshots > 0"
      :mode="groupBy"
      :snapshots="allLogs"
      :groups="data.groups"
      :selected-period="selectedPeriod"
      :metric="heatmapMetric"
      @select-period="onHeatmapSelectPeriod"
    />

    <!-- Selected Period Aggregated Breakdown Banner -->
    <div v-if="selectedPeriodAggregation" class="period-drilldown-banner">
      <div class="period-drilldown-title">
        <span>Filtered: {{ formatPeriodLabel(selectedPeriodAggregation.period, groupBy) }}</span>
        <UiBadge variant="secondary">{{ selectedPeriodAggregation.count }} {{ selectedPeriodAggregation.count === 1 ? 'log' : 'logs' }}</UiBadge>
      </div>

      <div class="period-drilldown-metrics">
        <span>Expense: <strong>₹{{ selectedPeriodAggregation.totalExpense.toFixed(2) }}</strong></span>
        <span>Fuel: <strong>{{ selectedPeriodAggregation.totalFuel.toFixed(2) }} L</strong></span>
        <span v-if="selectedPeriodAggregation.distanceSpan > 0">
          Distance: <strong>{{ selectedPeriodAggregation.distanceSpan.toFixed(1) }} km</strong>
        </span>
        <UiButton variant="ghost" size="sm" @click="clearSelection">
          Show all logs
        </UiButton>
      </div>
    </div>

    <!-- Loading / Error States -->
    <p v-if="loading" style="padding: 3rem 0; font-size: 0.875rem; color: var(--muted-foreground)">
      Loading fuel logs…
    </p>
    <div v-else-if="error" class="notice-banner">
      <p style="margin: 0 0 0.75rem">{{ error }}</p>
      <UiButton variant="outline" size="sm" @click="loadData">Retry</UiButton>
    </div>
    <div v-else-if="!data || data.groups.length === 0" style="padding: 3rem 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); text-align: center">
      <p style="margin: 0 0 1rem; font-size: 0.875rem; color: var(--muted-foreground)">
        No fuel logs recorded yet.
      </p>
    </div>

    <!-- ROW 3: Standardized Table with Shadcn UI Components & Pagination -->
    <div v-if="data && data.total_snapshots > 0" class="table-card">
      <header class="table-card-header">
        <div style="display: flex; align-items: center; gap: 0.5rem">
          <h3 style="margin: 0; font-size: 0.9375rem; font-weight: 600">All Fuel Logs</h3>
          <UiBadge variant="outline">{{ filteredLogs.length }}</UiBadge>
        </div>

        <div v-if="selectedPeriod" style="display: flex; align-items: center; gap: 0.5rem">
          <span style="font-size: 0.75rem; color: var(--muted-foreground)">Filtered by {{ groupBy }}</span>
          <UiButton variant="outline" size="sm" @click="clearSelection">Clear</UiButton>
        </div>
      </header>

      <UiTable>
        <UiTableHeader>
          <UiTableRow>
            <UiTableHead>Date</UiTableHead>
            <UiTableHead>Odometer</UiTableHead>
            <UiTableHead>Distance</UiTableHead>
            <UiTableHead>Expense</UiTableHead>
            <UiTableHead>Dispensed</UiTableHead>
            <UiTableHead>Rate / Extra</UiTableHead>
            <UiTableHead>Photos</UiTableHead>
            <UiTableHead style="text-align: right">Actions</UiTableHead>
          </UiTableRow>
        </UiTableHeader>
        <UiTableBody>
          <UiTableRow v-for="log in paginatedLogs" :key="log.id">
            <!-- Date & Time -->
            <UiTableCell>
              <div style="font-weight: 500">{{ formatDateTime(log.captured_at) }}</div>
              <div style="font-size: 0.6875rem; color: var(--muted-foreground)">
                {{ formatTime(log.captured_at) }}
              </div>
            </UiTableCell>

            <!-- Odometer Reading -->
            <UiTableCell>
              <span style="font-variant-numeric: tabular-nums; font-weight: 500">
                {{ log.odometer_reading.toFixed(1) }} km
              </span>
            </UiTableCell>

            <!-- Distance Delta & km/L -->
            <UiTableCell>
              <div v-if="distanceDeltas.has(log.id)" style="font-variant-numeric: tabular-nums">
                <span style="color: #10b981; font-weight: 600">
                  +{{ distanceDeltas.get(log.id)!.toFixed(1) }} km
                </span>
                <div v-if="log.fuel_dispensed > 0" style="font-size: 0.6875rem; color: var(--muted-foreground)">
                  {{ (distanceDeltas.get(log.id)! / log.fuel_dispensed).toFixed(1) }} km/L
                </div>
              </div>
              <span v-else style="color: var(--muted-foreground)">—</span>
            </UiTableCell>

            <!-- Expense -->
            <UiTableCell>
              <span style="font-weight: 600; font-variant-numeric: tabular-nums">
                ₹{{ log.total_expense.toFixed(2) }}
              </span>
            </UiTableCell>

            <!-- Fuel Dispensed -->
            <UiTableCell>
              <span style="font-variant-numeric: tabular-nums">
                {{ log.fuel_dispensed.toFixed(2) }} L
              </span>
            </UiTableCell>

            <!-- Extra Info / Badges -->
            <UiTableCell>
              <div style="display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center">
                <UiBadge variant="outline" style="font-size: 0.6875rem">{{ entryBadgeLabel(log.entry_mode) }}</UiBadge>
                <UiBadge v-if="log.extra_data?.unit_price" variant="secondary" style="font-size: 0.6875rem">
                  ₹{{ log.extra_data.unit_price }}/L
                </UiBadge>
                <UiBadge v-if="log.extra_data?.km_per_l" variant="outline" style="font-size: 0.6875rem">
                  {{ log.extra_data.km_per_l }} km/L
                </UiBadge>
                <span v-if="log.extra_data?.vehicle" style="font-size: 0.6875rem; color: var(--muted-foreground)">
                  {{ log.extra_data.vehicle }}
                </span>
              </div>
            </UiTableCell>

            <!-- Photos -->
            <UiTableCell>
              <div style="display: flex; align-items: center; gap: 0.35rem">
                <button
                  v-if="log.odo_image_url"
                  type="button"
                  class="thumb-card"
                  title="View odometer photo"
                  @click="openImage(log.odo_image_url, `Odometer · ${log.odometer_reading} km`)"
                >
                  <img :src="resolveApiUrl(log.odo_image_url) || ''" alt="Odo" />
                </button>
                <button
                  v-if="log.pump_image_url"
                  type="button"
                  class="thumb-card"
                  title="View pump photo"
                  @click="openImage(log.pump_image_url, `Pump · ₹${log.total_expense}`)"
                >
                  <img :src="resolveApiUrl(log.pump_image_url) || ''" alt="Pump" />
                </button>
                <span v-if="!log.odo_image_url && !log.pump_image_url" style="color: var(--muted-foreground); font-size: 0.75rem">—</span>
              </div>
            </UiTableCell>

            <!-- Actions (Edit & Delete) -->
            <UiTableCell style="text-align: right">
              <div style="display: inline-flex; align-items: center; justify-content: flex-end; gap: 0.25rem">
                <UiButton
                  variant="ghost"
                  size="icon"
                  title="Edit fuel log"
                  @click="editingLog = log"
                >
                  <!-- Edit Icon (Pencil) -->
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
                </UiButton>
                <UiButton
                  variant="ghost"
                  size="icon"
                  title="Delete log"
                  @click="handleDelete(log.id)"
                >
                  <!-- Trash2 Icon -->
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                </UiButton>
              </div>
            </UiTableCell>
          </UiTableRow>
        </UiTableBody>
      </UiTable>

      <!-- Standard Pagination Bar -->
      <footer class="table-pagination-bar">
        <span>
          Showing {{ ((currentPage - 1) * pageSize) + 1 }}–{{ Math.min(currentPage * pageSize, filteredLogs.length) }} of {{ filteredLogs.length }} {{ filteredLogs.length === 1 ? 'log' : 'logs' }}
        </span>

        <div style="display: flex; align-items: center; gap: 0.5rem">
          <UiButton
            variant="outline"
            size="sm"
            :disabled="currentPage <= 1"
            @click="currentPage--"
          >
            Previous
          </UiButton>

          <span style="font-variant-numeric: tabular-nums; padding: 0 0.25rem">
            Page {{ currentPage }} of {{ totalPages }}
          </span>

          <UiButton
            variant="outline"
            size="sm"
            :disabled="currentPage >= totalPages"
            @click="currentPage++"
          >
            Next
          </UiButton>
        </div>
      </footer>
    </div>

    <!-- Image Preview Dialog -->
    <div v-if="previewImage" class="dialog-overlay" @click="previewImage = null">
      <div class="dialog-content" @click.stop>
        <header class="dialog-header">
          <span>{{ previewImage.title }}</span>
          <UiButton variant="ghost" size="icon" @click="previewImage = null">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </UiButton>
        </header>
        <img :src="previewImage.url" :alt="previewImage.title" class="dialog-img" />
      </div>
    </div>

    <!-- Edit Fuel Log Modal -->
    <EditLogModal
      v-if="editingLog"
      :log="editingLog"
      :previous-odometer="getPreviousOdometerForLog(editingLog.id)"
      @close="editingLog = null"
      @updated="loadData"
    />

    <!-- SINGLE Point of Entry to Log Fuel: Floating Action Button (FAB) -->
    <RouterLink
      to="/capture"
      class="fab-log-action"
      title="Log fuel"
      aria-label="Log fuel"
    >
      <!-- Plus Icon -->
      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
      <span>Log Fuel</span>
    </RouterLink>
  </section>
</template>
