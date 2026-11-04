import { STATUSES } from '../labels.js'

export default function StatusPill({ status }) {
  const { label, tone } = STATUSES[status] || { label: status, tone: 'info' }
  return <span className={`pill pill-${tone}`}>{label}</span>
}
