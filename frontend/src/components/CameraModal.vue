<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
const capturing = ref(false)

// Rear camera device management (fixes Samsung 0.6x ultra-wide default)
const rearDevices = ref<MediaDeviceInfo[]>([])
const activeDeviceId = ref<string | null>(null)

// Hardware capabilities (Zoom & Torch/Flashlight)
const supportsZoom = ref(false)
const minZoom = ref(1)
const maxZoom = ref(1)
const currentZoom = ref(1)
const supportsTorch = ref(false)
const torchEnabled = ref(false)

let activeStream: MediaStream | null = null
let isUnmounted = false
let streamGeneration = 0

const zoomPresets = computed<number[]>(() => {
  if (!supportsZoom.value || maxZoom.value <= 1.05) return []
  const candidates = [1, 1.5, 2, 3]
  return candidates.filter((z) => z >= minZoom.value && z <= maxZoom.value)
})

function isUltraWideLabel(label: string): boolean {
  return /ultra|0\.5|0\.6|wide-angle secondary/i.test(label)
}

function pickBestPrimaryRearCamera(devices: MediaDeviceInfo[]): MediaDeviceInfo | null {
  if (!devices.length) return null
  // 1. Android / Samsung Camera2 HAL convention: "camera2 0, facing back" is always the primary 1.0x sensor
  const cam0 = devices.find((d) => /camera2?\s*0\b.*back/i.test(d.label))
  if (cam0) return cam0

  // 2. First rear camera that is not labeled ultra-wide (0.5x / 0.6x)
  const nonUltra = devices.find((d) => !isUltraWideLabel(d.label))
  if (nonUltra) return nonUltra

  return devices[0]
}

async function discoverRearCameras(currentTrackDeviceId?: string): Promise<string | null> {
  if (!navigator.mediaDevices?.enumerateDevices) return null
  try {
    const all = await navigator.mediaDevices.enumerateDevices()
    const videoInputs = all.filter((d) => d.kind === 'videoinput')
    const backCameras = videoInputs.filter((d) => /back|rear|environment/i.test(d.label))

    rearDevices.value = backCameras
    if (backCameras.length <= 1) {
      return null
    }
    const preferred = pickBestPrimaryRearCamera(backCameras)
    if (preferred && preferred.deviceId && preferred.deviceId !== currentTrackDeviceId) {
      // Check if current track is an ultra-wide or secondary lens on Samsung
      const currentDev = backCameras.find((d) => d.deviceId === currentTrackDeviceId)
      const currentIsPrimary =
        currentDev &&
        (/camera2?\s*0\b.*back/i.test(currentDev.label) || currentDev.deviceId === preferred.deviceId)
      if (!currentIsPrimary) {
        return preferred.deviceId
      }
    }
  } catch {
    // Ignore enumeration errors
  }
  return null
}

async function applyTrackEnhancements(track: MediaStreamTrack) {
  supportsZoom.value = false
  supportsTorch.value = false
  torchEnabled.value = false

  const getCaps = (track as any).getCapabilities
  if (typeof getCaps !== 'function') return

  try {
    const caps = getCaps.call(track) || {}
    const advancedConstraints: Record<string, unknown> = {}

    // Continuous autofocus for sharp odometer & LCD digits
    if (Array.isArray(caps.focusMode) && caps.focusMode.includes('continuous')) {
      advancedConstraints.focusMode = 'continuous'
    }

    // Clamp zoom to at least 1.0x so ultra-wide (<1.0) never stays zoomed out at 0.6x
    if (caps.zoom && typeof caps.zoom.min === 'number' && typeof caps.zoom.max === 'number') {
      supportsZoom.value = caps.zoom.max > caps.zoom.min
      minZoom.value = Math.max(1.0, Number(caps.zoom.min) || 1.0)
      maxZoom.value = Math.min(4.0, Number(caps.zoom.max) || 1.0)
      const targetZoom = Math.min(maxZoom.value, Math.max(minZoom.value, 1.0))
      currentZoom.value = targetZoom
      advancedConstraints.zoom = targetZoom
    }

    if (caps.torch) {
      supportsTorch.value = true
    }

    if (Object.keys(advancedConstraints).length > 0) {
      await track.applyConstraints({ advanced: [advancedConstraints] } as any)
    }
  } catch {
    // Ignore unsupported track constraints
  }
}

async function setZoom(zoomLevel: number) {
  if (!activeStream) return
  const track = activeStream.getVideoTracks()[0]
  if (!track) return
  try {
    await track.applyConstraints({ advanced: [{ zoom: zoomLevel }] } as any)
    currentZoom.value = zoomLevel
  } catch {
    // Fallback if constraint rejected
  }
}

async function toggleTorch() {
  if (!activeStream || !supportsTorch.value) return
  const track = activeStream.getVideoTracks()[0]
  if (!track) return
  const next = !torchEnabled.value
  try {
    await track.applyConstraints({ advanced: [{ torch: next }] } as any)
    torchEnabled.value = next
  } catch {
    // Ignore torch toggle errors
  }
}

async function startCamera(explicitDeviceId: string | null = null, skipAutoSwitch = false) {
  const gen = ++streamGeneration
  stopCamera()
  starting.value = true
  cameraError.value = null

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    cameraError.value =
      'In-browser camera stream is not available here. Tap "Native Camera" below to take the photo.'
    starting.value = false
    return
  }

  try {
    // Request 4:3 sensor aspect ratio (1600x1200) which matches the native 1.0x primary sensor
    // on Samsung/Android phones instead of triggering a 16:9 0.6x ultra-wide crop.
    const videoConstraints: MediaTrackConstraints = explicitDeviceId
      ? {
          deviceId: { exact: explicitDeviceId },
          width: { ideal: 1600 },
          height: { ideal: 1200 },
        }
      : {
          facingMode: { ideal: facingMode.value },
          width: { ideal: 1600 },
          height: { ideal: 1200 },
        }

    const stream = await navigator.mediaDevices.getUserMedia({
      video: videoConstraints,
      audio: false,
    })

    if (isUnmounted || gen !== streamGeneration) {
      for (const track of stream.getTracks()) {
        track.stop()
      }
      return
    }
    activeStream = stream

    const videoTrack = activeStream.getVideoTracks()[0]
    const openedDeviceId = videoTrack?.getSettings?.()?.deviceId || explicitDeviceId || null

    // On first environment open without explicitDeviceId, verify we didn't land on Samsung's 0.6x lens
    if (facingMode.value === 'environment' && !explicitDeviceId && !skipAutoSwitch) {
      const betterMainRearId = await discoverRearCameras(openedDeviceId || undefined)
      if (isUnmounted || gen !== streamGeneration) {
        stopCamera()
        return
      }
      if (betterMainRearId && betterMainRearId !== openedDeviceId) {
        await startCamera(betterMainRearId, true)
        return
      }
    }

    activeDeviceId.value = openedDeviceId
    if (videoTrack) {
      await applyTrackEnhancements(videoTrack)
      if (isUnmounted || gen !== streamGeneration) {
        stopCamera()
        return
      }
    }

    if (videoRef.value) {
      videoRef.value.srcObject = activeStream
      await videoRef.value.play()
    }
  } catch (err: unknown) {
    if (isUnmounted || gen !== streamGeneration) {
      stopCamera()
      return
    }
    // If auto-selected deviceId failed to open, fall back once to generic facingMode
    if (explicitDeviceId && skipAutoSwitch) {
      await startCamera(null, true)
      return
    }
    cameraError.value =
      err instanceof Error
        ? `Could not start live viewfinder (${err.message}). Use the "Native Camera" button below.`
        : 'Could not access camera.'
  } finally {
    if (!isUnmounted && gen === streamGeneration) {
      starting.value = false
    }
  }
}

function stopCamera() {
  if (videoRef.value && videoRef.value.srcObject) {
    videoRef.value.srcObject = null
  }
  if (activeStream) {
    for (const track of activeStream.getTracks()) {
      track.stop()
    }
    activeStream = null
  }
}

function cycleRearLens() {
  if (rearDevices.value.length <= 1) return
  const idx = rearDevices.value.findIndex((d) => d.deviceId === activeDeviceId.value)
  const nextIdx = (idx + 1) % rearDevices.value.length
  const nextDevice = rearDevices.value[nextIdx]
  if (nextDevice?.deviceId) {
    startCamera(nextDevice.deviceId, true)
  }
}

function switchCamera() {
  facingMode.value = facingMode.value === 'environment' ? 'user' : 'environment'
  activeDeviceId.value = null
  startCamera(null)
}

function takeSnapshot() {
  const video = videoRef.value
  if (!video || !video.videoWidth || capturing.value) return

  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    canvas.width = 0
    canvas.height = 0
    return
  }

  capturing.value = true
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  canvas.toBlob(
    (blob) => {
      canvas.width = 0
      canvas.height = 0
      capturing.value = false
      if (!blob || isUnmounted) return
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
  input.value = ''
  if (file) {
    stopCamera()
    emit('capture', file)
  }
}

const currentLensNumber = computed(() => {
  if (rearDevices.value.length <= 1) return 1
  const idx = rearDevices.value.findIndex((d) => d.deviceId === activeDeviceId.value)
  return idx >= 0 ? idx + 1 : 1
})

onMounted(() => {
  startCamera()
})

onBeforeUnmount(() => {
  isUnmounted = true
  streamGeneration++
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

      <div style="padding: 1rem 1.25rem 1.25rem">
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
            Starting 1.0x camera…
          </span>

          <!-- On-Screen Zoom Pills & Torch Controls -->
          <div
            v-if="!starting && (zoomPresets.length > 0 || supportsTorch)"
            style="position: absolute; bottom: 0.65rem; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 0.35rem; background: rgba(9, 9, 11, 0.78); backdrop-filter: blur(6px); padding: 0.25rem 0.45rem; border-radius: 9999px; border: 1px solid rgba(255, 255, 255, 0.15)"
          >
            <button
              v-for="z in zoomPresets"
              :key="z"
              type="button"
              :style="{
                padding: '0.2rem 0.55rem',
                borderRadius: '9999px',
                fontSize: '0.75rem',
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: Math.abs(currentZoom - z) < 0.15 ? '#10b981' : 'transparent',
                color: Math.abs(currentZoom - z) < 0.15 ? '#09090b' : '#fafafa',
              }"
              @click="setZoom(z)"
            >
              {{ z }}x
            </button>

            <button
              v-if="supportsTorch"
              type="button"
              title="Toggle Flashlight / Torch"
              :style="{
                padding: '0.2rem 0.55rem',
                borderRadius: '9999px',
                fontSize: '0.75rem',
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: torchEnabled ? '#f59e0b' : 'transparent',
                color: torchEnabled ? '#09090b' : '#fafafa',
              }"
              @click="toggleTorch"
            >
              {{ torchEnabled ? '⚡ Flash On' : '⚡ Flash' }}
            </button>
          </div>
        </div>

        <div v-else class="notice-banner" style="margin-bottom: 1rem">
          {{ cameraError }}
        </div>

        <div
          style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem; margin-top: 1rem"
        >
          <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 0.45rem">
            <UiButton
              v-if="!cameraError && rearDevices.length > 1 && facingMode === 'environment'"
              variant="outline"
              size="sm"
              title="Switch rear camera lens if wide/telephoto"
              @click="cycleRearLens"
            >
              Lens ({{ currentLensNumber }}/{{ rearDevices.length }})
            </UiButton>

            <UiButton
              v-if="!cameraError"
              variant="outline"
              size="sm"
              @click="switchCamera"
            >
              Flip
            </UiButton>

            <!-- Native mobile camera app fallback (always uses 1x hardware optical lens) -->
            <label class="ui-btn ui-btn-outline ui-btn-sm" style="cursor: pointer">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                class="sr-only"
                @change="onNativeCameraInput"
              />
              Native Camera App
            </label>
          </div>

          <div style="display: flex; align-items: center; gap: 0.5rem">
            <UiButton variant="ghost" size="sm" @click="emit('close')">
              Cancel
            </UiButton>
            <UiButton
              v-if="!cameraError"
              variant="default"
              :disabled="starting || capturing"
              @click="takeSnapshot"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>
              Capture photo
            </UiButton>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
