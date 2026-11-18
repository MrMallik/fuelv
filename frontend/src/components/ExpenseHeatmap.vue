<script setup lang="ts">
import { computed, ref } from 'vue'
import type { FuelSnapshot, GroupByPeriod, SnapshotPeriodGroup } from '../services/api'

export type HeatmapMetric = 'expense' | 'distance'

const props = withDefaults(
  defineProps<{
    mode: GroupByPeriod
    snapshots: FuelSnapshot[]
    groups: SnapshotPeriodGroup[]
    selectedPeriod?: string | null
    metric?: HeatmapMetric
  }>(),
  {
    selectedPeriod: null,
    metric: 'expense',
  }
)

const emit = defineEmits<{
  selectPeriod: [periodKey: string]
}>()

// Active Year filter for multi-year data
const availableYears = computed(() => {
  const years = new Set<number>()
  for (const s of props.snapshots) {
    const y = new Date(s.captured_at).getFullYear()
    if (!isNaN(y)) years.add(y)
  }
  return Array.from(years).sort((a, b) => b - a)
})

const selectedYear = ref<number>(
  availableYears.value.length > 0 ? availableYears.value[0] : new Date().getFullYear()
)

// Watch years if snapshots load later
if (availableYears.value.length > 0 && !availableYears.value.includes(selectedYear.value)) {
  selectedYear.value = availableYears.value[0]
}

// Distance deltas between consecutive logs chronologically
const snapshotDistanceMap = computed(() => {
  const map = new Map<string, number>()
  const chronological = [...props.snapshots].sort(
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

// Tooltip state
interface TooltipData {
  title: string
  subtitle?: string
  expense: number
  fuel: number
  distance: number
  count: number
  x: number
  y: number
}
const tooltip = ref<TooltipData | null>(null)

function showTooltip(e: MouseEvent, data: Omit<TooltipData, 'x' | 'y'>) {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  tooltip.value = {
    ...data,
    x: rect.left + rect.width / 2,
    y: rect.top - 8,
  }
}

function hideTooltip() {
  tooltip.value = null
}

// -------------------------------------------------------------
// 1. DAY VIEW: GitHub-style Calendar Grid (Weeks x 7 Days)
// -------------------------------------------------------------
interface DayCell {
  dateStr: string // YYYY-MM-DD
  dayOfWeek: number // 0=Mon ... 6=Sun
  weekIndex: number
  expense: number
  fuel: number
  distance: number
  count: number
  inYear: boolean
}

const dayGridData = computed(() => {
  const year = selectedYear.value
  const dailyMap = new Map<string, { expense: number; fuel: number; distance: number; count: number }>()

  let maxDaily = 0
  for (const s of props.snapshots) {
    const d = new Date(s.captured_at)
    if (isNaN(d.getTime())) continue
    const dateStr = s.captured_at.slice(0, 10)
    const existing = dailyMap.get(dateStr) || { expense: 0, fuel: 0, distance: 0, count: 0 }
    existing.expense += s.total_expense
    existing.fuel += s.fuel_dispensed
    existing.distance += (snapshotDistanceMap.value.get(s.id) || 0)
    existing.count += 1
    dailyMap.set(dateStr, existing)

    const val = props.metric === 'distance' ? existing.distance : existing.expense
    if (val > maxDaily) maxDaily = val
  }

  // Start from Monday of the week containing Jan 1st of selectedYear
  const jan1 = new Date(year, 0, 1)
  const dec31 = new Date(year, 11, 31)

  // dayOfWeek: Monday=0, Sunday=6
  const jan1Day = (jan1.getDay() + 6) % 7
  const startDate = new Date(jan1)
  startDate.setDate(startDate.getDate() - jan1Day)

  const weeks: DayCell[][] = []
  const monthLabels: { label: string; weekIndex: number }[] = []

  let current = new Date(startDate)
  let currentWeek: DayCell[] = []
  let weekIdx = 0
  let lastMonth = -1

  while (current <= dec31 || currentWeek.length > 0) {
    const dayOfWeek = (current.getDay() + 6) % 7
    const pad = (n: number) => String(n).padStart(2, '0')
    const dateStr = `${current.getFullYear()}-${pad(current.getMonth() + 1)}-${pad(current.getDate())}`
    const inYear = current.getFullYear() === year

    if (inYear && current.getMonth() !== lastMonth && dayOfWeek === 0) {
      lastMonth = current.getMonth()
      monthLabels.push({
        label: current.toLocaleDateString(undefined, { month: 'short' }),
        weekIndex: weekIdx,
      })
    }

    const data = dailyMap.get(dateStr) || { expense: 0, fuel: 0, distance: 0, count: 0 }
    currentWeek.push({
      dateStr,
      dayOfWeek,
      weekIndex: weekIdx,
      expense: data.expense,
      fuel: data.fuel,
      distance: data.distance,
      count: data.count,
      inYear,
    })

    if (dayOfWeek === 6) {
      weeks.push(currentWeek)
      currentWeek = []
      weekIdx++
      if (current > dec31) break
    }

    current.setDate(current.getDate() + 1)
  }

  return { weeks, monthLabels, maxDaily: maxDaily || (props.metric === 'distance' ? 50 : 1000) }
})

// -------------------------------------------------------------
// 2. WEEK VIEW: 52 Weeks Grid with intensity & metric totals
// -------------------------------------------------------------
interface WeekCell {
  weekKey: string // YYYY-Www
  weekNumber: number
  expense: number
  fuel: number
  distance: number
  count: number
  activeDays: number
}

const weekGridData = computed(() => {
  const year = selectedYear.value
  const weekMap = new Map<string, { expense: number; fuel: number; distance: number; count: number; days: Set<string> }>()
  let maxWeek = 0

  for (const s of props.snapshots) {
    const d = new Date(s.captured_at)
    if (isNaN(d.getTime()) || d.getFullYear() !== year) continue

    // Calculate ISO week
    const target = new Date(d.valueOf())
    const dayNr = (d.getDay() + 6) % 7
    target.setDate(target.getDate() - dayNr + 3)
    const firstThursday = target.valueOf()
    target.setMonth(0, 1)
    if (target.getDay() !== 4) {
      target.setMonth(0, 1 + ((4 - target.getDay()) + 7) % 7)
    }
    const weekNumber = 1 + Math.ceil((firstThursday - target.valueOf()) / 604800000)
    const pad = (n: number) => String(n).padStart(2, '0')
    const weekKey = `${year}-W${pad(weekNumber)}`

    const item = weekMap.get(weekKey) || { expense: 0, fuel: 0, distance: 0, count: 0, days: new Set<string>() }
    item.expense += s.total_expense
    item.fuel += s.fuel_dispensed
    item.distance += (snapshotDistanceMap.value.get(s.id) || 0)
    item.count += 1
    item.days.add(s.captured_at.slice(0, 10))
    weekMap.set(weekKey, item)

    const val = props.metric === 'distance' ? item.distance : item.expense
    if (val > maxWeek) maxWeek = val
  }

  const weeksList: WeekCell[] = []
  for (let w = 1; w <= 52; w++) {
    const pad = (n: number) => String(n).padStart(2, '0')
    const weekKey = `${year}-W${pad(w)}`
    const data = weekMap.get(weekKey) || { expense: 0, fuel: 0, distance: 0, count: 0, days: new Set<string>() }
    weeksList.push({
      weekKey,
      weekNumber: w,
      expense: data.expense,
      fuel: data.fuel,
      distance: data.distance,
      count: data.count,
      activeDays: data.days.size,
    })
  }

  return { weeksList, maxWeek: maxWeek || (props.metric === 'distance' ? 150 : 1500) }
})

// -------------------------------------------------------------
// 3. MONTH VIEW: 12-Month Matrix
// -------------------------------------------------------------
interface MonthCell {
  monthKey: string // YYYY-MM
  monthIndex: number // 0-11
  label: string
  expense: number
  fuel: number
  distance: number
  count: number
}

const monthGridData = computed(() => {
  const year = selectedYear.value
  const monthMap = new Map<string, { expense: number; fuel: number; distance: number; count: number }>()
  let maxMonth = 0

  for (const s of props.snapshots) {
    const d = new Date(s.captured_at)
    if (isNaN(d.getTime()) || d.getFullYear() !== year) continue
    const pad = (n: number) => String(n).padStart(2, '0')
    const monthKey = `${year}-${pad(d.getMonth() + 1)}`
    const item = monthMap.get(monthKey) || { expense: 0, fuel: 0, distance: 0, count: 0 }
    item.expense += s.total_expense
    item.fuel += s.fuel_dispensed
    item.distance += (snapshotDistanceMap.value.get(s.id) || 0)
    item.count += 1
    monthMap.set(monthKey, item)

    const val = props.metric === 'distance' ? item.distance : item.expense
    if (val > maxMonth) maxMonth = val
  }

  const months: MonthCell[] = []
  for (let m = 0; m < 12; m++) {
    const pad = (n: number) => String(n).padStart(2, '0')
    const monthKey = `${year}-${pad(m + 1)}`
    const dateObj = new Date(year, m, 1)
    const label = dateObj.toLocaleDateString(undefined, { month: 'short' })
    const data = monthMap.get(monthKey) || { expense: 0, fuel: 0, distance: 0, count: 0 }
    months.push({
      monthKey,
      monthIndex: m,
      label,
      expense: data.expense,
      fuel: data.fuel,
      distance: data.distance,
      count: data.count,
    })
  }

  return { months, maxMonth: maxMonth || (props.metric === 'distance' ? 500 : 3000) }
})

// -------------------------------------------------------------
// 4. YEAR VIEW: Year-over-Year Summary
// -------------------------------------------------------------
const yearGridData = computed(() => {
  const yearMap = new Map<string, { expense: number; fuel: number; distance: number; count: number; activeMonths: Set<number> }>()
  let maxYear = 0

  for (const s of props.snapshots) {
    const d = new Date(s.captured_at)
    if (isNaN(d.getTime())) continue
    const yStr = String(d.getFullYear())
    const item = yearMap.get(yStr) || { expense: 0, fuel: 0, distance: 0, count: 0, activeMonths: new Set<number>() }
    item.expense += s.total_expense
    item.fuel += s.fuel_dispensed
    item.distance += (snapshotDistanceMap.value.get(s.id) || 0)
    item.count += 1
    item.activeMonths.add(d.getMonth())
    yearMap.set(yStr, item)

    const val = props.metric === 'distance' ? item.distance : item.expense
    if (val > maxYear) maxYear = val
  }

  const years = Array.from(yearMap.entries())
    .map(([year, data]) => ({
      year,
      expense: data.expense,
      fuel: data.fuel,
      distance: data.distance,
      count: data.count,
      monthsCount: data.activeMonths.size,
    }))
    .sort((a, b) => Number(b.year) - Number(a.year))

  return { years, maxYear: maxYear || (props.metric === 'distance' ? 5000 : 20000) }
})

// Intensity Level Calculator (0 to 4)
function getHeatLevel(val: number, max: number): number {
  if (!val || val <= 0) return 0
  const ratio = val / max
  if (ratio <= 0.25) return 1
  if (ratio <= 0.5) return 2
  if (ratio <= 0.75) return 3
  return 4
}

function formatCurrency(n: number): string {
  return '₹' + n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}
</script>

<template>
  <div class="heatmap-card">
    <header class="heatmap-header">
      <div>
        <h3 class="heatmap-title">Heatmap</h3>
        <p class="heatmap-subtitle">
          Distribution by
          <span style="font-weight: 500; color: var(--foreground); text-transform: capitalize">{{ mode }}</span>
          <template v-if="mode !== 'year'"> · {{ selectedYear }}</template>
        </p>
      </div>

      <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap">
        <!-- Year Filter Pill (if multi-year) -->
        <div v-if="availableYears.length > 1 && mode !== 'year'" class="ui-tabs-list" style="height: 1.85rem">
          <button
            v-for="y in availableYears"
            :key="y"
            type="button"
            class="ui-tabs-trigger"
            :class="{ active: selectedYear === y }"
            style="padding: 0.15rem 0.6rem; font-size: 0.75rem"
            @click="selectedYear = y"
          >
            {{ y }}
          </button>
        </div>

        <!-- Legend -->
        <div class="heatmap-legend">
          <span>Less</span>
          <span class="heat-box heat-0" :title="metric === 'distance' ? '0 km' : 'No expense'" />
          <span class="heat-box heat-1" :title="metric === 'distance' ? 'Low distance' : 'Low spend'" />
          <span class="heat-box heat-2" :title="metric === 'distance' ? 'Moderate distance' : 'Medium spend'" />
          <span class="heat-box heat-3" :title="metric === 'distance' ? 'High distance' : 'High spend'" />
          <span class="heat-box heat-4" :title="metric === 'distance' ? 'Peak distance' : 'Peak spend'" />
          <span>More</span>
        </div>
      </div>
    </header>

    <!-- MODE 1: DAY (Calendar Grid) -->
    <div v-if="mode === 'day'" class="heatmap-scroll-wrap">
      <div class="calendar-layout">
        <!-- Weekday Labels on Left -->
        <div class="weekday-labels">
          <span>Mon</span>
          <span></span>
          <span>Wed</span>
          <span></span>
          <span>Fri</span>
          <span></span>
          <span>Sun</span>
        </div>

        <div class="calendar-content">
          <!-- Month Labels on Top -->
          <div class="month-label-row">
            <span
              v-for="m in dayGridData.monthLabels"
              :key="m.label + m.weekIndex"
              class="calendar-month-label"
              :style="{ gridColumnStart: m.weekIndex + 1 }"
            >
              {{ m.label }}
            </span>
          </div>

          <!-- 7 Rows x N Weeks Grid -->
          <div class="day-grid">
            <div
              v-for="week in dayGridData.weeks"
              :key="'week-' + week[0]?.weekIndex"
              class="day-col"
            >
              <div
                v-for="cell in week"
                :key="cell.dateStr"
                class="heat-box"
                :class="[
                  !cell.inYear ? 'heat-out-of-range' : `heat-${getHeatLevel(metric === 'distance' ? cell.distance : cell.expense, dayGridData.maxDaily)}`,
                  cell.count > 0 ? 'interactive' : '',
                  selectedPeriod === cell.dateStr ? 'is-selected' : '',
                ]"
                @mouseenter="
                  cell.inYear &&
                  showTooltip($event, {
                    title: new Date(cell.dateStr + 'T12:00:00').toLocaleDateString(undefined, {
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    }),
                    expense: cell.expense,
                    fuel: cell.fuel,
                    distance: cell.distance,
                    count: cell.count,
                  })
                "
                @mouseleave="hideTooltip"
                @click="cell.count > 0 && emit('selectPeriod', cell.dateStr)"
              />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- MODE 2: WEEK (52 Weeks Matrix) -->
    <div v-else-if="mode === 'week'" class="week-heatmap-layout">
      <div class="week-grid">
        <div
          v-for="w in weekGridData.weeksList"
          :key="w.weekKey"
          class="week-heat-cell"
          :class="[
            `heat-${getHeatLevel(metric === 'distance' ? w.distance : w.expense, weekGridData.maxWeek)}`,
            w.count > 0 ? 'interactive' : '',
            selectedPeriod === w.weekKey ? 'is-selected' : '',
          ]"
          @mouseenter="
            showTooltip($event, {
              title: `Week ${w.weekNumber} · ${selectedYear}`,
              subtitle: w.activeDays > 0 ? `${w.activeDays} days with fuel fill` : undefined,
              expense: w.expense,
              fuel: w.fuel,
              distance: w.distance,
              count: w.count,
            })
          "
          @mouseleave="hideTooltip"
          @click="w.count > 0 && emit('selectPeriod', w.weekKey)"
        >
          <span class="week-number-label">{{ w.weekNumber }}</span>
          <template v-if="metric === 'distance'">
            <span v-if="w.distance > 0" class="week-spend-tag">{{ Math.round(w.distance) }} km</span>
          </template>
          <template v-else>
            <span v-if="w.expense > 0" class="week-spend-tag">₹{{ Math.round(w.expense) }}</span>
          </template>
        </div>
      </div>
    </div>

    <!-- MODE 3: MONTH (12-Month Matrix) -->
    <div v-else-if="mode === 'month'" class="month-heatmap-layout">
      <div class="month-grid">
        <div
          v-for="m in monthGridData.months"
          :key="m.monthKey"
          class="month-heat-cell"
          :class="[
            `heat-${getHeatLevel(metric === 'distance' ? m.distance : m.expense, monthGridData.maxMonth)}`,
            m.count > 0 ? 'interactive' : '',
            selectedPeriod === m.monthKey ? 'is-selected' : '',
          ]"
          @mouseenter="
            showTooltip($event, {
              title: `${m.label} ${selectedYear}`,
              expense: m.expense,
              fuel: m.fuel,
              distance: m.distance,
              count: m.count,
            })
          "
          @mouseleave="hideTooltip"
          @click="m.count > 0 && emit('selectPeriod', m.monthKey)"
        >
          <div style="display: flex; align-items: baseline; justify-content: space-between">
            <span class="month-name">{{ m.label }}</span>
            <span v-if="m.count > 0" class="month-fill-count">{{ m.count }} {{ m.count === 1 ? 'fill' : 'fills' }}</span>
          </div>

          <template v-if="metric === 'distance'">
            <strong class="month-spend">
              {{ m.distance > 0 ? `${m.distance.toFixed(1)} km` : '—' }}
            </strong>
            <span class="month-fuel">
              {{ m.expense > 0 ? formatCurrency(m.expense) : 'No fills' }}
            </span>
          </template>
          <template v-else>
            <strong class="month-spend">
              {{ m.expense > 0 ? formatCurrency(m.expense) : '—' }}
            </strong>
            <span class="month-fuel">
              {{ m.fuel > 0 ? `${m.fuel.toFixed(1)} L` : 'No fills' }}
            </span>
          </template>
        </div>
      </div>
    </div>

    <!-- MODE 4: YEAR (Annual Spend / Distance Comparison) -->
    <div v-else-if="mode === 'year'" class="year-heatmap-layout">
      <div class="year-grid">
        <div
          v-for="y in yearGridData.years"
          :key="y.year"
          class="year-heat-cell"
          :class="[
            `heat-${getHeatLevel(metric === 'distance' ? y.distance : y.expense, yearGridData.maxYear)}`,
            'interactive',
            selectedPeriod === String(y.year) ? 'is-selected' : '',
          ]"
          @mouseenter="
            showTooltip($event, {
              title: `Year ${y.year}`,
              subtitle: `${y.monthsCount} active months`,
              expense: y.expense,
              fuel: y.fuel,
              distance: y.distance,
              count: y.count,
            })
          "
          @mouseleave="hideTooltip"
          @click="emit('selectPeriod', y.year)"
        >
          <div style="display: flex; align-items: baseline; justify-content: space-between">
            <span class="year-name">{{ y.year }}</span>
            <span class="year-fill-count">{{ y.count }} fills</span>
          </div>

          <template v-if="metric === 'distance'">
            <strong class="year-spend">{{ y.distance > 0 ? `${y.distance.toFixed(1)} km` : '—' }}</strong>
            <span class="year-fuel">{{ formatCurrency(y.expense) }} · {{ y.fuel.toFixed(1) }} L</span>
          </template>
          <template v-else>
            <strong class="year-spend">{{ formatCurrency(y.expense) }}</strong>
            <span class="year-fuel">{{ y.distance > 0 ? `${y.distance.toFixed(1)} km · ` : '' }}{{ y.fuel.toFixed(1) }} L</span>
          </template>
        </div>
      </div>
    </div>

    <!-- Floating Tooltip -->
    <teleport to="body">
      <div
        v-if="tooltip"
        class="heat-tooltip"
        :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
      >
        <div class="tooltip-title">{{ tooltip.title }}</div>
        <div v-if="tooltip.subtitle" class="tooltip-sub">{{ tooltip.subtitle }}</div>
        <div v-if="tooltip.count > 0" class="tooltip-metrics">
          <template v-if="metric === 'distance'">
            <strong>{{ tooltip.distance.toFixed(1) }} km</strong>
            <span>· {{ formatCurrency(tooltip.expense) }} · {{ tooltip.fuel.toFixed(1) }} L ({{ tooltip.count }} {{ tooltip.count === 1 ? 'fill' : 'fills' }})</span>
          </template>
          <template v-else>
            <strong>{{ formatCurrency(tooltip.expense) }}</strong>
            <span>· {{ tooltip.distance > 0 ? `${tooltip.distance.toFixed(1)} km · ` : '' }}{{ tooltip.fuel.toFixed(2) }} L ({{ tooltip.count }} {{ tooltip.count === 1 ? 'fill' : 'fills' }})</span>
          </template>
        </div>
        <div v-else class="tooltip-sub">No fuel logs recorded</div>
      </div>
    </teleport>
  </div>
</template>

<style scoped>
.heatmap-card {
  border: 1px solid var(--border);
  background: var(--card);
  border-radius: var(--radius);
  padding: 0.85rem 1rem;
  margin-bottom: 1.25rem;
}

@media (min-width: 640px) {
  .heatmap-card {
    padding: 1rem 1.25rem;
  }
}

.heatmap-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.heatmap-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--foreground);
}

.heatmap-subtitle {
  margin: 0.25rem 0 0;
  font-size: 0.75rem;
  color: var(--muted-foreground);
}

.heatmap-legend {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.6875rem;
  color: var(--muted-foreground);
}

.heatmap-scroll-wrap {
  overflow-x: auto;
  padding-bottom: 0.5rem;
  -webkit-overflow-scrolling: touch;
}

/* Day Mode: Calendar Grid */
.calendar-layout {
  display: flex;
  gap: 0.5rem;
  min-width: 740px;
}

.weekday-labels {
  display: grid;
  grid-template-rows: repeat(7, 12px);
  gap: 3px;
  font-size: 0.625rem;
  color: var(--muted-foreground);
  line-height: 12px;
  margin-top: 18px;
  user-select: none;
}

.calendar-content {
  display: flex;
  flex-direction: column;
}

.month-label-row {
  display: grid;
  grid-template-columns: repeat(53, 12px);
  gap: 3px;
  height: 16px;
  margin-bottom: 2px;
}

.calendar-month-label {
  font-size: 0.625rem;
  color: var(--muted-foreground);
  white-space: nowrap;
}

.day-grid {
  display: flex;
  gap: 3px;
}

.day-col {
  display: grid;
  grid-template-rows: repeat(7, 12px);
  gap: 3px;
}

/* Base Heat Cell */
.heat-box {
  width: 12px;
  height: 12px;
  border-radius: 2px;
  transition: transform 120ms ease, outline 120ms ease;
}

.heat-box.interactive {
  cursor: pointer;
}

.heat-box.interactive:hover {
  transform: scale(1.25);
  z-index: 10;
  outline: 1.5px solid var(--foreground);
  outline-offset: 1px;
}

.is-selected {
  outline: 2px solid var(--primary) !important;
  outline-offset: 2px;
}

/* 5 Heat Level Colors (OKLCH green spectrum) */
.heat-0 {
  background: var(--muted);
}
.heat-1 {
  background: oklch(0.85 0.08 145);
}
.heat-2 {
  background: oklch(0.72 0.14 145);
}
.heat-3 {
  background: oklch(0.58 0.18 145);
}
.heat-4 {
  background: oklch(0.42 0.2 145);
}

.dark .heat-0 {
  background: oklch(0.24 0 0);
}
.dark .heat-1 {
  background: oklch(0.35 0.1 145);
}
.dark .heat-2 {
  background: oklch(0.48 0.15 145);
}
.dark .heat-3 {
  background: oklch(0.62 0.18 145);
}
.dark .heat-4 {
  background: oklch(0.75 0.19 145);
}

.heat-out-of-range {
  background: transparent;
  pointer-events: none;
}

/* Week Mode */
.week-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(42px, 1fr));
  gap: 0.4rem;
}

.week-heat-cell {
  aspect-ratio: 1;
  border-radius: calc(var(--radius) - 2px);
  padding: 0.3rem 0.25rem;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  border: 1px solid var(--border);
  transition: transform 120ms ease, border-color 120ms ease;
  user-select: none;
}

.week-heat-cell.interactive {
  cursor: pointer;
}

.week-heat-cell.interactive:hover {
  transform: translateY(-2px);
  border-color: var(--ring);
}

.week-number-label {
  font-size: 0.625rem;
  font-weight: 500;
  color: var(--muted-foreground);
}

.week-spend-tag {
  font-size: 0.625rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

/* Month Mode */
.month-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 0.75rem;
}

.month-heat-cell {
  border-radius: var(--radius);
  border: 1px solid var(--border);
  padding: 0.85rem;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  transition: transform 120ms ease, border-color 120ms ease;
}

.month-heat-cell.interactive {
  cursor: pointer;
}

.month-heat-cell.interactive:hover {
  transform: translateY(-2px);
  border-color: var(--ring);
}

.month-name {
  font-size: 0.8125rem;
  font-weight: 600;
}

.month-fill-count {
  font-size: 0.6875rem;
  color: var(--muted-foreground);
}

.month-spend {
  font-size: 1.0625rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.month-fuel {
  font-size: 0.6875rem;
  color: var(--muted-foreground);
  font-variant-numeric: tabular-nums;
}

/* Year Mode */
.year-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 1rem;
}

.year-heat-cell {
  border-radius: var(--radius);
  border: 1px solid var(--border);
  padding: 1.15rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  transition: transform 120ms ease, border-color 120ms ease;
}

.year-heat-cell.interactive {
  cursor: pointer;
}

.year-heat-cell.interactive:hover {
  transform: translateY(-2px);
  border-color: var(--ring);
}

.year-name {
  font-size: 1.125rem;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.year-fill-count {
  font-size: 0.75rem;
  color: var(--muted-foreground);
}

.year-spend {
  font-size: 1.35rem;
  font-weight: 600;
  letter-spacing: -0.025em;
  font-variant-numeric: tabular-nums;
}

.year-fuel {
  font-size: 0.75rem;
  color: var(--muted-foreground);
  font-variant-numeric: tabular-nums;
}

/* Tooltip */
.heat-tooltip {
  position: fixed;
  z-index: 100;
  transform: translate(-50%, -100%);
  background: var(--popover);
  color: var(--popover-foreground);
  border: 1px solid var(--border);
  border-radius: calc(var(--radius) - 2px);
  padding: 0.45rem 0.65rem;
  font-size: 0.75rem;
  line-height: 1.35;
  box-shadow: 0 4px 6px -1px oklch(0 0 0 / 0.15), 0 2px 4px -2px oklch(0 0 0 / 0.1);
  pointer-events: none;
  white-space: nowrap;
}

.tooltip-title {
  font-weight: 600;
}

.tooltip-sub {
  color: var(--muted-foreground);
}

.tooltip-metrics {
  margin-top: 0.2rem;
  font-variant-numeric: tabular-nums;
}
</style>
