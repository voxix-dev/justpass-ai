import { useEffect, useState } from 'react'
import { Download, LoaderCircle } from 'lucide-react'
import { loadFile } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { blobToDataUrl } from '@/lib/image'

type StoredFile = { id: string; name: string }

/** A button that downloads a stored file through the API client, so the request carries the bearer token. */
export function DownloadButton({ file }: { file: StoredFile }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function download() {
    setBusy(true)
    setFailed(false)
    try {
      const url = URL.createObjectURL(await loadFile(file.id))
      const link = document.createElement('a')
      link.href = url
      link.download = file.name
      link.click()
      // Safari starts the download after the click returns, so the URL must outlive it.
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-full">
      <Button type="button" variant="outline" onClick={() => !busy && void download()} aria-disabled={busy} className="max-w-full">
        {busy ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
        <span className="truncate">{file.name}</span>
      </Button>
      {failed && <p role="alert" className="mt-1 text-sm font-bold text-coral">Could not download this file. Try again.</p>}
    </div>
  )
}

/** Show a stored image file of a job, loaded through the API client; a download button if it cannot be shown. Key it by file. */
export function FileThumb({ file, alt, className }: { file: StoredFile; alt: string; className?: string }) {
  const [src, setSrc] = useState<string>()
  const [failed, setFailed] = useState(false)
  const { id } = file
  useEffect(() => {
    let live = true
    // The page allows only 'self' and data: images (see index.html), so a blob: URL would not load.
    loadFile(id)
      .then((blob) => blobToDataUrl(blob))
      .then((url) => {
        if (live) setSrc(url)
      })
      .catch(() => {
        if (live) setFailed(true)
      })
    return () => {
      live = false
    }
  }, [id])
  if (failed) return <DownloadButton file={file} />
  return src ? <img src={src} alt={alt} className={className} onError={() => setFailed(true)} /> : <div role="img" aria-label={alt} className={className} />
}
