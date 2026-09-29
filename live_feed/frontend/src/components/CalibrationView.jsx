import { useCallback, useEffect, useRef, useState } from 'react'
import { camUrl } from '../config.js'

const FUNCTIONS = ['read_zone', 'parking', 'restricted', 'occupancy']
const FN_COLORS = { read_zone: '#0cf', parking: '#0c0', restricted: '#f22', occupancy: '#cc0' }
const FN_DEFAULT = '#888'
const CLOSE_RADIUS = 10  // px on-canvas — click within this of first point to close polygon

function polyColor(fns) {
  for (const fn of ['restricted', 'parking', 'read_zone', 'occupancy']) {
    if (fns.includes(fn)) return FN_COLORS[fn]
  }
  return FN_DEFAULT
}

let _shapeIdCounter = 0
function nextId(prefix) { return `${prefix}${++_shapeIdCounter}` }

export default function CalibrationView({ camera }) {
  const canvasRef = useRef(null)
  const imgRef = useRef(null)   // native HTMLImageElement for the snapshot
  const [nativeSize, setNativeSize] = useState([1920, 1080])

  const [shapes, setShapes] = useState([])          // saved shapes (polygons + lines)
  const [currentPoints, setCurrentPoints] = useState([])  // points being drawn
  const [mode, setMode] = useState('draw_polygon')   // 'draw_polygon' | 'draw_line' | 'select'
  const [selected, setSelected] = useState(null)     // id of selected shape
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  // ── Load snapshot ──────────────────────────────────────────────────────────
  const loadSnapshot = useCallback(() => {
    setStatus('Loading snapshot…')
    const img = new Image()
    img.onload = () => {
      imgRef.current = img
      setNativeSize([img.naturalWidth, img.naturalHeight])
      setStatus('')
      redraw(img, shapes, currentPoints, mode, selected)
    }
    img.onerror = () => setError('Failed to load snapshot. Is the pipeline running?')
    img.src = `${camUrl(camera, '/snapshot')}&t=${Date.now()}`
  }, [camera.id])  // eslint-disable-line

  // ── Load existing ROIs ─────────────────────────────────────────────────────
  const loadRois = useCallback(async () => {
    try {
      const res = await fetch(camUrl(camera, '/rois'))
      if (!res.ok) return
      const data = await res.json()
      const loaded = []
      for (const p of data.polygons || []) {
        loaded.push({ kind: 'polygon', id: p.id, name: p.name, points: p.points, functions: p.functions })
      }
      for (const l of data.lines || []) {
        loaded.push({ kind: 'line', id: l.id, name: l.name, a: l.a, b: l.b })
      }
      setShapes(loaded)
    } catch { /* backend may not be running yet */ }
  }, [camera.id])

  useEffect(() => {
    loadSnapshot()
    loadRois()
  }, [loadSnapshot, loadRois])

  // ── Redraw canvas ──────────────────────────────────────────────────────────
  const redraw = useCallback((img, shapeList, curPts, drawMode, selId) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width
    const H = canvas.height

    ctx.clearRect(0, 0, W, H)
    if (img) ctx.drawImage(img, 0, 0, W, H)

    // Draw saved shapes
    for (const s of shapeList) {
      const isSelected = s.id === selId
      ctx.lineWidth = isSelected ? 3 : 2
      if (s.kind === 'polygon') {
        const color = polyColor(s.functions)
        const pts = s.points.map(([x, y]) => toCanvas(x, y, img, W, H))
        ctx.beginPath()
        pts.forEach(([cx, cy], i) => i === 0 ? ctx.moveTo(cx, cy) : ctx.lineTo(cx, cy))
        ctx.closePath()
        ctx.fillStyle = color + '33'
        ctx.fill()
        ctx.strokeStyle = color
        ctx.stroke()
        // label
        const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length
        const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length
        ctx.font = '12px system-ui'
        ctx.fillStyle = color
        ctx.textAlign = 'center'
        ctx.fillText(s.name || s.id, cx, cy)
      } else if (s.kind === 'line') {
        const [ax, ay] = toCanvas(s.a[0], s.a[1], img, W, H)
        const [bx, by] = toCanvas(s.b[0], s.b[1], img, W, H)
        ctx.strokeStyle = isSelected ? '#fff' : '#aaf'
        ctx.beginPath()
        ctx.moveTo(ax, ay)
        ctx.lineTo(bx, by)
        ctx.stroke()
        // arrowhead
        const angle = Math.atan2(by - ay, bx - ax)
        const tLen = 10
        ctx.beginPath()
        ctx.moveTo(bx, by)
        ctx.lineTo(bx - tLen * Math.cos(angle - 0.4), by - tLen * Math.sin(angle - 0.4))
        ctx.lineTo(bx - tLen * Math.cos(angle + 0.4), by - tLen * Math.sin(angle + 0.4))
        ctx.closePath()
        ctx.fillStyle = isSelected ? '#fff' : '#aaf'
        ctx.fill()
        // label
        ctx.font = '12px system-ui'
        ctx.fillStyle = '#aaf'
        ctx.textAlign = 'center'
        ctx.fillText(s.name || s.id, (ax + bx) / 2, (ay + by) / 2 - 6)
      }
    }

    // Draw in-progress points
    if (curPts.length > 0) {
      const color = drawMode === 'draw_line' ? '#ff0' : '#f90'
      ctx.strokeStyle = color
      ctx.fillStyle = color
      ctx.lineWidth = 2
      ctx.beginPath()
      const [fx, fy] = curPts[0]
      ctx.moveTo(fx, fy)
      for (let i = 1; i < curPts.length; i++) {
        ctx.lineTo(curPts[i][0], curPts[i][1])
      }
      ctx.stroke()
      // dot on each point
      for (const [px, py] of curPts) {
        ctx.beginPath()
        ctx.arc(px, py, 4, 0, Math.PI * 2)
        ctx.fill()
      }
      // highlight first point as close target for polygon
      if (drawMode === 'draw_polygon' && curPts.length >= 3) {
        ctx.beginPath()
        ctx.arc(curPts[0][0], curPts[0][1], CLOSE_RADIUS, 0, Math.PI * 2)
        ctx.strokeStyle = '#fff'
        ctx.lineWidth = 1
        ctx.stroke()
      }
    }
  }, [])

  // Re-render whenever relevant state changes
  useEffect(() => {
    redraw(imgRef.current, shapes, currentPoints, mode, selected)
  }, [shapes, currentPoints, mode, selected, redraw])

  // ── Coordinate helpers ─────────────────────────────────────────────────────
  function toCanvas(nx, ny, img, W, H) {
    if (!img) return [nx, ny]
    return [Math.round(nx * W / img.naturalWidth), Math.round(ny * H / img.naturalHeight)]
  }

  function toNative(cx, cy) {
    const canvas = canvasRef.current
    if (!canvas || !imgRef.current) return [cx, cy]
    const img = imgRef.current
    return [
      Math.round(cx * img.naturalWidth / canvas.width),
      Math.round(cy * img.naturalHeight / canvas.height),
    ]
  }

  function getCanvasXY(e) {
    const canvas = canvasRef.current
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    return [
      (e.clientX - rect.left) * scaleX,
      (e.clientY - rect.top) * scaleY,
    ]
  }

  // ── Canvas click handler ───────────────────────────────────────────────────
  function handleCanvasClick(e) {
    if (mode === 'select') {
      setSelected(null)
      return
    }
    const [cx, cy] = getCanvasXY(e)
    const [nx, ny] = toNative(cx, cy)

    if (mode === 'draw_line') {
      const next = [...currentPoints, [cx, cy]]
      if (next.length === 2) {
        const [ap, bp] = next.map(([pcx, pcy]) => toNative(pcx, pcy))
        const newLine = { kind: 'line', id: nextId('l'), name: 'line', a: ap, b: bp }
        setShapes(s => [...s, newLine])
        setCurrentPoints([])
        setSelected(newLine.id)
      } else {
        setCurrentPoints(next)
      }
      return
    }

    // draw_polygon
    if (currentPoints.length >= 3) {
      const [fx, fy] = currentPoints[0]
      const dist = Math.hypot(cx - fx, cy - fy)
      if (dist <= CLOSE_RADIUS) {
        // Close polygon
        const nativePts = currentPoints.map(([pcx, pcy]) => toNative(pcx, pcy))
        const newPoly = { kind: 'polygon', id: nextId('p'), name: 'zone', points: nativePts, functions: [] }
        setShapes(s => [...s, newPoly])
        setCurrentPoints([])
        setSelected(newPoly.id)
        return
      }
    }
    setCurrentPoints(pts => [...pts, [cx, cy]])
  }

  function cancelDraw() { setCurrentPoints([]) }

  // ── Shape property editors ─────────────────────────────────────────────────
  function updateShape(id, patch) {
    setShapes(s => s.map(sh => sh.id === id ? { ...sh, ...patch } : sh))
  }

  function deleteShape(id) {
    setShapes(s => s.filter(sh => sh.id !== id))
    if (selected === id) setSelected(null)
  }

  // ── Save / Reload ──────────────────────────────────────────────────────────
  async function handleSave() {
    setError('')
    const polygons = shapes
      .filter(s => s.kind === 'polygon')
      .map(s => ({ id: s.id, name: s.name, points: s.points, functions: s.functions }))
    const lines = shapes
      .filter(s => s.kind === 'line')
      .map(s => ({ id: s.id, name: s.name, a: s.a, b: s.b, count: true }))
    const body = {
      camera: camera.id,
      frame_size: nativeSize,
      polygons,
      lines,
    }
    try {
      const res = await fetch(camUrl(camera, '/rois'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        setError(`Save failed: ${err.detail || res.statusText}`)
      } else {
        setStatus('Saved.')
        setTimeout(() => setStatus(''), 2000)
      }
    } catch (err) {
      setError(`Save failed: ${err.message}`)
    }
  }

  async function handleReload() {
    setError('')
    try {
      const res = await fetch(camUrl(camera, '/reload-rois'), { method: 'POST' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        setError(`Reload failed: ${err.detail || res.statusText}`)
      } else {
        setStatus('Reloaded.')
        setTimeout(() => setStatus(''), 2000)
      }
    } catch (err) {
      setError(`Reload failed: ${err.message}`)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  const selShape = shapes.find(s => s.id === selected)

  return (
    <div className="calib-view">
      {/* Toolbar */}
      <div className="calib-toolbar">
        <span className="camera-label">{camera.label} — Calibration</span>
        <button
          className={mode === 'draw_polygon' ? 'btn-mode active' : 'btn-mode'}
          onClick={() => { setMode('draw_polygon'); cancelDraw() }}
        >Polygon</button>
        <button
          className={mode === 'draw_line' ? 'btn-mode active' : 'btn-mode'}
          onClick={() => { setMode('draw_line'); cancelDraw() }}
        >Line</button>
        {currentPoints.length > 0 && (
          <button className="btn-cancel" onClick={cancelDraw}>Cancel ({currentPoints.length} pts)</button>
        )}
        <span style={{ flex: 1 }} />
        <button className="btn-reload-snap" onClick={loadSnapshot}>↺ Snapshot</button>
        <button className="btn-save" onClick={handleSave}>Save</button>
        <button className="btn-reload" onClick={handleReload}>Reload into pipeline</button>
      </div>

      {error && <p className="error-banner">{error}</p>}
      {status && <p className="calib-status">{status}</p>}

      <div className="calib-body">
        {/* Canvas */}
        <div className="calib-canvas-wrap">
          <canvas
            ref={canvasRef}
            width={960}
            height={540}
            className="calib-canvas"
            onClick={handleCanvasClick}
          />
          <p className="calib-hint">
            {mode === 'draw_polygon'
              ? 'Click to add points. Click near the first point (circle) to close.'
              : 'Click point A, then point B to place a counting line.'}
          </p>
        </div>

        {/* Shape list + editor */}
        <div className="calib-sidebar">
          <h4 className="calib-sidebar-title">Shapes ({shapes.length})</h4>
          {shapes.length === 0 && <p className="calib-empty">No shapes yet.</p>}
          {shapes.map(s => (
            <div
              key={s.id}
              className={`calib-shape-item${s.id === selected ? ' selected' : ''}`}
              onClick={() => setSelected(s.id)}
            >
              <span className="calib-shape-kind">{s.kind === 'polygon' ? '⬡' : '—'}</span>
              <span className="calib-shape-name">{s.name}</span>
              {s.kind === 'polygon' && s.functions.length > 0 && (
                <span className="calib-shape-fns" style={{ color: polyColor(s.functions) }}>
                  {s.functions.join(',')}
                </span>
              )}
            </div>
          ))}

          {/* Selected shape editor */}
          {selShape && (
            <div className="calib-editor">
              <label className="calib-field">
                <span>Name</span>
                <input
                  value={selShape.name}
                  onChange={e => updateShape(selShape.id, { name: e.target.value })}
                  className="calib-input"
                />
              </label>

              {selShape.kind === 'polygon' && (
                <div className="calib-field">
                  <span>Functions</span>
                  <div className="calib-fns">
                    {FUNCTIONS.map(fn => (
                      <label key={fn} className="calib-fn-label" style={{ color: FN_COLORS[fn] }}>
                        <input
                          type="checkbox"
                          checked={selShape.functions.includes(fn)}
                          onChange={e => {
                            const fns = e.target.checked
                              ? [...selShape.functions, fn]
                              : selShape.functions.filter(f => f !== fn)
                            updateShape(selShape.id, { functions: fns })
                          }}
                        />
                        {fn}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <button className="btn-delete" onClick={() => deleteShape(selShape.id)}>
                Delete shape
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
