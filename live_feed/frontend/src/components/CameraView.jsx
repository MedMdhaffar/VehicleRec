import { useEffect, useRef, useState } from 'react'
import { streamUrl, camUrl, wsUrl } from '../config.js'
import MetricsStrip from './MetricsStrip.jsx'
import ProfilesTable from './ProfilesTable.jsx'
import TrafficPanel from './TrafficPanel.jsx'

const MAX_PROFILES = 50
const MAX_ZONE_EVENTS = 50

export default function CameraView({ camera }) {
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)
  const lineNamesRef = useRef({})

  const [profiles, setProfiles] = useState(new Map())
  const [metrics, setMetrics] = useState(null)
  const [wsStatus, setWsStatus] = useState('connecting')
  const [pipelineStatus, setPipelineStatus] = useState('idle')
  const [pipelineError, setPipelineError] = useState(null)
  const [streamError, setStreamError] = useState(false)
  // { [zone_id]: { name, left_to_right: {type: count}, right_to_left: {type: count} } }
  const [lineCounters, setLineCounters] = useState({})
  // last N non-crossing zone events
  const [zoneEvents, setZoneEvents] = useState([])

  useEffect(() => {
    // Fetch ROI line names for display labels
    fetch(camUrl(camera, '/rois'))
      .then(r => r.json())
      .then(d => {
        const m = {}
        for (const l of d.lines || []) m[l.id] = l.name
        lineNamesRef.current = m
      })
      .catch(() => {})
  }, [camera.id])

  useEffect(() => {
    function connect() {
      setWsStatus('connecting')
      const ws = new WebSocket(wsUrl(camera))
      wsRef.current = ws

      ws.onopen = () => setWsStatus('open')

      ws.onmessage = (event) => {
        let msg
        try { msg = JSON.parse(event.data) } catch { return }

        if (msg.type === 'snapshot') {
          const map = new Map()
          for (const p of msg.profiles) map.set(p.track_id, p)
          setProfiles(map)
        } else if (msg.type === 'profile') {
          setProfiles(prev => {
            const next = new Map(prev)
            next.set(msg.profile.track_id, msg.profile)
            if (next.size > MAX_PROFILES) evict(next)
            return next
          })
        } else if (msg.type === 'metrics') {
          setMetrics(msg.data)
        } else if (msg.type === 'zone_event') {
          if (msg.event === 'crossing') {
            setLineCounters(prev => {
              const entry = prev[msg.zone] ?? {
                name: lineNamesRef.current[msg.zone] ?? msg.zone,
                left_to_right: {},
                right_to_left: {},
              }
              return {
                ...prev,
                [msg.zone]: {
                  ...entry,
                  [msg.direction]: {
                    ...entry[msg.direction],
                    [msg.vehicle_type]: msg.count,
                  },
                },
              }
            })
          } else {
            setZoneEvents(prev => {
              const next = [msg, ...prev]
              return next.length > MAX_ZONE_EVENTS ? next.slice(0, MAX_ZONE_EVENTS) : next
            })
          }
        }
      }

      ws.onerror = () => setWsStatus('error')

      ws.onclose = () => {
        setWsStatus('closed')
        reconnectRef.current = setTimeout(connect, 2000)
      }
    }

    connect()

    return () => {
      clearTimeout(reconnectRef.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [camera.id])

  async function handleStart() {
    setPipelineError(null)
    setPipelineStatus('starting')
    try {
      const res = await fetch(camUrl(camera, '/start'), { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setPipelineStatus('running')
    } catch (e) {
      setPipelineError(`Start failed: ${e.message}`)
      setPipelineStatus('idle')
    }
  }

  async function handleStop() {
    setPipelineError(null)
    setPipelineStatus('stopping')
    try {
      const res = await fetch(camUrl(camera, '/stop'), { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setPipelineStatus('stopped')
    } catch (e) {
      setPipelineError(`Stop failed: ${e.message}`)
      setPipelineStatus('running')
    }
  }

  const busy = pipelineStatus === 'starting' || pipelineStatus === 'stopping'

  return (
    <section className="camera-view">
      <div className="controls">
        <span className="camera-label">{camera.label}</span>
        <button
          className="btn-start"
          onClick={handleStart}
          disabled={busy || pipelineStatus === 'running'}
        >
          Start
        </button>
        <button
          className="btn-stop"
          onClick={handleStop}
          disabled={busy || pipelineStatus === 'idle' || pipelineStatus === 'stopped'}
        >
          Stop
        </button>
        <span className="pipeline-status">{pipelineStatus}</span>
        <span className={`ws-indicator ws-${wsStatus}`} title={`WS: ${wsStatus}`}>●</span>
      </div>

      {pipelineError && <p className="error-banner">{pipelineError}</p>}

      <MetricsStrip metrics={metrics} />

      <TrafficPanel lineCounters={lineCounters} zoneEvents={zoneEvents} />

      <div className="video-wrapper">
        {streamError ? (
          <div className="stream-placeholder">
            <span>Stream unavailable</span>
            <button onClick={() => setStreamError(false)}>Retry</button>
          </div>
        ) : (
          <img
            src={streamUrl(camera)}
            alt="live annotated feed"
            className="video-stream"
            onError={() => setStreamError(true)}
          />
        )}
      </div>

      <ProfilesTable profiles={profiles} />
    </section>
  )
}

function evict(map) {
  // evict oldest final first, then oldest active
  const entries = [...map.entries()].sort((a, b) => a[1].last_seen - b[1].last_seen)
  for (const [id, p] of entries) {
    if (p.status === 'final') { map.delete(id); return }
  }
  for (const [id] of entries) {
    map.delete(id); return
  }
}
