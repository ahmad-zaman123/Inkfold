import { Suspense, lazy, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'
import { rangesIn, scrollToActive, segments } from '../highlight.js'
import { FIELD_LABELS } from '../labels.js'

// pdf.js is large, so it only loads when a PDF is opened.
const PdfPages = lazy(() => import('./PdfPages.jsx'))
const WIDE = '(min-width: 1200px)'
const ZOOMS = [1, 1.5, 2, 3]
const OPEN_KEY = 'inkfold_original_open'

// Open on wide screens unless the reviewer closed it before; on narrow ones it starts closed.
function startOpen() {
  try {
    const saved = localStorage.getItem(OPEN_KEY)
    if (saved !== null && window.matchMedia(WIDE).matches) return saved === '1'
  } catch {
    // Without storage it just uses the default.
  }
  return window.matchMedia(WIDE).matches
}

function TextOriginal({ text, marks, active, onPick }) {
  const runs = useMemo(() => segments(text, rangesIn(text, marks)), [text, marks])
  return (
    <pre className="original-text">
      {runs.map((run, index) =>
        run.keys.length ? (
          <mark
            key={index}
            className={`hl${run.keys.includes(active) ? ' active' : ''}`}
            title={run.keys.map((key) => FIELD_LABELS[key]).join(', ')}
            onClick={() => onPick(run.keys[0])}
          >
            {run.text}
          </mark>
        ) : (
          run.text
        ),
      )}
    </pre>
  )
}

export default function Original({ document, marks, active, scrollKey, onPick }) {
  const [open, setOpenState] = useState(startOpen)
  const setOpen = (next) => {
    setOpenState(next)
    try {
      localStorage.setItem(OPEN_KEY, next ? '1' : '0')
    } catch {
      // Remembering is only a convenience.
    }
  }
  const [state, setState] = useState({ status: 'loading' })
  const pane = useRef(null)
  const made = useRef(null)
  const [zoom, setZoom] = useState(0)
  const kind = document.mime === 'application/pdf' ? 'pdf' : document.mime === 'text/plain' ? 'text' : 'image'

  useEffect(() => {
    if (!open || state.status !== 'loading') return undefined
    let current = true
    const link = (blob) => (made.current = URL.createObjectURL(blob))
    api.file(document.id)
      .then(async (blob) => {
        if (!current) return
        if (kind === 'text') setState({ status: 'ready', text: await blob.text() })
        else if (kind === 'pdf') setState({ status: 'pdf', data: new Uint8Array(await blob.arrayBuffer()), url: link(blob) })
        else setState({ status: 'ready', url: link(blob) })
      })
      .catch((err) => current && setState({ status: 'error', message: err.status === 404 ? err.message : "The original couldn't be loaded." }))
    return () => {
      current = false
    }
    // Loaded once per document; the page keys this component by document.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => () => made.current && URL.revokeObjectURL(made.current), [])

  useLayoutEffect(() => {
    if (scrollKey) scrollToActive(pane.current)
  }, [scrollKey])

  const url = state.url
  return (
    <section className={`card original${open ? ' open' : ''}`} aria-label="Original document">
      <div className="original-head">
        <h2>Original</h2>
        <div className="original-actions">
          {open && kind !== 'text' && state.status !== 'error' && (
            <span className="zoom" role="group" aria-label="Zoom">
              <button type="button" className="link-button" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>−</button>
              <span aria-live="polite">{ZOOMS[zoom] * 100}%</span>
              <button type="button" className="link-button" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}>+</button>
            </span>
          )}
          {open && url && (
            <a href={url} target="_blank" rel="noreferrer" className="link-button">Open full size</a>
          )}
          <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      {open && (
        <>
          <p className="original-note muted">
            {kind === 'image'
              ? 'Compare the fields with the picture. Highlights work on PDFs and text, not images.'
              : 'Highlights show where each field was read. Click one to jump to its field.'}
          </p>
          <div className="original-body" ref={pane} style={{ '--zoom': ZOOMS[zoom] }}>
            {state.status === 'loading' && <p className="muted" aria-busy="true">Loading the original...</p>}
            {state.status === 'error' && <p className="muted">{state.message}</p>}
            {state.status === 'ready' && kind === 'text' && (
              <TextOriginal text={state.text} marks={marks} active={active} onPick={onPick} />
            )}
            {state.status === 'ready' && kind === 'image' && <img src={url} alt={`The uploaded file, ${document.filename}`} />}
            {state.status === 'pdf' && (
              <Suspense fallback={<p className="muted" aria-busy="true">Loading the original...</p>}>
                <PdfPages
                  data={state.data}
                  marks={marks}
                  active={active}
                  onPick={onPick}
                  onReady={() => {}}
                  onError={() => setState({ status: 'error', message: "This PDF couldn't be shown here. Use Open full size.", url })}
                />
              </Suspense>
            )}
          </div>
        </>
      )}
    </section>
  )
}
