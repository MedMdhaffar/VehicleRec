export default function MetricsStrip({ metrics }) {
  const fps = metrics ? metrics.fps.toFixed(1) : '—'
  const e2e = metrics ? `${Math.round(metrics.e2e_ms)}ms` : '—'
  const ocr = metrics ? `${metrics.ocr_rate.toFixed(1)}/s` : '—'
  const tracks = metrics ? metrics.active_tracks : '—'

  return (
    <div className="metrics-strip">
      <span className="metric"><span className="val">{fps}</span> <span className="lbl">FPS</span></span>
      <span className="sep">·</span>
      <span className="metric"><span className="val">{e2e}</span> <span className="lbl">e2e</span></span>
      <span className="sep">·</span>
      <span className="metric"><span className="val">{ocr}</span> <span className="lbl">OCR</span></span>
      <span className="sep">·</span>
      <span className="metric"><span className="val">{tracks}</span> <span className="lbl">tracks</span></span>
    </div>
  )
}
