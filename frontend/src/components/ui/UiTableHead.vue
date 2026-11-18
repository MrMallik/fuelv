<script setup lang="ts">
// UiTableHead — Shadcn Vue-compatible th
</script>

<template>
  <th class="ui-table-head">
    <slot />
  </th>
</template>

<style scoped>
.ui-table-head {
  height: 2.15rem;
  padding: 0 0.65rem;
  text-align: left;
  vertical-align: middle;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--muted-foreground);
  white-space: nowrap;
}
</style>
