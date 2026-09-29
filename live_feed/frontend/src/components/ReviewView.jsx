import { useState, useEffect, useCallback } from 'react'
import { camUrl } from '../config.js'

const VEHICLE_CLASSES = ['car', 'truck', 'bus', 'van', 'motorcycle', 'bicycle', 'person', 'unknown']
const COLOURS = ['white', 'black', 'silver', 'grey', 'red', 'blue', 'green', 'yellow', 'orange', 'brown', 'beige', 'unknown']
const PLATE_TYPES = ['tounes', 'other']
const CATEGORIES = ['private', 'diplomatic', 'military', 'RS', 'taxi', 'transport', 'foreign', 'unknown']

function AgreeIcon({ value }) {
  if (value === true)  return <span className="agree-ok">✓</span>
  if (value === false) return <span className="agree-bad">✗</span>
  return null
}

function RecordItem({ rec, selected, saved, onClick }) {
  const isQueue = rec.routed_to === 'review_queue'
  const cls = [
    'review-record-item',
    selected ? 'selected' : '',
    saved    ? 'saved'    : '',
  ].filter(Boolean).join(' ')

  return (
    <div className={cls} onClick={onClick}>
      <span className="rr-id">#{rec.record_id} · tk{rec.track_id}</span>
      <span className={`rr-badge ${isQueue ? 'rr-badge-queue' : 'rr-badge-trusted'}`}>
        {isQueue ? 'queue' : 'trusted'}
      </span>
      <span className="rr-desc">
        {rec.vehicle.vehicle_class} · {rec.vehicle.colour}
        {rec.vehicle.vehicle_maker && rec.vehicle.vehicle_maker !== 'unknown'
          ? ` · ${rec.vehicle.vehicle_maker}` : ''}
      </span>
    </div>
  )
}

function DetailPanel({ rec, camera, onSaved }) {
  const initEdits = useCallback((r) => ({
    vehicle_class:     r.vehicle.vehicle_class,
    colour:            r.vehicle.colour,
    vehicle_maker:     r.vehicle.vehicle_maker,
    ocr:               r.plate?.ocr ?? '',
    plate_type:        r.plate?.plate_type ?? 'other',
    observed_category: r.plate?.observed_category ?? 'unknown',
  }), [])

  const [edits, setEdits]   = useState(() => initEdits(rec))
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState(null)

  useEffect(() => { setEdits(initEdits(rec)); setError(null) }, [rec, initEdits])

  function set(field, val) {
    setEdits(e => ({ ...e, [field]: val }))
  }

  async function handleSave() {
    setSaving(true); setError(null)
    try {
      const res = await fetch(camUrl(camera, `/review/verify/${rec.record_id}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(edits),
      })
      if (!res.ok) {
        const j = await res.json().catch(() => ({}))
        throw new Error(j.detail || res.statusText)
      }
      onSaved(rec.record_id)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const ag = rec.agreement || {}
  const hasPlate = !!rec.predicted?.plate_crop_path

  return (
    <div className="review-detail">
      {/* ── Crop images ── */}
      <div className="review-crops">
        <div className="review-crop-box">
          <span className="review-crop-label">VEHICLE</span>
          <img
            src={`${camUrl(camera, `/review/crop/${rec.record_id}`)}&type=veh`}
            alt="vehicle crop"
            onError={e => { e.target.style.display = 'none' }}
          />
        </div>
        {hasPlate
          ? <div className="review-crop-box">
              <span className="review-crop-label">PLATE</span>
              <img
                src={`${camUrl(camera, `/review/crop/${rec.record_id}`)}&type=plate`}
                alt="plate crop"
                onError={e => { e.target.style.display = 'none' }}
              />
            </div>
          : <div className="review-crop-box">
              <span className="review-crop-label">PLATE</span>
              <div className="review-no-plate">no plate</div>
            </div>
        }
      </div>

      {/* ── Field comparison table ── */}
      <table className="review-table">
        <thead>
          <tr>
            <th>FIELD</th>
            <th>MODEL SAID</th>
            <th>GEMINI / EDIT</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="review-field-label">class</td>
            <td className="review-model-val">{rec.predicted.vehicle_class_predicted}</td>
            <td>
              <select className="review-select" value={edits.vehicle_class}
                onChange={e => set('vehicle_class', e.target.value)}>
                {VEHICLE_CLASSES.map(v => <option key={v}>{v}</option>)}
              </select>
            </td>
            <td><AgreeIcon value={ag.class} /></td>
          </tr>
          <tr>
            <td className="review-field-label">colour</td>
            <td className="review-model-val">{rec.predicted.colour_predicted}</td>
            <td>
              <select className="review-select" value={edits.colour}
                onChange={e => set('colour', e.target.value)}>
                {COLOURS.map(v => <option key={v}>{v}</option>)}
              </select>
            </td>
            <td><AgreeIcon value={ag.colour} /></td>
          </tr>
          <tr>
            <td className="review-field-label">maker</td>
            <td className="review-model-val">—</td>
            <td>
              <input className="review-input" value={edits.vehicle_maker}
                onChange={e => set('vehicle_maker', e.target.value)}
                placeholder="brand" />
            </td>
            <td></td>
          </tr>
          <tr>
            <td className="review-field-label">ocr</td>
            <td className="review-model-val">{rec.predicted.ocr_predicted || '—'}</td>
            <td>
              <input className="review-input" value={edits.ocr}
                onChange={e => set('ocr', e.target.value)}
                placeholder="plate digits" disabled={!hasPlate} />
            </td>
            <td><AgreeIcon value={hasPlate ? ag.ocr : undefined} /></td>
          </tr>
          <tr>
            <td className="review-field-label">plate type</td>
            <td className="review-model-val">{rec.predicted.plate_type_predicted || '—'}</td>
            <td>
              <select className="review-select" value={edits.plate_type}
                onChange={e => set('plate_type', e.target.value)} disabled={!hasPlate}>
                {PLATE_TYPES.map(v => <option key={v}>{v}</option>)}
              </select>
            </td>
            <td><AgreeIcon value={hasPlate ? ag.plate_type : undefined} /></td>
          </tr>
          <tr>
            <td className="review-field-label">category</td>
            <td className="review-model-val">—</td>
            <td>
              <select className="review-select" value={edits.observed_category}
                onChange={e => set('observed_category', e.target.value)} disabled={!hasPlate}>
                {CATEGORIES.map(v => <option key={v}>{v}</option>)}
              </select>
            </td>
            <td></td>
          </tr>
        </tbody>
      </table>

      {error && <div className="error-banner">{error}</div>}

      <div className="review-actions">
        <button className="btn-verify" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : '✓ Save as Verified'}
        </button>
        <button className="btn-skip" onClick={() => onSaved(null)}>
          → Skip
        </button>
      </div>
    </div>
  )
}

export default function ReviewView({ camera }) {
  const [records, setRecords]   = useState([])
  const [total, setTotal]       = useState(0)
  const [offset, setOffset]     = useState(0)
  const [filter, setFilter]     = useState('all')
  const [selected, setSelected] = useState(null)
  const [savedIds, setSavedIds] = useState(new Set())
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)

  const LIMIT = 50

  useEffect(() => {
    setLoading(true); setError(null)
    fetch(camUrl(camera, `/review/records?filter=${filter}&limit=${LIMIT}&offset=${offset}`))
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.detail)))
      .then(d => { setRecords(d.records); setTotal(d.total) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [camera, filter, offset])

  function handleFilterChange(f) {
    setFilter(f); setOffset(0); setSelected(null)
  }

  function handleSaved(recordId) {
    if (recordId !== null) {
      setSavedIds(prev => new Set([...prev, recordId]))
    }
    // auto-advance to next unsaved record
    const idx = records.findIndex(r => r.record_id === (selected?.record_id))
    const next = records.slice(idx + 1).find(r => !savedIds.has(r.record_id))
    setSelected(next || null)
  }

  return (
    <div className="review-view">
      {/* ── Record list ── */}
      <div className="review-list">
        <div className="review-filter-tabs">
          {['all', 'review_queue', 'trusted'].map(f => (
            <button key={f}
              className={filter === f ? 'active' : ''}
              onClick={() => handleFilterChange(f)}
            >
              {f === 'review_queue' ? 'queue' : f}
            </button>
          ))}
        </div>
        <div className="review-records">
          {loading && <div className="review-loading">loading…</div>}
          {error   && <div className="error-banner">{error}</div>}
          {!loading && records.length === 0 && (
            <div className="review-empty-list">no records</div>
          )}
          {records.map(rec => (
            <RecordItem
              key={rec.record_id}
              rec={rec}
              selected={selected?.record_id === rec.record_id}
              saved={savedIds.has(rec.record_id)}
              onClick={() => setSelected(rec)}
            />
          ))}
          {offset + LIMIT < total && (
            <button className="review-load-more"
              onClick={() => setOffset(o => o + LIMIT)}>
              load more ({total - offset - LIMIT} left)
            </button>
          )}
        </div>
      </div>

      {/* ── Detail panel ── */}
      {selected
        ? <DetailPanel
            key={selected.record_id}
            rec={selected}
            camera={camera}
            onSaved={handleSaved}
          />
        : <div className="review-empty">← select a record to review</div>
      }
    </div>
  )
}
