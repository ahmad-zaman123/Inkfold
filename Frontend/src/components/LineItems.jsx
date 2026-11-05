import { LOW_CONFIDENCE } from '../labels.js'

const COLUMNS = [
  ['description', 'Description'],
  ['quantity', 'Qty'],
  ['unit_price', 'Unit price'],
  ['amount', 'Amount'],
]
const EMPTY = { description: '', quantity: null, unit_price: null, amount: null }

export default function LineItems({ field, items, onChange, disabled }) {
  const rows = items || []

  function update(index, key, value) {
    onChange(rows.map((row, i) => (i === index ? { ...row, [key]: value === '' ? null : value } : row)))
  }

  return (
    <div className="field-row line-items" data-field="invoice.line_items">
      <div className="field-top">
        <span className="field-label" id="line-items-label">Line items</span>
        {field.edited ? (
          <span className="badge badge-edited">Edited</span>
        ) : (
          rows.length > 0 && (
            <span className={`badge${field.confidence < LOW_CONFIDENCE ? ' badge-warn' : ''}`}>
              {field.confidence < LOW_CONFIDENCE ? 'Check this' : `${Math.round(field.confidence * 100)}% sure`}
            </span>
          )
        )}
      </div>
      <div className="table-scroll">
        <table aria-labelledby="line-items-label">
          <thead>
            <tr>
              {COLUMNS.map(([key, label]) => <th key={key} scope="col">{label}</th>)}
              <th><span className="visually-hidden">Remove</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {COLUMNS.map(([key, label]) => (
                  <td key={key} className={key === 'description' ? '' : 'num'}>
                    <input
                      value={row[key] ?? ''}
                      onChange={(e) => update(index, key, e.target.value)}
                      aria-label={`${label}, line ${index + 1}`}
                      disabled={disabled}
                      inputMode={key === 'description' ? undefined : 'decimal'}
                    />
                  </td>
                ))}
                <td>
                  <button
                    type="button"
                    className="icon-button small"
                    onClick={() => onChange(rows.filter((_, i) => i !== index))}
                    aria-label={`Remove line ${index + 1}`}
                    disabled={disabled}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="link-button" onClick={() => onChange([...rows, { ...EMPTY }])} disabled={disabled}>
        Add a line
      </button>
    </div>
  )
}
