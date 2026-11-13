import { useEffect, useState } from "react";
import "./App.css";
import { cleanupLabel, holdsActiveSlot } from "./runLifecycle.js";
import { cropPath, plateReasonLabel, sourceFrame } from "./cropEvidence.js";
import { samplingDefaults, validInterval, samplingLabel } from "./frameSampling.js";

// The dev server proxies API requests so one SSH forward serves the console.
// Production builds retain the configured deployment API origin.
const API = (import.meta.env.DEV ? "" : (import.meta.env.VITE_API_URL || "")).replace(/\/$/, "");
const endpoint = (path) => `${API}/api/t1/runs/${path}`;
const label = (value) => value ? value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "Unknown";
const deliveryLabel = { not_configured: "Not configured", not_attempted: "Not attempted", acknowledged: "Acknowledged", committed: "Committed", no_deliveries: "No deliveries", pending: "Pending", disabled: "Disabled" };

function Brand({ result }) {
  const status = result?.status;
  const names = { disabled: "Disabled", unknown: "Unknown", timed_out: "Timed out", malformed: "Unavailable", failed: "Unavailable", execution_uncertain: "Uncertain" };
  const make = result?.make;
  const name = status === "accepted" ? (["bmw", "gmc", "mg", "byd"].includes(make) ? make.toUpperCase() : label(make)) : (names[status] || "Pending");
  return <div className="brand-result"><strong>{name}</strong>{status !== "disabled" && <span className="review-tag">Review only</span>}</div>;
}

function TrackCrop({ runId, trackId, kind, crop }) {
  const path = cropPath(runId, trackId, kind, crop);
  const [failedPath, setFailedPath] = useState(null);
  const available = path && failedPath !== path;
  const origin = kind === "plate" ? "Selected plate" : crop?.source === "manufacturer" ? "Manufacturer input" : "Vehicle observation";
  return <div className={`track-crop ${kind}`}>
    {available ? <img src={`${API}${path}`} alt={`${origin} crop for vehicle track ${trackId}`} loading="lazy" onError={() => setFailedPath(path)} /> : <span className="crop-missing">Crop unavailable</span>}
    {available && <small>{origin}</small>}<small>{sourceFrame(crop?.frame)}</small>
  </div>;
}

function PlateCrop({ row }) {
  const plate = row.plate;
  if (cropPath(row.run_id, row.track_id, "plate", plate.crop)) return <TrackCrop runId={row.run_id} trackId={row.track_id} kind="plate" crop={plate.crop} />;
  return <div className="plate-crop-evidence"><span>{plateReasonLabel(plate)}</span><small>{sourceFrame(plate.diagnostic?.frame)}</small></div>;
}

function App() {
  const [kind, setKind] = useState("mp4");
  const [file, setFile] = useState(null);
  const [uri, setUri] = useState("");
  const [transport, setTransport] = useState("tcp");
  const [manufacturer, setManufacturer] = useState(false);
  const [intervals, setIntervals] = useState(samplingDefaults);
  const sampleEvery = intervals[kind];
  const [maxSeconds, setMaxSeconds] = useState("");
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState("");
  const [backend, setBackend] = useState("checking");
  const [stack, setStack] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previewFailed, setPreviewFailed] = useState(false);
  const run = runs.find((r) => r.run_id === selected) || (!selected ? runs[0] : null);
  const active = runs.find(holdsActiveSlot);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    async function poll() {
      try {
        const response = await fetch(endpoint(""), { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("backend unavailable");
        const data = await response.json();
        const stackResponse = await fetch(`${API}/api/t1/stack/`, { signal: controller.signal, cache: "no-store" });
        const stackData = stackResponse.ok ? await stackResponse.json() : null;
        if (alive) { setRuns(data.runs); setBackend("online"); setStack(stackData); }
      } catch { if (alive) setBackend("offline"); }
    }
    poll();
    const timer = setInterval(poll, 900);
    return () => { alive = false; controller.abort(); clearInterval(timer); };
  }, []);

  async function start(event) {
    event.preventDefault();
    if (kind === "mp4" && !file) return setError("Choose an MP4 before starting.");
    if (kind === "rtsp" && !uri) return setError("Enter the camera URI for this Start.");
    if (!validInterval(sampleEvery)) return setError("Analyze every Nth frame must be an integer of at least 1.");
    setBusy(true); setError(""); setPreviewFailed(false);
    let body;
    const headers = {};
    if (kind === "mp4") {
      body = new FormData();
      body.append("video", file); body.append("source_kind", kind);
      body.append("manufacturer_enabled", String(manufacturer));
      body.append("sample_every", String(Number(sampleEvery)));
      if (maxSeconds !== "") body.append("max_seconds", String(maxSeconds));
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify({ source_kind: kind, rtsp_uri: uri, rtsp_transport: transport, manufacturer_enabled: manufacturer, sample_every: Number(sampleEvery), max_seconds: maxSeconds === "" ? null : Number(maxSeconds) });
    }
    // The URI is transient, body-only, and is never persisted in browser storage.
    setUri("");
    try {
      const response = await fetch(endpoint(""), { method: "POST", headers, body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not start the run.");
      setRuns((previous) => [data, ...previous.filter((r) => r.run_id !== data.run_id)]);
      setSelected(data.run_id);
    } catch (failure) { setError(failure.message || "Could not start. Check the input, backend connection, and run limits."); }
    finally { setBusy(false); }
  }
  async function stop() {
    if (!run) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(endpoint(`${run.run_id}/stop/`), { method: "POST" });
      if (!response.ok) throw new Error("stop failed");
      const data = await response.json();
      setRuns((previous) => previous.map((r) => r.run_id === data.run_id ? data : r));
    } catch { setError("Could not request Stop. The run status will continue updating."); }
    finally { setBusy(false); }
  }
  const rows = run?.rows || [];
  const running = holdsActiveSlot(run);
  const cleanup = cleanupLabel(run);
  return <div className="console-shell">
    <aside className="sidebar"><a className="wordmark" href="#main"><span className="mark">W</span><span>WAYCON<small>VEHICLE INTELLIGENCE</small></span></a>
      <div className="workspace-label">WORKSPACE</div><div className="nav-item"><span>▤</span> Vehicle Console <i /></div>
      <div className="sidebar-note"><span className="signal-dot" /> T1 connected pipeline<p>One input. One vehicle identity.<br />Joined recognition results.</p></div>
      <div className="backend-state"><span className={`signal-dot ${backend}`} /><span>Backend {backend}</span><small>Local processing</small></div>
    </aside>
    <main id="main"><header className="page-header"><div><div className="eyebrow">OPERATIONS / VEHICLES</div><h1>Vehicle Console<span>.</span></h1><p>Start a run. Follow every vehicle from frame to decision.</p></div><span className="environment">T1 · Local</span></header>
      <div className={stack?.full_stack_ready ? "run-identity" : "error-banner"} role="status" aria-label="Stack readiness">{stack?.full_stack_ready ? "Full stack ready · Disposable DB1 demo writes · Manufacturer review only" : (stack?.error || "Checking full stack readiness…")}</div>
      <div className="console-layout"><section className="panel source-panel"><div className="panel-title"><span className="step">01</span><h2>Input & controls</h2></div>
        <form onSubmit={start}><div className="source-tabs" role="tablist" aria-label="Input source"><button type="button" role="tab" aria-selected={kind === "mp4"} className={kind === "mp4" ? "selected" : ""} disabled={!!active} onClick={() => setKind("mp4")}>Recorded MP4</button><button type="button" role="tab" aria-selected={kind === "rtsp"} className={kind === "rtsp" ? "selected" : ""} disabled={!!active} onClick={() => setKind("rtsp")}>RTSP camera</button></div>
          {kind === "mp4" ? <label className="file-picker"><span className="file-symbol">↥</span><strong>{file?.name || "Choose footage"}</strong><span>{file ? `${(file.size / 1048576).toFixed(1)} MB · MP4` : "Select an MP4 from your computer"}</span><input aria-label="MP4 file" type="file" accept="video/mp4,.mp4" disabled={!!active} onChange={(e) => setFile(e.target.files[0] || null)} /></label> : <div className="camera-input"><label htmlFor="camera-uri">Camera URI</label><input id="camera-uri" aria-label="RTSP URI" required type="password" autoComplete="new-password" spellCheck={false} placeholder="Enter URI for this Start" disabled={!!active} value={uri} onChange={(e) => setUri(e.target.value)} /><p>Sent privately to the backend. Cleared on Start. Enter a new URI for each run.</p><label htmlFor="rtsp-transport">RTSP transport</label><select id="rtsp-transport" aria-label="RTSP transport" value={transport} disabled={!!active} onChange={(e) => setTransport(e.target.value)}><option value="tcp">TCP</option><option value="udp">UDP</option></select></div>}
          <div className="run-limits"><label>Analyze every Nth frame<input aria-label="Analyze every Nth frame" type="number" min="1" step="1" required value={sampleEvery} disabled={!!active} onChange={(e) => setIntervals({ ...intervals, [kind]: e.target.value })} /></label><label>Timeout · optional seconds<input aria-label="Timeout" type="number" min="1" step="1" placeholder="No timeout" value={maxSeconds} disabled={!!active} onChange={(e) => setMaxSeconds(e.target.value)} /></label></div>
          <div className="recognition-option"><div><label htmlFor="manufacturer-toggle">Manufacturer recognition</label><small>Local model · predictions are review only</small></div><input id="manufacturer-toggle" type="checkbox" role="switch" checked={manufacturer} disabled={!!active} onChange={(e) => setManufacturer(e.target.checked)} /></div>
          <div className="action-row"><button className="start-button" type="submit" disabled={busy || !!active || backend !== "online" || (stack?.mode === "fixture" && !stack.full_stack_ready) || (kind === "mp4" && !file)}>▶ Start run</button><button className="stop-button" type="button" onClick={stop} disabled={busy || !running}>■ Stop</button></div>
          <p className="bounded-note">MP4 runs through EOF. Stop or an optional timeout ends a partial run.</p>
        </form>
      </section>
      <section className="panel preview-panel"><div className="panel-title"><span className="step">02</span><h2>Backend preview</h2><span className={`run-badge ${run?.status || "idle"}`}>{label(run?.status || "idle")}</span></div>
        <div className="preview-stage">{run?.preview_revision > 0 && !previewFailed ? <img key={run.run_id} src={endpoint(`${run.run_id}/preview/?revision=${run.preview_revision}`)} alt={`Backend frame preview for run ${run.run_id}`} onError={() => setPreviewFailed(true)} /> : <div className="preview-empty"><span>⌗</span><strong>{running ? "Preparing your feed" : "Ready when you are"}</strong><p>{running ? run.phase : "Your backend frame preview appears here after Start."}</p></div>}<div className="preview-caption"><span>{run ? `${run.source_kind.toUpperCase()} · ${run.source_kind === "rtsp" ? "Live source frames" : "Backend frames"}` : "NO ACTIVE INPUT"}</span><span>{run ? `${run.decoded_frames ?? run.frames} decoded · ${run.analyzed_frames ?? run.frames} analyzed` : "—"}</span></div></div>
        <div className="run-progress" aria-live="polite"><span className={running ? "activity-dot" : ""} />{run?.phase || "Choose an input to begin."}</div>
      </section></div>
      {run && <div className="run-identity" aria-label="Frame counters"><span>Decoded: {run.decoded_frames ?? run.frames}</span><span>Analyzed: {run.analyzed_frames ?? run.frames}</span><span>Sampling skips: {run.sampling_skips ?? "Unknown (historical)"}</span>{run.source_kind === "rtsp" && <span>Overload drops: {run.overload_drops ?? "Unknown (historical)"}</span>}<span>{run.sample_every ? `Every ${run.sample_every} frame(s)` : "Historical frame-limit run"}</span><span>{run.end_reason ? `${run.full_video_complete ? "Full video complete" : "Partial / stream ended"} · ${label(run.end_reason)}` : ""}</span></div>}
      {error && <div className="error-banner" role="alert">{error}<button aria-label="Dismiss error" onClick={() => setError("")}>×</button></div>}
      {run?.error_code && <div className="error-banner" role="alert">{run.error_code === "input_unavailable" ? "The input could not be opened. Check the camera connection and start a new run." : "The backend could not finish this run. Its status and completed results are retained."}</div>}
      {run?.error_code === "db1_delivery_failed" && <div className="error-banner" role="alert">DB1 delivery failed. The full stack is not ready. Commit and ACK states show the last confirmed boundary.</div>}
      <section className="delivery-strip" aria-label="Delivery states"><div><span>POSTGRESQL COMMIT</span><strong>{deliveryLabel[run?.delivery.postgres_commit] || "Not configured"}</strong></div><div><span>REDIS DB1 ACK</span><strong>{deliveryLabel[run?.delivery.redis_ack] || "Not attempted"}</strong></div><div><span>MANUFACTURER REVIEW HANDOFF</span><strong>{run ? deliveryLabel[run.delivery.review_ack] : "—"}</strong></div><p>Recognition and database delivery are separate. Manufacturer predictions remain review only.</p></section>
      <section className="panel results-panel"><div className="results-heading"><div><div className="eyebrow">JOINED BY VEHICLE TRACK</div><h2>Vehicle activity <span className="count">{rows.length}</span></h2></div><label className="run-select">Run history<select aria-label="Run history" value={run?.run_id || ""} onChange={(e) => { setSelected(e.target.value); setPreviewFailed(false); }}><option value="" disabled>No runs yet</option>{runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.source_kind.toUpperCase()} · {r.run_id.slice(0,8)} · {label(r.status)} · {samplingLabel(r)}</option>)}</select></label></div>
        {run && <div className="run-identity"><span>RUN</span><code>{run.run_id}</code><span>·</span><span>{new Date(run.started_at).toLocaleString()}</span><span>· Manufacturer {run.manufacturer_enabled ? "on" : "off"} for this run</span></div>}
        <div className="table-scroll"><table><thead><tr><th>Vehicle track</th><th>Type</th><th>Colour</th><th>Plate decision</th><th>Manufacturer</th><th>PostgreSQL commit</th><th>Redis DB1 ACK</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.run_id}:${row.track_id}`} data-track-id={row.track_id}><td><strong className="track-id">#{row.track_id}</strong><small>Frames {row.first_frame}–{row.last_frame}</small><TrackCrop runId={row.run_id} trackId={row.track_id} kind="vehicle" crop={row.vehicle_crop} /></td><td>{label(row.vehicle_type)}</td><td><span className="colour-label"><i style={{ background: row.colour.label || "transparent" }} />{label(row.colour.label)}</span><small>{row.colour.status === "undecided" ? "Undecided" : ""}</small></td><td><span className={`decision ${row.plate.status}`}>{label(row.plate.status)}</span>{row.plate.category && <small>{label(row.plate.category)}</small>}<PlateCrop row={row} /></td><td><Brand result={row.manufacturer} /></td><td className="delivery-cell">{deliveryLabel[row.delivery.postgres_commit]}</td><td className="delivery-cell">{deliveryLabel[row.delivery.redis_ack]}</td></tr>)}</tbody></table>{!rows.length && <div className="results-empty"><span>◎</span><h3>{running ? "Following vehicle tracks…" : "No vehicle results yet"}</h3><p>{running ? "Results arrive as the backend closes and joins each vehicle lifetime." : "Run an MP4 or RTSP source to see actual backend decisions."}</p></div>}</div>
        <footer className="results-footer"><span>One row per vehicle lifetime · No generated confidence scores</span><span role="status" className={cleanup === "Cleanup needs attention" ? "cleanup-warning" : ""}>{cleanup}</span></footer>
      </section><footer className="page-footer"><span>WAYCON / VEHICLE INTELLIGENCE</span><span>MP4 + RTSP · T1 backend</span></footer>
    </main>
  </div>;
}
export default App;
