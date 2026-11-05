import { useEffect, useRef, useState } from 'react'
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { rangesIn } from '../highlight.js'
import { FIELD_LABELS } from '../labels.js'

GlobalWorkerOptions.workerSrc = workerUrl

const MAX_PAGES = 10
// Rendered sharp enough for a wide pane on a high-density screen, then scaled down by CSS.
const RENDER_WIDTH = 1400

// Boxes, as fractions of the page, around each piece of text a field was read from.
function boxesFor(items, viewport, marks) {
  let text = ''
  const starts = []
  for (const item of items) {
    starts.push(text.length)
    text += item.str + '\n'
  }
  const boxes = []
  for (const range of rangesIn(text, marks)) {
    items.forEach((item, index) => {
      const from = Math.max(range.start, starts[index])
      const to = Math.min(range.end, starts[index] + item.str.length)
      if (from >= to || !item.str.length) return
      const [, , c, d, x, y] = item.transform
      const height = item.height || Math.hypot(c, d)
      // A part of an item is sized by its share of the characters, which is close enough for a highlight.
      const left = x + (item.width * (from - starts[index])) / item.str.length
      const right = x + (item.width * (to - starts[index])) / item.str.length
      const [x1, y1, x2, y2] = viewport.convertToViewportRectangle([left, y - height * 0.22, right, y + height * 0.9])
      boxes.push({
        key: range.key,
        left: Math.min(x1, x2) / viewport.width,
        top: Math.min(y1, y2) / viewport.height,
        width: Math.abs(x2 - x1) / viewport.width,
        height: Math.abs(y2 - y1) / viewport.height,
      })
    })
  }
  // Fields read from the same text, such as first and last name, share one box.
  const merged = new Map()
  for (const { key, ...box } of boxes) {
    const spot = [box.left, box.top, box.width, box.height].map((n) => n.toFixed(4)).join()
    if (merged.has(spot)) merged.get(spot).keys.push(key)
    else merged.set(spot, { ...box, keys: [key] })
  }
  return [...merged.values()]
}

function Page({ page, marks, active, onPick }) {
  const canvas = useRef(null)
  const [boxes, setBoxes] = useState([])
  const [size, setSize] = useState(null)

  const [content, setContent] = useState(null)

  useEffect(() => {
    let cancelled = false
    const base = page.getViewport({ scale: 1 })
    const viewport = page.getViewport({ scale: RENDER_WIDTH / base.width })
    const element = canvas.current
    element.width = Math.round(viewport.width)
    element.height = Math.round(viewport.height)
    setSize({ width: viewport.width, height: viewport.height })
    const task = page.render({ canvasContext: element.getContext('2d'), viewport })
    task.promise.catch(() => {})
    page.getTextContent().then((text) => {
      if (!cancelled) setContent({ items: text.items.filter((item) => 'str' in item), viewport })
    })
    return () => {
      cancelled = true
      task.cancel()
    }
  }, [page])

  // Worked out again after a save, without drawing the page again.
  useEffect(() => {
    if (content) setBoxes(boxesFor(content.items, content.viewport, marks))
  }, [content, marks])

  return (
    <div className="pdf-page" style={size ? { aspectRatio: `${size.width} / ${size.height}` } : undefined}>
      <canvas ref={canvas} aria-hidden="true" />
      {boxes.map((box, index) => (
        <span
          key={index}
          className={`hl${box.keys.includes(active) ? ' active' : ''}`}
          style={{ left: `${box.left * 100}%`, top: `${box.top * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }}
          title={box.keys.map((key) => FIELD_LABELS[key]).join(', ')}
          aria-hidden="true"
          onClick={() => onPick(box.keys[0])}
        />
      ))}
    </div>
  )
}

export default function PdfPages({ data, marks, active, onPick, onReady, onError }) {
  const [pages, setPages] = useState([])
  const [total, setTotal] = useState(0)

  useEffect(() => {
    let cancelled = false
    // A copy, because pdf.js hands the buffer to its worker and the original would be emptied.
    const task = getDocument({ data: data.slice(0), isEvalSupported: false, useSystemFonts: true })
    task.promise
      .then(async (pdf) => {
        const count = Math.min(pdf.numPages, MAX_PAGES)
        const loaded = []
        for (let n = 1; n <= count; n++) loaded.push(await pdf.getPage(n))
        if (cancelled) return
        setTotal(pdf.numPages)
        setPages(loaded)
        onReady()
      })
      .catch(() => !cancelled && onError())
    return () => {
      cancelled = true
      task.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  return (
    <div className="pdf-pages">
      {pages.map((page) => (
        <Page key={page.pageNumber} page={page} marks={marks} active={active} onPick={onPick} />
      ))}
      {total > MAX_PAGES && <p className="muted original-note">Showing the first {MAX_PAGES} of {total} pages.</p>}
    </div>
  )
}
