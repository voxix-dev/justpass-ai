import { useEffect, useState } from 'react'
import { loadPhoto } from '@/api/jobs'

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

/** Show a stored photo of a job, loaded through the API client; empty until it arrives. */
export function PhotoImg({ id, alt, className }: { id: string; alt: string; className?: string }) {
  const [src, setSrc] = useState<string>()
  useEffect(() => {
    let live = true
    // The page allows only 'self' and data: images (see index.html), so a blob: URL would not load.
    loadPhoto(id)
      .then((blob) => blobToDataUrl(blob))
      .then((url) => {
        if (live) setSrc(url)
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [id])
  return src ? <img src={src} alt={alt} className={className} /> : <div role="img" aria-label={alt} className={className} />
}
