<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import ThemeToggle from './components/ThemeToggle.vue'
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
