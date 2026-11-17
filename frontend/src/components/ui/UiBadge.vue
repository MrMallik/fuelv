<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'secondary' | 'outline'
  }>(),
  {
    variant: 'secondary',
  }
)
</script>

<template>
  <span class="ui-badge" :class="variant === 'outline' ? 'ui-badge-outline' : ''">
    <slot />
  </span>
</template>
