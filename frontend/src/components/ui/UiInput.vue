<script setup lang="ts">
defineProps<{
  modelValue: string | number
  id?: string
  type?: string
  step?: string
  min?: string
  placeholder?: string
  required?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <input
    :id="id"
    :type="type || 'text'"
    :value="modelValue"
    :step="step"
    :min="min"
    :placeholder="placeholder"
    :required="required"
    class="ui-input"
    @input="onInput"
  />
</template>
