<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import UiButton from './ui/UiButton.vue'

const props = defineProps<{
  title: string
  filenamePrefix: string
}>()

const emit = defineEmits<{
  capture: [file: File]
  close: []
}>()

const videoRef = ref<HTMLVideoElement | null>(null)
const facingMode = ref<'environment' | 'user'>('environment')
const cameraError = ref<string | null>(null)
const starting = ref(true)
let activeStream: MediaStream | null = null

async function startCamera() {
  stopCamera()
  starting.value = true
  cameraError.value = null

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    cameraError.value = 'Camera API is not available in this browser context. Use the Native Camera button below.'
    starting.value = false
    return
  }

  try {
    activeStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: facingMode.value },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    })
    if (videoRef.value) {
      videoRef.value.srcObject = activeStream
      await videoRef.value.play()
    }
  } catch (err: unknown) {
    cameraError.value =
      err instanceof Error
        ? `Could not access camera (${err.message}). You can use Native Camera or Select File instead.`
        : 'Could not access camera.'
  } finally {
    starting.value = false
  }
}

function stopCamera() {
  if (activeStream) {
    for (const track of activeStream.getTracks()) {
      track.stop()
    }
    activeStream = null
  }
}

function switchCamera() {
  facingMode.value = facingMode.value === 'environment' ? 'user' : 'environment'
  startCamera()
}

function takeSnapshot() {
  const video = videoRef.value
  if (!video || !video.videoWidth) return

  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  canvas.toBlob(
    (blob) => {
      if (!blob) return
      const file = new File([blob], `${props.filenamePrefix}_${Date.now()}.jpg`, {
        type: 'image/jpeg',
      })
      stopCamera()
      emit('capture', file)
    },
    'image/jpeg',
    0.92
  )
}

function onNativeCameraInput(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    stopCamera()
    emit('capture', file)
  }
}

onMounted(() => {
  startCamera()
})

onBeforeUnmount(() => {
  stopCamera()
})
</script>

<template>
  <div class="dialog-overlay" @click="emit('close')">
    <div class="dialog-content" @click.stop>
      <header class="dialog-header">
        <span>{{ title }}</span>
        <UiButton variant="ghost" size="icon" @click="emit('close')">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </UiButton>
      </header>

      <div style="padding: 1.25rem">
        <div
          v-if="!cameraError"
          style="position: relative; background: oklch(0.1 0 0); border-radius: calc(var(--radius) - 2px); overflow: hidden; min-height: 280px; display: flex; align-items: center; justify-content: center"
        >
          <video
            ref="videoRef"
            autoplay
            playsinline
            muted
            style="width: 100%; max-height: 60vh; object-fit: contain; display: block"
          />
          <span
            v-if="starting"
            style="position: absolute; font-size: 0.8125rem; color: var(--muted-foreground)"
          >
            Starting camera…
          </span>
        </div>

        <div v-else class="notice-banner" style="margin-bottom: 1rem">
          {{ cameraError }}
        </div>

        <div
          style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem; margin-top: 1.25rem"
        >
          <div style="display: flex; align-items: center; gap: 0.5rem">
            <UiButton
              v-if="!cameraError"
              variant="outline"
              size="sm"
              @click="switchCamera"
            >
              Flip camera
            </UiButton>

            <!-- Native mobile camera fallback -->
            <label class="ui-btn ui-btn-outline ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                class="sr-only"
                @change="onNativeCameraInput"
              />
              Device camera
            </label>
          </div>

          <div style="display: flex; align-items: center; gap: 0.5rem">
            <UiButton variant="ghost" size="sm" @click="emit('close')">
              Cancel
            </UiButton>
            <UiButton
              v-if="!cameraError"
              variant="default"
              :disabled="starting"
              @click="takeSnapshot"
            >
              <!-- Camera shutter icon -->
              <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
              Capture photo
            </UiButton>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
