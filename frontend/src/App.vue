<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import KeySettingsModal from './components/KeySettingsModal.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import UiButton from './components/ui/UiButton.vue'

const showKeyModal = ref(false)
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
          <UiButton
            variant="ghost"
            size="icon"
            title="Configure Admin Access Key"
            aria-label="Admin Access Key"
            @click="showKeyModal = true"
          >
            <!-- Key Round Icon (Lucide) -->
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/></svg>
          </UiButton>
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

    <!-- Access Key Modal -->
    <KeySettingsModal v-if="showKeyModal" @close="showKeyModal = false" />
  </div>
</template>
