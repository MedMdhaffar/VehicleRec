import { useEffect, useState } from 'react'

const BADGE_COLORS = {
  overstay:      { bg: '#3a2000', border: '#f80', text: '#f80' },
  violation:     { bg: '#2a0000', border: '#f44', text: '#f44' },
  overcrowding:  { bg: '#2a2a00', border: '#cc0', text: '#cc0' },
  parking_ended: { bg: '#1a1a1a', border: '#555', text: '#888' },
}

function fmtTypes(typeMap) {
  return Object.entries(typeMap)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${t} ${n}`)
    .join('  ')
}

function total(typeMap) {
  return Object.values(typeMap).reduce((a, b) => a + b, 0)
}

function RelTime({ ts }) {
  const [label, setLabel] = useState('')

  useEffect(() => {
    function update() {
      const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
      if (diff < 5)  setLabel('just now')
      else if (diff < 60) setLabel(`${diff}s ago`)
      else setLabel(`${Math.floor(diff / 60)}m ago`)
    }
    update()
    const id = setInterval(update, 5000)
    return () => clearInterval(id)
  }, [ts])

  return <span className="zev-time">{label}</span>
}

function ZoneEventRow({ evt }) {
  const colors = BADGE_COLORS[evt.event] ?? BADGE_COLORS.parking_ended
  const fields = []
  if (evt.vehicle_type) fields.push(evt.vehicle_type)
  if (evt.plate)        fields.push(evt.plate)
  if (evt.duration_s != null) fields.push(`${evt.duration_s}s`)
  if (evt.count != null)      fields.push(`×${evt.count}`)

  return (
    <div className="zev-row">
      <span
        className="zev-badge"
        style={{ background: colors.bg, border: `1px solid ${colors.border}`, color: colors.text }}
      >
        {evt.event}
      </span>
      <span className="zev-zone">{evt.zone}</span>
      {fields.length > 0 && <span className="zev-fields">{fields.join(' · ')}</span>}
      {evt.ts && <RelTime ts={evt.ts} />}
    </div>
  )
}

export default function TrafficPanel({ lineCounters, zoneEvents }) {
  const lines = Object.entries(lineCounters)
  const hasLines = lines.length > 0
  const hasEvents = zoneEvents.length > 0

  if (!hasLines && !hasEvents) return null

  return (
    <div className="traffic-panel">
      {/* ── Line counters ─────────────────────────────── */}
      <div className="traffic-section">
        <span className="traffic-section-title">Line Counts</span>
        {hasLines ? (
          <div className="line-counters">
            {lines.map(([zoneId, data]) => {
              const ltrTotal = total(data.left_to_right)
              const rtlTotal = total(data.right_to_left)
              const ltrBreak = fmtTypes(data.left_to_right)
              const rtlBreak = fmtTypes(data.right_to_left)
              return (
                <div key={zoneId} className="line-counter">
                  <span className="line-name">{data.name}</span>
                  <div className="line-dirs">
                    <div className="line-dir">
                      <span className="dir-arrow dir-ltr">→</span>
                      <span className="dir-total">{ltrTotal}</span>
                      {ltrBreak && <span className="dir-breakdown">{ltrBreak}</span>}
                    </div>
                    <div className="line-dir">
                      <span className="dir-arrow dir-rtl">←</span>
                      <span className="dir-total">{rtlTotal}</span>
                      {rtlBreak && <span className="dir-breakdown">{rtlBreak}</span>}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <span className="traffic-empty">No line crossings yet.</span>
        )}
      </div>

      {/* ── Zone event log ────────────────────────────── */}
      {hasEvents && (
        <div className="traffic-section traffic-section-log">
          <span className="traffic-section-title">Zone Alerts</span>
          <div className="zone-events-log">
            {zoneEvents.map((evt, i) => (
              <ZoneEventRow key={i} evt={evt} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
