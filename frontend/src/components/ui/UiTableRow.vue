<script setup lang="ts">
// UiTableRow — Shadcn Vue-compatible tr
</script>

<template>
  <tr class="ui-table-row">
    <slot />
  </tr>
</template>

<style scoped>
.ui-table-row {
  border-bottom: 1px solid var(--border);
  transition: background-color 0.15s ease;
}

.ui-table-row:hover {
  background-color: var(--muted);
}
</style>
