// Finds where each field was read in the original's text, so the reviewer can see it in place.

const SAME = { '’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-', ' ': ' ' }

// Lower-case text without whitespace, plus where each kept character sits in the original string.
// Layouts break lines and spaces differently from how the model copies them, so spacing is ignored.
function squash(text) {
  let chars = ''
  const at = []
  for (let i = 0; i < text.length; i++) {
    let c = SAME[text[i]] ?? text[i]
    if (/\s/.test(c)) continue
    const lower = c.toLowerCase()
    c = lower.length === 1 ? lower : c
    chars += c
    at.push(i)
  }
  return { chars, at }
}

const WORD = /[\p{L}\p{N}]/u
const GLUE = /[@._-]/

// True when the text at `i` carries on the word, as in "lina" within "lina@cedar.com" or "sara.malik".
function continues(text, i, step) {
  if (WORD.test(text[i] ?? '')) return true
  return GLUE.test(text[i] ?? '') && WORD.test(text[i + step] ?? '')
}

// Every place `needle` appears in `text`, as [start, end) positions in `text`.
// Only whole words count, so a first name doesn't light up inside an email address.
export function findAll(text, needle) {
  const target = squash(needle).chars
  if (target.length < 2) return []
  const { chars, at } = squash(text)
  const found = []
  for (let from = chars.indexOf(target); from !== -1; from = chars.indexOf(target, from + 1)) {
    const [start, end] = [at[from], at[from + target.length - 1] + 1]
    const joined = (WORD.test(text[start]) && continues(text, start - 1, -1)) || (WORD.test(text[end - 1]) && continues(text, end, 1))
    if (!joined && !(found.length && start < found.at(-1)[1])) found.push([start, end])
  }
  return found
}

// The fields worth pointing at: read by the model, not typed in by hand.
export function marksFor(fields) {
  return Object.entries(fields)
    .filter(([, field]) => field.value !== null && !field.edited && (field.source || typeof field.value === 'string'))
    .map(([key, field]) => ({ key, texts: [field.source, typeof field.value === 'string' ? field.value : ''].filter(Boolean) }))
}

// Where each mark lands in `text`: the source as quoted, or the value if the quote isn't there word for word.
export function rangesIn(text, marks) {
  const ranges = []
  for (const mark of marks) {
    for (const needle of mark.texts) {
      const found = findAll(text, needle)
      if (found.length) {
        ranges.push(...found.map(([start, end]) => ({ start, end, key: mark.key })))
        break
      }
    }
  }
  return ranges
}

// Splits text into runs, each with the keys of the marks covering it.
export function segments(text, ranges) {
  const cuts = new Set([0, text.length])
  for (const range of ranges) cuts.add(range.start).add(range.end)
  const points = [...cuts].sort((a, b) => a - b)
  const runs = []
  for (let i = 0; i < points.length - 1; i++) {
    const [start, end] = [points[i], points[i + 1]]
    const keys = ranges.filter((r) => r.start <= start && r.end >= end).map((r) => r.key)
    runs.push({ text: text.slice(start, end), keys })
  }
  return runs
}

// Brings the active highlight into view inside the scrolling pane, without moving the page.
export function scrollToActive(pane) {
  const target = pane?.querySelector('.hl.active')
  if (!target) return
  const box = pane.getBoundingClientRect()
  const spot = target.getBoundingClientRect()
  const across = spot.left >= box.left && spot.right <= box.right
  if (spot.top >= box.top + 8 && spot.bottom <= box.bottom - 8 && across) return
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  pane.scrollTo({
    top: pane.scrollTop + spot.top - box.top - box.height / 3,
    left: across ? pane.scrollLeft : pane.scrollLeft + spot.left - box.left - 24,
    behavior: smooth ? 'smooth' : 'auto',
  })
}
