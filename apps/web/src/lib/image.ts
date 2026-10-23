/**
 * Shrinks a photo to a JPEG with the long edge at most `maxEdge` px, honoring EXIF rotation.
 * Smaller photos are not enlarged. quality is passed to the browser as a JPEG quality hint
 * from 0 to 1. Decoding and canvas errors reject the promise; a null encoding result
 * rejects with Error("Could not encode photo").
 */
export async function shrinkImage(file: Blob, maxEdge = 1600, quality = 0.8): Promise<Blob> {
  const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' })
  const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bmp.width * scale)
  canvas.height = Math.round(bmp.height * scale)
  canvas.getContext('2d')!.drawImage(bmp, 0, 0, canvas.width, canvas.height)
  bmp.close()
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not encode photo'))), 'image/jpeg', quality),
  )
}
