import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { ACCEPTED, MAX_BYTES } from '../labels.js'

const ALLOWED = /\.(pdf|png|jpe?g|txt)$/i
// Each file is its own request, so the 4 MB limit holds per file. Two at a time keeps the AI service happy.
export const MAX_BATCH = 10
const AT_ONCE = 2
const RESULT = {
  waiting: ['Waiting', 'muted'],
  reading: ['Reading...', 'muted'],
  added: ['Ready to review', 'ok'],
  duplicate: ['Uploaded before', 'muted'],
  failed: ["Couldn't read it. Type the details in by hand.", 'warn'],
}

function problemWith(file) {
  if (!ALLOWED.test(file.name)) return 'Not a PDF, PNG, JPG or TXT file.'
  if (file.size > MAX_BYTES) return 'Bigger than 4 MB.'
  return ''
}

export default function Upload({ onUploaded = () => {} }) {
  const navigate = useNavigate()
  const input = useRef(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState([])

  function update(index, change) {
    setItems((current) => current.map((item, i) => (i === index ? { ...item, ...change } : item)))
  }

  async function sendOne(file) {
    setBusy(true)
    try {
      const document = await api.upload(file)
      navigate(`/documents/${document.id}`, { state: { duplicate: document.duplicate } })
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  async function runQueue(queue, list) {
    setBusy(true)
    let stopped = ''
    const next = () => queue.shift()
    async function worker() {
      for (let index = next(); index !== undefined; index = next()) {
        if (stopped) {
          update(index, { state: 'error', message: stopped, retry: true })
          continue
        }
        update(index, { state: 'reading', message: '' })
        try {
          const document = await api.upload(list[index].file)
          const state = document.duplicate ? 'duplicate' : document.status === 'failed' ? 'failed' : 'added'
          update(index, { state, document })
          onUploaded()
        } catch (err) {
          // A daily limit won't lift for the rest of the files, so they wait for another day.
          if (err.status === 429) stopped = err.message
          update(index, { state: 'error', message: err.message, retry: err.status !== 413 && err.status !== 415 && err.status !== 400 })
        }
      }
    }
    await Promise.all(Array.from({ length: AT_ONCE }, worker))
    setBusy(false)
  }

  function add(fileList) {
    const files = Array.from(fileList || [])
    if (input.current) input.current.value = ''
    if (!files.length) return
    setError('')
    if (files.length === 1 && !items.length) {
      const problem = problemWith(files[0])
      return problem ? setError(problem === 'Bigger than 4 MB.' ? 'Files can be up to 4 MB.' : 'Upload a PDF, PNG, JPG or TXT file.') : sendOne(files[0])
    }
    const list = files.map((file, index) => {
      const problem = index >= MAX_BATCH ? `Only ${MAX_BATCH} files at a time. Add this one after.` : problemWith(file)
      return { file, name: file.name, state: problem ? 'error' : 'waiting', message: problem }
    })
    setItems(list)
    runQueue(list.flatMap((item, index) => (item.state === 'waiting' ? [index] : [])), list)
  }

  function retry() {
    const list = items.map((item) => (item.state === 'error' && item.retry ? { ...item, state: 'waiting', message: '' } : item))
    setItems(list)
    runQueue(list.flatMap((item, index) => (item.state === 'waiting' ? [index] : [])), list)
  }

  function drop(event) {
    event.preventDefault()
    setDragging(false)
    if (!busy) add(event.dataTransfer.files)
  }

  const done = items.filter((item) => ['added', 'failed', 'duplicate', 'error'].includes(item.state)).length
  // Files uploaded before count too, if they're still waiting for review.
  const toReview = items.find((item) => ['needs_review', 'failed'].includes(item.document?.status))
  const canRetry = !busy && items.some((item) => item.state === 'error' && item.retry)
  let title = 'Drop documents here, or choose files'
  if (busy) title = items.length ? `Reading ${done} of ${items.length}. Each one can take up to half a minute.` : 'Reading the document. This can take up to half a minute.'

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
          multiple
          accept={ACCEPTED}
          className="visually-hidden"
          disabled={busy}
          onChange={(e) => add(e.target.files)}
        />
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" /></svg>
        <span className="dropzone-title" aria-live="polite">{title}</span>
        <span className="dropzone-hint">PDF, PNG, JPG or TXT, up to 4 MB each, {MAX_BATCH} at a time</span>
      </label>
      {error && <p className="banner error" role="alert">{error}</p>}

      {items.length > 0 && (
        <div className="card batch">
          <ul className="batch-list">
            {items.map((item, index) => {
              const [label, tone] = item.state === 'error' ? [item.message, 'error'] : RESULT[item.state]
              return (
                <li key={index}>
                  <span className="batch-name">
                    {item.document ? <Link to={`/documents/${item.document.id}`}>{item.name}</Link> : item.name}
                  </span>
                  <span className={`batch-result ${tone}`}>{label}</span>
                </li>
              )
            })}
          </ul>
          <div className="batch-actions">
            {toReview && !busy && (
              <Link className="button primary" to={`/documents/${toReview.document.id}`}>Start reviewing</Link>
            )}
            {canRetry && <button type="button" className="button" onClick={retry}>Try the others again</button>}
            {!busy && <button type="button" className="link-button" onClick={() => setItems([])}>Clear</button>}
          </div>
        </div>
      )}
    </div>
  )
}
