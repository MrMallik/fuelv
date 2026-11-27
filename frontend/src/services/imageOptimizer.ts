/**
 * Client-side image compression and normalization for odometer and fuel pump snapshots.
 * Shrinks 6-12 MB smartphone camera photos down to ~200-350 KB (max 1600px at 0.88 JPEG quality),
 * keeping digits razor-sharp for VLM extraction while making 4G/5G uploads at fuel stations 20x faster.
 */

const MAX_DIMENSION = 1600
const JPEG_QUALITY = 0.88
const PASSTHROUGH_MAX_BYTES = 450 * 1024 // 450 KB

export async function optimizeSnapshotImage(file: File, prefix = 'snapshot'): Promise<File> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return file
  }

  // If already a compact JPEG/WebP/PNG under 450 KB, skip re-encoding
  if (
    file.size <= PASSTHROUGH_MAX_BYTES &&
    ['image/jpeg', 'image/png', 'image/webp'].includes(file.type)
  ) {
    return file
  }

  let canvas: HTMLCanvasElement | null = null
  try {
    const bitmap = await loadImageElement(file)
    const origW = bitmap.naturalWidth || bitmap.width
    const origH = bitmap.naturalHeight || bitmap.height
    if (!origW || !origH) {
      return file
    }

    let targetW = origW
    let targetH = origH
    if (origW > MAX_DIMENSION || origH > MAX_DIMENSION) {
      if (origW >= origH) {
        targetW = MAX_DIMENSION
        targetH = Math.round((origH * MAX_DIMENSION) / origW)
      } else {
        targetH = MAX_DIMENSION
        targetW = Math.round((origW * MAX_DIMENSION) / origH)
      }
    }

    canvas = document.createElement('canvas')
    canvas.width = targetW
    canvas.height = targetH
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      return file
    }

    ctx.imageSmoothingEnabled = true
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(bitmap, 0, 0, targetW, targetH)

    const blob = await new Promise<Blob | null>((resolve) => {
      canvas!.toBlob((b) => resolve(b), 'image/jpeg', JPEG_QUALITY)
    })

    if (!blob || blob.size >= file.size) {
      return file
    }

    const baseName = file.name ? file.name.replace(/\.[^.]+$/, '') : `${prefix}_${Date.now()}`
    return new File([blob], `${baseName}.jpg`, {
      type: 'image/jpeg',
      lastModified: Date.now(),
    })
  } catch {
    // Graceful fallback: return original untouched file if browser cannot decode format (e.g. raw HEIC)
    return file
  } finally {
    if (canvas) {
      canvas.width = 0
      canvas.height = 0
    }
  }
}

function loadImageElement(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = (err) => {
      URL.revokeObjectURL(url)
      reject(err)
    }
    img.src = url
  })
}
