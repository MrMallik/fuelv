<script setup lang="ts">
// UiTableCell — Shadcn Vue-compatible td
</script>

<template>
  <td class="ui-table-cell">
    <slot />
  </td>
</template>

<style scoped>
.ui-table-cell {
  padding: 0.5rem 0.65rem;
  vertical-align: middle;
  font-size: 0.8125rem;
  color: var(--foreground);
}
</style>
