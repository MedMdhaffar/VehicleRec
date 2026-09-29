import { useEffect, useRef, useState } from 'react'
import { streamUrl, camUrl, wsUrl } from '../config.js'
import AnalyticsCard from './AnalyticsCard.jsx'

const MAX_PROFILES = 50
const MAX_ZONE_EVENTS = 50

export default function CameraTile({ cam }) {
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)
  const lineNamesRef = useRef({})

  const [profiles, setProfiles] = useState(new Map())
  const [metrics, setMetrics] = useState(null)
  const [wsStatus, setWsStatus] = useState('connecting')
  const [streamError, setStreamError] = useState(false)
  const [lineCounters, setLineCounters] = useState({})
  const [zoneEvents, setZoneEvents] = useState([])

  useEffect(() => {
    fetch(camUrl(cam, '/rois'))
      .then(r => r.json())
      .then(d => {
        const m = {}
        for (const l of d.lines || []) m[l.id] = l.name
        lineNamesRef.current = m
      })
      .catch(() => {})
  }, [cam.id])

  useEffect(() => {
    function connect() {
      setWsStatus('connecting')
      const ws = new WebSocket(wsUrl(cam))
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
  }, [cam.id])

  return (
    <section className="camera-tile">
      <div className="tile-header">
        <span className="camera-label">{cam.label}</span>
        <span className={`ws-indicator ws-${wsStatus}`} title={`WS: ${wsStatus}`}>●</span>
      </div>

      <div className="tile-video-wrapper">
        {streamError ? (
          <div className="stream-placeholder">
            <span>Stream unavailable</span>
            <button onClick={() => setStreamError(false)}>Retry</button>
          </div>
        ) : (
          <img
            src={streamUrl(cam)}
            alt="live annotated feed"
            className="video-stream"
            onError={() => setStreamError(true)}
          />
        )}
        {metrics && (
          <div className="tile-fps-overlay">
            {metrics.fps ?? '—'} fps · {metrics.e2e_ms ?? '—'} ms
          </div>
        )}
      </div>

      <AnalyticsCard
        metrics={metrics}
        lineCounters={lineCounters}
        zoneEvents={zoneEvents}
        profiles={profiles}
      />
    </section>
  )
}

function evict(map) {
  const entries = [...map.entries()].sort((a, b) => a[1].last_seen - b[1].last_seen)
  for (const [id, p] of entries) {
    if (p.status === 'final') { map.delete(id); return }
  }
  for (const [id] of entries) {
    map.delete(id); return
  }
}
