<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import ThemeToggle from './components/ThemeToggle.vue'
import UiBadge from './components/ui/UiBadge.vue'
import { fetchHealth, type HealthStatus } from './services/api'

const health = ref<HealthStatus | null>(null)

onMounted(async () => {
  try {
    health.value = await fetchHealth()
  } catch {
    health.value = null
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <div class="site-header-inner">
        <div class="brand-group">
          <a href="/" class="brand-link text-link" title="Back to Portfolio">Portfolio</a>
          <span class="brand-sep">/</span>
          <RouterLink to="/" class="brand-link">FuelV</RouterLink>
        </div>

        <nav aria-label="FuelV navigation" class="nav-group">
          <RouterLink to="/" class="nav-item" exact-active-class="active">
            Snapshots
          </RouterLink>
          <RouterLink to="/capture" class="nav-item" active-class="active">
            Capture
          </RouterLink>
        </nav>

        <div class="nav-group">
          <UiBadge v-if="health" variant="outline" :title="`Extractor: ${health.extractor_backend}`">
            {{ health.extractor_model }}
          </UiBadge>
          <ThemeToggle />
        </div>
      </div>
    </header>

    <main class="page-container">
      <RouterView />
    </main>

    <footer class="site-footer">
      <div class="site-footer-inner">
        <p>© {{ new Date().getFullYear() }} FuelV · Snapshot Fuel Log</p>
        <nav aria-label="Footer navigation" class="nav-group" style="gap: 1.25rem">
          <a href="/" class="text-link">Portfolio</a>
          <RouterLink to="/" class="text-link">Logs</RouterLink>
          <RouterLink to="/capture" class="text-link">New Snapshot</RouterLink>
        </nav>
      </div>
    </footer>
  </div>
</template>
