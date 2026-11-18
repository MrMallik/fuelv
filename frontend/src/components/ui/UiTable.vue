<script setup lang="ts">
// UiTable — Shadcn Vue-compatible responsive table wrapper
</script>

<template>
  <div class="ui-table-wrapper">
    <table class="ui-table">
      <slot />
    </table>
  </div>
</template>

<style scoped>
.ui-table-wrapper {
  position: relative;
  width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.ui-table {
  width: 100%;
  caption-side: bottom;
  font-size: 0.8125rem;
  border-collapse: collapse;
  text-align: left;
}
</style>
