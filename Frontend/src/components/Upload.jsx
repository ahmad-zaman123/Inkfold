import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { ACCEPTED, MAX_BYTES } from '../labels.js'

const ALLOWED = /\.(pdf|png|jpe?g|txt)$/i

export default function Upload() {
  const navigate = useNavigate()
  const input = useRef(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')

  async function send(file) {
    if (!file) return
    setError('')
    if (!ALLOWED.test(file.name)) return setError('Upload a PDF, PNG, JPG or TXT file.')
    if (file.size > MAX_BYTES) return setError('Files can be up to 4 MB.')
    setBusy(true)
    try {
      const document = await api.upload(file)
      navigate(`/documents/${document.id}`, { state: { duplicate: document.duplicate } })
    } catch (err) {
      setError(err.message)
      setBusy(false)
    } finally {
      if (input.current) input.current.value = ''
    }
  }

  function drop(event) {
    event.preventDefault()
    setDragging(false)
    if (!busy) send(event.dataTransfer.files[0])
  }

  return (
    <div>
      <label
        className={`dropzone${dragging ? ' dragging' : ''}${busy ? ' busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
      >
        <input
          ref={input}
          type="file"
          accept={ACCEPTED}
          className="visually-hidden"
          disabled={busy}
          onChange={(e) => send(e.target.files[0])}
        />
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></svg>
        <span className="dropzone-title" aria-live="polite">
          {busy ? 'Reading the document. This can take up to half a minute.' : 'Drop a document here, or choose a file'}
        </span>
        <span className="dropzone-hint">PDF, PNG, JPG or TXT, up to 4 MB</span>
      </label>
      {error && <p className="banner error" role="alert">{error}</p>}
    </div>
  )
}
