<script setup lang="ts">
// UiTableHeader — Shadcn Vue-compatible thead
</script>

<template>
  <thead class="ui-table-header">
    <slot />
  </thead>
</template>

<style scoped>
.ui-table-header {
  border-bottom: 1px solid var(--border);
}
</style>
