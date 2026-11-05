import { FIELD_LABELS, LOW_CONFIDENCE, PLACEHOLDERS, inTeam, who } from '../labels.js'

function Confidence({ field, user }) {
  if (field.edited) {
    const by = inTeam(user) && field.edited_by ? ` by ${who(field.edited_by, user)}` : ''
    return <span className="badge badge-edited">Edited{by}</span>
  }
  if (field.value === null) return null
  if (field.issues.length || field.confidence < LOW_CONFIDENCE) return <span className="badge badge-warn">Check this</span>
  return <span className="badge">{Math.round(field.confidence * 100)}% sure</span>
}

export default function FieldRow({ name, field, value, onChange, disabled, user }) {
  const id = `field-${name.replace('.', '-')}`
  const notes = name === 'notes'
  const describedBy = [field.source && !field.edited && `${id}-source`, field.issues.length && `${id}-issues`].filter(Boolean).join(' ')
  const props = {
    id,
    value: value ?? '',
    onChange: (event) => onChange(event.target.value === '' ? null : event.target.value),
    disabled,
    placeholder: PLACEHOLDERS[name] || '',
    'aria-describedby': describedBy || undefined,
    'aria-invalid': field.issues.length ? true : undefined,
  }
  return (
    <div className={`field-row${field.issues.length ? ' has-issue' : ''}`} data-field={name}>
      <div className="field-top">
        <label htmlFor={id} className="field-label">{FIELD_LABELS[name]}</label>
        <Confidence field={field} user={user} />
      </div>
      {notes ? <textarea rows={3} {...props} /> : <input type={name === 'contact.email' ? 'email' : 'text'} {...props} />}
      {field.source && !field.edited && (
        <p id={`${id}-source`} className="source">
          <span>From the document</span> <q>{field.source}</q>
        </p>
      )}
      {field.issues.length > 0 && (
        <ul id={`${id}-issues`} className="issues">
          {field.issues.map((issue) => <li key={issue}>{issue}</li>)}
        </ul>
      )}
    </div>
  )
}
