<script setup lang="ts">
import { ref } from 'vue'
import { getStoredWriteKey, setStoredWriteKey } from '../services/api'
import UiButton from './ui/UiButton.vue'
import UiInput from './ui/UiInput.vue'

const emit = defineEmits<{
  close: []
  saved: [key: string]
}>()

const currentKey = ref(getStoredWriteKey())
const inputKey = ref(currentKey.value)
const savedMessage = ref<string | null>(null)

function save() {
  const trimmed = inputKey.value.trim()
  setStoredWriteKey(trimmed)
  currentKey.value = trimmed
  savedMessage.value = trimmed ? 'Access key saved.' : 'Access key cleared.'
  setTimeout(() => {
    savedMessage.value = null
    emit('saved', trimmed)
    emit('close')
  }, 600)
}

function clear() {
  inputKey.value = ''
  setStoredWriteKey('')
  currentKey.value = ''
  savedMessage.value = 'Access key cleared.'
  setTimeout(() => {
    savedMessage.value = null
    emit('saved', '')
    emit('close')
  }, 600)
}
</script>

<template>
  <div class="dialog-overlay" @click="emit('close')">
    <div class="dialog-content" style="max-width: 28rem" @click.stop>
      <header class="dialog-header">
        <span>Admin Access Key</span>
        <UiButton variant="ghost" size="icon" @click="emit('close')">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </UiButton>
      </header>

      <div style="padding: 1.25rem">
        <p style="margin: 0 0 1rem; font-size: 0.8125rem; color: var(--muted-foreground); line-height: 1.5">
          To protect your Gemini API quota and prevent unauthorized writes, enter your secret UUID write key (configured in <code style="background: var(--muted); padding: 0.1rem 0.3rem; border-radius: 3px">FUELV_WRITE_KEY</code>). It is stored securely in this browser.
        </p>

        <div style="margin-bottom: 1.25rem">
          <label for="uuid-input" style="display: block; font-size: 0.8125rem; font-weight: 500; margin-bottom: 0.35rem">
            Access Key (UUID)
          </label>
          <UiInput
            id="uuid-input"
            v-model="inputKey"
            placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
            @keydown.enter="save"
          />
        </div>

        <div v-if="savedMessage" class="notice-banner" style="margin-bottom: 1rem; color: #10b981">
          {{ savedMessage }}
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.5rem">
          <UiButton v-if="currentKey" variant="ghost" size="sm" @click="clear">
            Clear key
          </UiButton>
          <div v-else />

          <div style="display: flex; align-items: center; gap: 0.5rem">
            <UiButton variant="outline" size="sm" @click="emit('close')">
              Cancel
            </UiButton>
            <UiButton variant="default" size="sm" @click="save">
              Save key
            </UiButton>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
