<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'default' | 'secondary' | 'outline' | 'ghost' | 'destructive'
    size?: 'default' | 'sm' | 'lg' | 'icon'
    type?: 'button' | 'submit' | 'reset'
    disabled?: boolean
  }>(),
  {
    variant: 'default',
    size: 'default',
    type: 'button',
    disabled: false,
  }
)
</script>

<template>
  <button
    :type="type"
    :disabled="disabled"
    class="ui-btn"
    :class="[
      `ui-btn-${variant}`,
      size !== 'default' ? `ui-btn-${size}` : '',
    ]"
  >
    <slot />
  </button>
</template>
