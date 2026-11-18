<script setup lang="ts">
// UiTableBody — Shadcn Vue-compatible tbody
</script>

<template>
  <tbody class="ui-table-body">
    <slot />
  </tbody>
</template>

<style scoped>
.ui-table-body :deep(tr:last-child) {
  border-bottom: 0;
}
</style>
