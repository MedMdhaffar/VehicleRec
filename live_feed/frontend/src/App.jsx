import { useState, useEffect } from 'react'
import { API_BASE, CAMERAS as CAMERAS_FALLBACK } from './config.js'
import CalibrationView from './components/CalibrationView.jsx'
import CameraTile from './components/CameraTile.jsx'
import ReviewView from './components/ReviewView.jsx'

export default function App() {
  const [overlay, setOverlay] = useState(null)          // null | 'review' | 'calibrate'
  const [running, setRunning] = useState(false)
  const [cameras, setCameras] = useState(CAMERAS_FALLBACK)
  const [overlayCamera, setOverlayCamera] = useState(cameras[0])
  const [selectedCams, setSelectedCams] = useState(() => new Set(cameras.map(c => c.id)))
  const [camsLoading, setCamsLoading] = useState(true)

  // Fetch available cameras from backend (Dahua discovered from VMS + Hik from env)
  useEffect(() => {
    fetch(`${API_BASE}/cameras`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.cameras?.length) {
          const fetched = d.cameras.map(c => ({ id: c.id, label: c.label }))
          setCameras(fetched)
          setOverlayCamera(fetched[0])
          setSelectedCams(new Set(fetched.map(c => c.id)))
        }
      })
      .catch(() => {})
      .finally(() => setCamsLoading(false))
  }, [])

  // Poll /health every 2 s
  useEffect(() => {
    const poll = () => {
      fetch(`${API_BASE}/health`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setRunning(!!d.running) })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [])

  async function handleStart() {
    try {
      await fetch(`${API_BASE}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cameras: [...selectedCams] }),
      })
    } catch {}
  }

  async function handleStop() {
    try { await fetch(`${API_BASE}/stop`, { method: 'POST' }) } catch {}
  }

  function toggleCam(id, checked) {
    const next = new Set(selectedCams)
    checked ? next.add(id) : next.delete(id)
    setSelectedCams(next)
  }

  function openOverlay(name) {
    setOverlay(overlay === name ? null : name)
  }

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-title">Live Feed</span>
        <div className="header-controls">
          {!running ? (
            <div className="cam-select">
              {camsLoading ? (
                <span className="cams-loading">loading cameras…</span>
              ) : (
                cameras.map(cam => (
                  <label key={cam.id} className="cam-check">
                    <input
                      type="checkbox"
                      checked={selectedCams.has(cam.id)}
                      onChange={e => toggleCam(cam.id, e.target.checked)}
                    />
                    {cam.label}
                  </label>
                ))
              )}
              <button
                className="btn-start"
                onClick={handleStart}
                disabled={camsLoading || selectedCams.size === 0}
              >
                ● Start selected
              </button>
            </div>
          ) : (
            <span className="running-label">
              ▶ {cameras.filter(c => selectedCams.has(c.id)).map(c => c.label).join(' · ')}
            </span>
          )}
          <button className="btn-stop" onClick={handleStop} disabled={!running}>
            ■ Stop
          </button>
        </div>
        <div className="header-nav">
          <button
            className={overlay === 'review' ? 'active' : ''}
            onClick={() => openOverlay('review')}
          >
            Review
          </button>
          <button
            className={overlay === 'calibrate' ? 'active' : ''}
            onClick={() => openOverlay('calibrate')}
          >
            Calibrate
          </button>
        </div>
      </header>

      <div className="camera-grid">
        {cameras.map(cam => (
          <CameraTile key={cam.id} cam={cam} />
        ))}
      </div>

      {overlay && (
        <div
          className="overlay"
          onClick={e => { if (e.target === e.currentTarget) setOverlay(null) }}
        >
          <div className="overlay-box">
            <div className="overlay-header">
              <span className="overlay-title">
                {overlay === 'review' ? 'Review' : 'Calibrate'}
              </span>
              <select
                className="overlay-cam-select"
                value={overlayCamera.id}
                onChange={e => setOverlayCamera(cameras.find(c => c.id === e.target.value))}
              >
                {cameras.map(c => (
                  <option key={c.id} value={c.id}>{c.label}</option>
                ))}
              </select>
              <button className="overlay-close" onClick={() => setOverlay(null)}>✕</button>
            </div>
            <div className="overlay-content">
              {overlay === 'review'
                ? <ReviewView key={overlayCamera.id} camera={overlayCamera} />
                : <CalibrationView key={overlayCamera.id} camera={overlayCamera} />
              }
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
