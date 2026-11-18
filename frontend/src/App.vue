<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import KeySettingsModal from './components/KeySettingsModal.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import UiButton from './components/ui/UiButton.vue'
import logoUrl from './assets/logo/fuelv-logo-128.png'

const showKeyModal = ref(false)
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <div class="site-header-inner">
        <!-- App Brand on the left -->
        <div class="brand-group">
          <RouterLink to="/" class="brand-link" title="FuelV — The Visual Fuel Logger">
            <img :src="logoUrl" alt="FuelV Logo" class="brand-logo-img" />
            <span class="brand-name">FuelV</span>
            <span class="brand-tagline">The Visual Fuel Logger</span>
          </RouterLink>
        </div>

        <!-- Navigation and Actions on the right -->
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

    <!-- Access Key Modal -->
    <KeySettingsModal v-if="showKeyModal" @close="showKeyModal = false" />
  </div>
</template>
