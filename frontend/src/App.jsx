import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import "./App.css";

const API = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
const apiUrl = (path) => `${API}${path}`;

function Metric({ label, value }) {
  return <div className="metric"><span>{label}</span><strong>{value ?? "—"}</strong></div>;
}

function ResultsTable({ rows = [] }) {
  if (!rows.length) return <div className="empty">Recognized plates will appear here.</div>;
  return <div className="table-wrap"><table><thead><tr><th>Track</th><th>Plate</th><th>Category</th><th>Confidence</th><th>Status</th></tr></thead><tbody>
    {rows.map((row) => <tr key={row.track_id}><td>#{row.track_id}</td><td className="plate">{row.plate_text || row.text || "Pending"}</td><td>{row.plate_category || row.category}</td><td>{Math.round((row.confidence || 0) * 100)}%</td><td><span className={`tag ${row.status}`}>{row.status}</span></td></tr>)}
  </tbody></table></div>;
}

function App() {
  const [mode, setMode] = useState("video");
  const [backend, setBackend] = useState("checking");
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [maxFrames, setMaxFrames] = useState("");
  const [videoBusy, setVideoBusy] = useState(false);
  const [videoResult, setVideoResult] = useState(null);
  const [colorFile, setColorFile] = useState(null);
  const [colorBusy, setColorBusy] = useState(false);
  const [colorResult, setColorResult] = useState(null);
  const [classFile, setClassFile] = useState(null);
  const [classBusy, setClassBusy] = useState(false);
  const [classResult, setClassResult] = useState(null);
  const [rtsp, setRtsp] = useState({ status: "idle", results: [] });
  const [rtspForm, setRtspForm] = useState({ url: "", camera_id: "entrance-1", max_seconds: "", record: true });
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    axios.get(apiUrl("/api/license-plate/health/"))
      .then(() => setBackend("online")).catch(() => setBackend("offline"));
  }, []);
  useEffect(() => {
    if (mode !== "rtsp") return undefined;
    const poll = () => axios.get(apiUrl("/api/license-plate/rtsp/"))
      .then(({ data }) => setRtsp(data)).catch(() => {});
    poll(); const id = setInterval(poll, 1200); return () => clearInterval(id);
  }, [mode]);

  const videoRows = useMemo(() => Object.entries(videoResult?.tracks || {}).map(([track_id, row]) => ({ track_id, ...row })), [videoResult]);
  async function analyzeVideo(event) {
    event.preventDefault();
    if (!file) return setError("Choose a video before starting the analysis.");
    setError(""); setVideoBusy(true); setVideoResult(null);
    const data = new FormData(); data.append("video", file); if (maxFrames) data.append("max_frames", maxFrames);
    try { setVideoResult((await axios.post(apiUrl("/api/license-plate/analyze/"), data)).data); }
    catch (requestError) { setError(requestError.response?.data?.error || "The video analysis failed."); }
    finally { setVideoBusy(false); }
  }
  async function startRtsp(event) {
    event.preventDefault(); setError("");
    try { setRtsp((await axios.post(apiUrl("/api/license-plate/rtsp/"), rtspForm)).data); }
    catch (requestError) { setError(requestError.response?.data?.error || "Could not start the camera."); }
  }
  async function stopRtsp() { setRtsp((await axios.post(apiUrl("/api/license-plate/rtsp/stop/"))).data); }
  async function analyzeColor(event) {
    event.preventDefault(); if (!colorFile) return setError("Choose a video before starting colour analysis.");
    setError(""); setColorBusy(true); setColorResult(null); const data = new FormData(); data.append("video", colorFile);
    try { setColorResult((await axios.post(apiUrl("/api/color/analyze/"), data)).data); }
    catch (requestError) { setError(requestError.response?.data?.error || "The colour analysis failed."); }
    finally { setColorBusy(false); }
  }
  async function analyzeClassification(event) {
    event.preventDefault(); if (!classFile) return setError("Choose a video before starting classification.");
    setError(""); setClassBusy(true); setClassResult(null); const data = new FormData(); data.append("video", classFile);
    try { setClassResult((await axios.post(apiUrl("/api/classification-manufacturer/analyze/"), data)).data); }
    catch (requestError) { setError(requestError.response?.data?.error || "The classification failed."); }
    finally { setClassBusy(false); }
  }
  const preview = rtsp.running || rtsp.processed_frames ? `${apiUrl("/api/license-plate/rtsp/preview/")}?frame=${rtsp.processed_frames || 0}` : "";

  return <div className="shell">
    <aside><a className="brand" href="#top"><span className="brand-mark">V</span><span>VEHICLE<b>AI</b></span></a>
      <nav><button className="nav-active"><span>⌁</span> License plates</button><button disabled><span>◇</span> Classification</button><button disabled><span>◐</span> Vehicle color</button></nav>
      <div className="system"><p>System status</p><div><i className={backend}></i><span>Backend {backend}</span></div><small>License Plate Service</small></div>
    </aside>
    <main id="top"><header><div><p className="eyebrow">Computer vision workspace</p><h1>License plate recognition</h1><p>Detect, track and recognize Tunisian plates from recorded video or a live camera feed.</p></div><div className="status-pill"><i></i> Service ready</div></header>
      <div className="tabs"><button className={mode === "video" ? "active" : ""} onClick={() => { setMode("video"); setError(""); }}>License plates</button><button className={mode === "color" ? "active" : ""} onClick={() => { setMode("color"); setError(""); }}>Vehicle colour</button><button className={mode === "classification" ? "active" : ""} onClick={() => { setMode("classification"); setError(""); }}>Classification</button><button className={mode === "rtsp" ? "active" : ""} onClick={() => { setMode("rtsp"); setError(""); }}>Live RTSP camera</button></div>
      {error && <div className="alert">{error}<button onClick={() => setError("")}>×</button></div>}
      {mode === "video" ? <>
        <section className="workspace-card"><div className="card-head"><div><span className="step">01</span><h2>Upload footage</h2></div><p>MP4, MOV, AVI, MKV or WebM</p></div>
          <form onSubmit={analyzeVideo}><div className={`dropzone ${dragging ? "dragging" : ""} ${file ? "selected" : ""}`} onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(e) => { e.preventDefault(); setDragging(false); setFile(e.dataTransfer.files[0]); }} onClick={() => inputRef.current?.click()}>
            <input ref={inputRef} type="file" accept="video/*" hidden onChange={(e) => setFile(e.target.files[0])} /><div className="upload-icon">{file ? "✓" : "↑"}</div><strong>{file ? file.name : "Drop your video here"}</strong><span>{file ? `${(file.size / 1048576).toFixed(1)} MB · Ready to process` : "or click to browse from your computer"}</span></div>
            <div className="controls"><label><span>Frame limit <em>optional</em></span><input type="number" min="1" placeholder="Process entire video" value={maxFrames} onChange={(e) => setMaxFrames(e.target.value)} /></label><button className="primary" disabled={!file || videoBusy}>{videoBusy ? <><b className="spinner"></b> Analyzing…</> : "Run recognition →"}</button></div></form>
        </section>
        {videoResult && <section className="results"><div className="results-head"><div><p className="eyebrow">Analysis complete</p><h2>Recognition results</h2></div><a className="secondary" href={apiUrl(videoResult.output_video_url)} target="_blank" rel="noreferrer">Open in new tab ↗</a></div><div className="metrics"><Metric label="Frames processed" value={videoResult.frames} /><Metric label="Unique tracks" value={videoResult.unique_tracks} /><Metric label="Plates recognized" value={videoResult.recognized_tracks} /><Metric label="Processing FPS" value={videoResult.processing_fps} /></div><div className="video-player"><video controls playsInline preload="metadata" src={apiUrl(videoResult.output_video_url)}><track kind="captions" /><p>Your browser cannot play this video. <a href={apiUrl(videoResult.output_video_url)}>Open the annotated video</a>.</p></video></div><ResultsTable rows={videoRows} /></section>}
      </> : mode === "color" ? <>
        <section className="workspace-card"><div className="card-head"><div><span className="step">01</span><h2>Upload vehicle footage</h2></div><p>MP4, MOV, AVI, MKV or WebM</p></div>
          <form onSubmit={analyzeColor}><div className={`dropzone ${colorFile ? "selected" : ""}`} onClick={() => document.getElementById("color-file")?.click()}><input id="color-file" type="file" accept="video/*" hidden onChange={(e) => setColorFile(e.target.files[0])} /><div className="upload-icon">{colorFile ? "✓" : "↑"}</div><strong>{colorFile ? colorFile.name : "Drop your video here"}</strong><span>{colorFile ? "Ready to estimate vehicle colours" : "or click to browse from your computer"}</span></div><div className="controls"><span className="helper">Tracks vehicles and estimates dominant exterior colour over time.</span><button className="primary" disabled={!colorFile || colorBusy}>{colorBusy ? "Analyzing…" : "Estimate colours →"}</button></div></form>
        </section>
        {colorResult && <section className="results"><div className="results-head"><div><p className="eyebrow">Analysis complete</p><h2>Vehicle colour results</h2></div><a className="secondary" href={apiUrl(colorResult.output_video_url)} target="_blank" rel="noreferrer">Open annotated video ↗</a></div><div className="metrics"><Metric label="Frames processed" value={colorResult.frames} /><Metric label="Vehicles tracked" value={colorResult.vehicles?.length || 0} /><Metric label="Pipeline" value="HSV + temporal" /><Metric label="Output" value="Ready" /></div><div className="video-player"><video controls playsInline preload="metadata" src={apiUrl(colorResult.output_video_url)} /></div><ResultsTable rows={(colorResult.vehicles || []).map((item) => ({ ...item, plate_text: item.colour, plate_category: item.type, confidence: item.colour_confidence }))} /></section>}
      </> : mode === "classification" ? <>
        <section className="workspace-card"><div className="card-head"><div><span className="step">01</span><h2>Upload vehicle footage</h2></div><p>MP4, MOV, AVI, MKV or WebM</p></div>
          <form onSubmit={analyzeClassification}><div className={`dropzone ${classFile ? "selected" : ""}`} onClick={() => document.getElementById("classification-file")?.click()}><input id="classification-file" type="file" accept="video/*" hidden onChange={(e) => setClassFile(e.target.files[0])} /><div className="upload-icon">{classFile ? "✓" : "↑"}</div><strong>{classFile ? classFile.name : "Drop your video here"}</strong><span>{classFile ? "Ready to classify vehicle types" : "or click to browse from your computer"}</span></div><div className="controls"><span className="helper">Detects and tracks cars, vans, buses, trucks, motorcycles and bicycles.</span><button className="primary" disabled={!classFile || classBusy}>{classBusy ? "Analyzing…" : "Classify vehicles →"}</button></div></form>
        </section>
        {classResult && <section className="results"><div className="results-head"><div><p className="eyebrow">Analysis complete</p><h2>Vehicle classification results</h2></div><a className="secondary" href={apiUrl(classResult.output_video_url)} target="_blank" rel="noreferrer">Open annotated video ↗</a></div><div className="metrics"><Metric label="Frames processed" value={classResult.frames_processed} /><Metric label="Vehicles tracked" value={classResult.tracks_detected} /><Metric label="Events" value={classResult.events?.length || 0} /><Metric label="Processing time" value={`${classResult.processing_seconds || 0}s`} /></div><div className="video-player"><video controls playsInline preload="metadata" src={apiUrl(classResult.output_video_url)} /></div><ResultsTable rows={(classResult.vehicles || []).map((item) => ({ ...item, plate_text: item.vehicle_type, plate_category: "vehicle", confidence: item.type_confidence, status: "final" }))} /></section>}
      </> : <>
        <section className="rtsp-grid"><div className="workspace-card camera-config"><div className="card-head"><div><span className="step">01</span><h2>Camera connection</h2></div><span className={`live-state ${rtsp.status}`}>{rtsp.status}</span></div>
          <form onSubmit={startRtsp}><label><span>RTSP URL</span><input required type="password" autoComplete="off" placeholder="rtsp://user:password@camera:554/stream" value={rtspForm.url} onChange={(e) => setRtspForm({ ...rtspForm, url: e.target.value })} /></label><div className="form-row"><label><span>Camera ID</span><input value={rtspForm.camera_id} onChange={(e) => setRtspForm({ ...rtspForm, camera_id: e.target.value })} /></label><label><span>Run time <em>seconds, optional</em></span><input type="number" min="1" placeholder="Unlimited" value={rtspForm.max_seconds} onChange={(e) => setRtspForm({ ...rtspForm, max_seconds: e.target.value })} /></label></div><label className="checkbox"><input type="checkbox" checked={rtspForm.record} onChange={(e) => setRtspForm({ ...rtspForm, record: e.target.checked })} /><span>Record annotated stream</span></label><div className="camera-actions"><button className="primary" disabled={rtsp.running}>Connect camera →</button><button type="button" className="danger" disabled={!rtsp.running} onClick={stopRtsp}>Stop session</button></div></form>
        </div><div className="monitor"><div className="monitor-bar"><span><i className={rtsp.running ? "pulse" : ""}></i>{rtsp.camera_id || "Camera preview"}</span><small>{rtsp.processed_frames || 0} frames</small></div>{preview ? <img src={preview} alt="Live annotated camera feed" onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} onLoad={(e) => { e.currentTarget.style.visibility = "visible"; }} /> : <div className="no-signal"><span>⌁</span><strong>No camera connected</strong><small>Your annotated feed will appear here</small></div>}</div></section>
        <section className="results live-results"><div className="results-head"><div><p className="eyebrow">Live detections</p><h2>Plate activity</h2></div>{rtsp.recording_url && <a className="secondary" href={apiUrl(rtsp.recording_url)}>Open recording ↗</a>}</div><div className="metrics"><Metric label="Frames" value={rtsp.processed_frames || 0} /><Metric label="Active tracks" value={rtsp.active_tracks || 0} /><Metric label="Recognized" value={rtsp.recognized_tracks || rtsp.tracks_recognized || 0} /><Metric label="Processing FPS" value={rtsp.processing_fps || 0} /></div><ResultsTable rows={rtsp.results} /></section>
      </>}
    </main></div>;
}
export default App;
