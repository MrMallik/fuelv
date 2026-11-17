<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import ExpenseHeatmap from '../components/ExpenseHeatmap.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import {
  deleteSnapshot,
  fetchGroupedSnapshots,
  resolveApiUrl,
  type FuelSnapshot,
  type GroupByPeriod,
  type GroupedSnapshotsResponse,
} from '../services/api'

const groupBy = ref<GroupByPeriod>('month')
const data = ref<GroupedSnapshotsResponse | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const previewImage = ref<{ url: string; title: string } | null>(null)

const allSnapshots = computed<FuelSnapshot[]>(() => {
  if (!data.value) return []
  return data.value.groups.flatMap((g) => g.snapshots)
})

async function loadData() {
  loading.value = true
  error.value = null
  try {
    data.value = await fetchGroupedSnapshots(groupBy.value)
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'Failed to load snapshots'
  } finally {
    loading.value = false
  }
}

watch(groupBy, () => {
  loadData()
})

onMounted(() => {
  loadData()
})

function formatPeriodLabel(period: string, mode: GroupByPeriod): string {
  if (mode === 'year') return period
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

function onHeatmapSelectPeriod(periodKey: string) {
  const el = document.getElementById(`period-${periodKey}`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function formatDateTime(iso: string): string {
  const dt = new Date(iso)
  if (isNaN(dt.getTime())) return iso
  return dt.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function entryBadgeLabel(mode: FuelSnapshot['entry_mode']): string {
  if (mode === 'vision') return 'Vision'
  if (mode === 'vision_edited') return 'Vision · Edited'
  return 'Manual'
}

async function handleDelete(id: string) {
  if (!confirm('Delete this fuel snapshot?')) return
  try {
    await deleteSnapshot(id)
    await loadData()
  } catch (err: unknown) {
    alert(err instanceof Error ? err.message : 'Delete failed')
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
    <header class="page-header">
      <p class="page-eyebrow">FuelV</p>
      <div style="display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 1.5rem">
        <div>
          <h1 class="page-title">Fuel Log</h1>
          <p class="page-description">
            Odometer and fuel pump snapshots, sorted and aggregated over time.
          </p>
        </div>

        <RouterLink to="/capture" class="ui-btn ui-btn-default">
          <!-- Plus Icon (Lucide) -->
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
          New snapshot
        </RouterLink>
      </div>
    </header>

    <!-- Summary KPI Strip -->
    <div v-if="data && data.total_snapshots > 0" class="stat-strip">
      <div class="stat-cell">
        <span class="stat-label">Snapshots</span>
        <strong class="stat-value">{{ data.total_snapshots }}</strong>
      </div>
      <div class="stat-cell">
        <span class="stat-label">Total Expense</span>
        <strong class="stat-value">₹{{ data.overall_expense.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</strong>
      </div>
      <div class="stat-cell">
        <span class="stat-label">Fuel Dispensed</span>
        <strong class="stat-value">{{ data.overall_fuel_dispensed.toFixed(2) }} L</strong>
      </div>
    </div>

    <!-- Period Grouping Tabs (Timeline Options) -->
    <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.5rem">
      <h2 class="section-heading" style="margin: 0">Timeline</h2>

      <div class="ui-tabs-list" role="tablist" aria-label="Group snapshots by">
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

    <!-- Expense Heatmap Component (below Timeline Options) -->
    <ExpenseHeatmap
      v-if="data && data.total_snapshots > 0"
      :mode="groupBy"
      :snapshots="allSnapshots"
      :groups="data.groups"
      @select-period="onHeatmapSelectPeriod"
    />

    <!-- Loading / Error / Empty States -->
    <p v-if="loading" style="padding: 3rem 0; font-size: 0.875rem; color: var(--muted-foreground)">
      Loading fuel snapshots…
    </p>
    <div v-else-if="error" class="notice-banner">
      <p style="margin: 0 0 0.75rem">{{ error }}</p>
      <UiButton variant="outline" size="sm" @click="loadData">Retry</UiButton>
    </div>
    <div v-else-if="!data || data.groups.length === 0" style="padding: 3rem 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border)">
      <p style="margin: 0 0 1rem; font-size: 0.875rem; color: var(--muted-foreground)">
        No fuel snapshots recorded yet.
      </p>
      <RouterLink to="/capture" class="ui-btn ui-btn-outline ui-btn-sm">
        Capture first snapshot
      </RouterLink>
    </div>

    <!-- Period Groups -->
    <div v-else>
      <section
        v-for="group in data.groups"
        :id="'period-' + group.period"
        :key="group.period"
        class="period-block"
      >
        <header class="period-header-bar">
          <div style="display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.75rem">
            <h3 class="period-heading">{{ formatPeriodLabel(group.period, groupBy) }}</h3>
            <span class="period-submeta">
              {{ group.snapshot_count }} {{ group.snapshot_count === 1 ? 'entry' : 'entries' }}
              <template v-if="group.max_odometer > group.min_odometer">
                · {{ (group.max_odometer - group.min_odometer).toFixed(0) }} km span
              </template>
            </span>
          </div>

          <div class="period-summary-pills">
            <span>
              <span style="color: var(--muted-foreground)">Expense </span>
              <strong>₹{{ group.total_expense.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</strong>
            </span>
            <span>
              <span style="color: var(--muted-foreground)">Fuel </span>
              <strong>{{ group.total_fuel_dispensed.toFixed(2) }} L</strong>
            </span>
          </div>
        </header>

        <div class="divide-list">
          <article v-for="snap in group.snapshots" :key="snap.id" class="divide-row">
            <div style="flex: 1; min-width: 240px">
              <div class="row-meta-tags">
                <span style="font-size: 0.8125rem; color: var(--muted-foreground)">
                  {{ formatDateTime(snap.captured_at) }}
                </span>
                <UiBadge variant="outline">{{ entryBadgeLabel(snap.entry_mode) }}</UiBadge>
                <UiBadge v-if="snap.extra_data?.vehicle" variant="secondary">
                  {{ snap.extra_data.vehicle }}
                </UiBadge>
                <UiBadge v-if="snap.extra_data?.station" variant="outline">
                  {{ snap.extra_data.station }}
                </UiBadge>
                <UiBadge v-if="snap.extra_data?.unit_price" variant="outline">
                  ₹{{ snap.extra_data.unit_price }}/L
                </UiBadge>
                <UiBadge v-if="snap.extra_data?.km_per_l" variant="outline">
                  {{ snap.extra_data.km_per_l }} km/L
                </UiBadge>
              </div>

              <div class="row-metrics">
                <div>
                  <span class="row-metric-label">Odometer</span>
                  <span class="row-metric-val">{{ snap.odometer_reading.toFixed(1) }} km</span>
                </div>
                <div>
                  <span class="row-metric-label">Expense</span>
                  <span class="row-metric-val">₹{{ snap.total_expense.toFixed(2) }}</span>
                </div>
                <div>
                  <span class="row-metric-label">Dispensed</span>
                  <span class="row-metric-val">{{ snap.fuel_dispensed.toFixed(2) }} L</span>
                </div>
              </div>

              <p
                v-if="snap.extra_data?.notes"
                style="margin: 0.65rem 0 0; font-size: 0.8125rem; color: var(--muted-foreground)"
              >
                {{ snap.extra_data.notes }}
              </p>
            </div>

            <div class="thumb-group">
              <button
                v-if="snap.odo_image_url"
                type="button"
                class="thumb-card"
                title="View odometer photo"
                @click="openImage(snap.odo_image_url, `Odometer · ${snap.odometer_reading} km`)"
              >
                <img :src="resolveApiUrl(snap.odo_image_url) || ''" alt="Odometer" />
                <span>Odo</span>
              </button>
              <button
                v-if="snap.pump_image_url"
                type="button"
                class="thumb-card"
                title="View fuel pump photo"
                @click="openImage(snap.pump_image_url, `Fuel Pump · ₹${snap.total_expense}`)"
              >
                <img :src="resolveApiUrl(snap.pump_image_url) || ''" alt="Pump" />
                <span>Pump</span>
              </button>

              <UiButton
                variant="ghost"
                size="icon"
                title="Delete snapshot"
                @click="handleDelete(snap.id)"
              >
                <!-- Trash2 Icon (Lucide) -->
                <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
              </UiButton>
            </div>
          </article>
        </div>
      </section>
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
  </section>
</template>
