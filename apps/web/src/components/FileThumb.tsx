import { useEffect, useState } from 'react'
import { loadFile } from '@/api/jobs'
import { blobToDataUrl } from '@/lib/image'

/** Show a stored image file of a job, loaded through the API client; empty until it arrives. */
export function FileThumb({ id, alt, className }: { id: string; alt: string; className?: string }) {
  const [src, setSrc] = useState<string>()
  useEffect(() => {
    let live = true
    // The page allows only 'self' and data: images (see index.html), so a blob: URL would not load.
    loadFile(id)
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
