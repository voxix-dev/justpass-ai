import { useEffect, useState } from 'react'
import { loadPhoto } from '@/api/jobs'

/** Show a stored photo of a job, loaded through the API client; empty until it arrives. */
export function PhotoImg({ id, alt, className }: { id: string; alt: string; className?: string }) {
  const [src, setSrc] = useState<string>()
  useEffect(() => {
    let url: string | undefined
    let live = true
    loadPhoto(id)
      .then((blob) => {
        if (!live) return
        url = URL.createObjectURL(blob)
        setSrc(url)
      })
      .catch(() => undefined)
    return () => {
      live = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [id])
  return src ? <img src={src} alt={alt} className={className} /> : <div role="img" aria-label={alt} className={className} />
}
