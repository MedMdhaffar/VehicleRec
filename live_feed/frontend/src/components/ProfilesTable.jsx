import { useRef } from 'react'

// Anchor monotonic first_seen to a wall-clock reference so we can display
// human-readable times. Python's time.monotonic() is NOT a Unix epoch.
function useTimeAnchor() {
  const anchor = useRef(null) // { monotonic, wall }
  return function resolve(monotonic) {
    if (anchor.current === null) {
      anchor.current = { monotonic, wall: Date.now() }
    }
    const wallMs = anchor.current.wall + (monotonic - anchor.current.monotonic) * 1000
    return new Date(wallMs).toLocaleTimeString()
  }
}

export default function ProfilesTable({ profiles }) {
  const resolveTime = useTimeAnchor()

  const rows = [...profiles.values()].sort((a, b) => b.last_seen - a.last_seen)

  if (rows.length === 0) {
    return (
      <div className="table-wrapper">
        <p className="table-empty">No vehicles detected yet.</p>
      </div>
    )
  }

  return (
    <div className="table-wrapper">
      <table className="profiles-table">
        <thead>
          <tr>
            <th>Track</th>
            <th>Type</th>
            <th>Colour</th>
            <th>Plate</th>
            <th>Plate Type</th>
            <th>Conf</th>
            <th>First Seen</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(p => (
            <tr key={p.track_id} className={p.status === 'final' ? 'row-final' : ''}>
              <td>{p.track_id}</td>
              <td>{p.type ?? '—'}</td>
              <td>{p.colour ?? '—'}</td>
              <td className="cell-plate">{p.plate?.number ?? '—'}</td>
              <td>{p.plate?.type ?? '—'}</td>
              <td>{p.plate?.digits_conf != null ? p.plate.digits_conf.toFixed(2) : '—'}</td>
              <td>{resolveTime(p.first_seen)}</td>
              <td>{p.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
