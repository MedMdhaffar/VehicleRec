import { useEffect, useState } from "react";
import axios from "axios";
import "./App.css";
import PlateLab from "./PlateLab";
import VehicleLab from "./VehicleLab";

const API = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(
  /\/$/,
  "",
);
const apiUrl = (path) => `${API}${path}`;

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value ?? "—"}</strong>
    </div>
  );
}

function PlateText({ row }) {
  if (row.recognition_status && row.recognition_status !== "recognized") {
    return row.recognition_status === "error" ? "Error" : "Unresolved";
  }
  const fields = row.registration_fields;
  if (
    row.recognition_status === "recognized" &&
    row.recognition_policy === "tunis_private" &&
    fields?.series &&
    fields?.registration_number
  ) {
    return (
      <span
        dir="ltr"
        aria-label={row.normalized_number}
        style={{ display: "inline-flex", gap: "0.35em" }}
      >
        <bdi dir="ltr">{fields.series}</bdi>{" "}
        <bdi dir="rtl" lang="ar">
          تونس
        </bdi>{" "}
        <bdi dir="ltr">{fields.registration_number}</bdi>
      </span>
    );
  }
  return row.plate_text || row.text || "Pending";
}

function ResultsTable({ rows = [] }) {
  if (!rows.length)
    return <div className="empty">Recognized plates will appear here.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Track</th>
            <th>Plate</th>
            <th>Category</th>
            <th>
              {rows.some((row) => row.recognition_status !== undefined)
                ? "OCR confidence"
                : "Confidence"}
            </th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.track_id}>
              <td>#{row.track_id}</td>
              <td className="plate">
                <PlateText row={row} />
              </td>
              <td title={row.category_decision_source || ""}>
                {row.predicted_category
                  ? `${row.predicted_category} (${row.plate_category || row.category})`
                  : row.plate_category || row.category}
              </td>
              <td>
                {Math.round((row.ocr_confidence ?? row.confidence ?? 0) * 100)}%
              </td>
              <td>
                <span className={`tag ${row.status}`}>
                  {row.recognition_status || row.status}
                </span>
                {row.unresolved_reason && (
                  <small title={row.unresolved_reason}>
                    {" "}
                    {row.unresolved_reason.replaceAll("_", " ")}
                  </small>
                )}
                {row.identity_conflict && <small> Identity conflict</small>}
                {row.category_conflict && <small> Category conflict</small>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function App() {
  const [mode, setMode] = useState("lab");
  const [backend, setBackend] = useState("checking");
  const [rtsp, setRtsp] = useState({ status: "idle", results: [] });
  const [rtspForm, setRtspForm] = useState({
    url: "",
    camera_id: "entrance-1",
    max_seconds: "",
    record: true,
  });
  const [error, setError] = useState("");

  useEffect(() => {
    axios
      .get(apiUrl("/api/health/"))
      .then(() => setBackend("online"))
      .catch(() => setBackend("offline"));
  }, []);
  useEffect(() => {
    if (mode !== "rtsp") return undefined;
    const poll = () =>
      axios
        .get(apiUrl("/api/license-plate/rtsp/"))
        .then(({ data }) => setRtsp(data))
        .catch(() => {});
    poll();
    const id = setInterval(poll, 1200);
    return () => clearInterval(id);
  }, [mode]);

  async function startRtsp(event) {
    event.preventDefault();
    setError("");
    try {
      setRtsp(
        (await axios.post(apiUrl("/api/license-plate/rtsp/"), rtspForm)).data,
      );
    } catch (requestError) {
      setError(
        requestError.response?.data?.error || "Could not start the camera.",
      );
    }
  }
  async function stopRtsp() {
    setRtsp((await axios.post(apiUrl("/api/license-plate/rtsp/stop/"))).data);
  }
  const preview =
    rtsp.running || rtsp.processed_frames
      ? `${apiUrl("/api/license-plate/rtsp/preview/")}?frame=${rtsp.processed_frames || 0}`
      : "";

  return (
    <div className="shell">
      <aside>
        <a className="brand" href="#top">
          <img
            className="brand-mark"
            src={`${import.meta.env.BASE_URL}logo.svg`}
            alt=""
            width="40"
            height="40"
          />
          <span>
            VEHICLE<b>AI</b>
          </span>
        </a>
        <div className="system">
          <p>System status</p>
          <div>
            <i className={backend}></i>
            <span>Backend {backend}</span>
          </div>
          <small>Vehicle vision services</small>
        </div>
      </aside>
      <main id="top" className={mode !== "rtsp" ? "lab-workspace" : undefined}>
        <header>
          <div>
            <p className="eyebrow">Computer vision workspace</p>
            <h1>
              {mode === "classification"
                ? "Vehicle classification"
                : "License plate recognition"}
            </h1>
            <p>
              {mode === "classification"
                ? "Recognize vehicle manufacturers, body types and colors. Tune the model and inspect the evidence."
                : "Detect, track and recognize Tunisian plates from recorded video or a live camera feed."}
            </p>
          </div>
          <div className="status-pill">
            <i></i> Service ready
          </div>
        </header>
        <div className="tabs">
          <button
            className={mode === "lab" ? "active" : ""}
            onClick={() => setMode("lab")}
          >
            Plate Lab
          </button>
          <button
            className={mode === "classification" ? "active" : ""}
            onClick={() => {
              setMode("classification");
              setError("");
            }}
          >
            Classification
          </button>
          <button
            className={mode === "rtsp" ? "active" : ""}
            onClick={() => {
              setMode("rtsp");
              setError("");
            }}
          >
            Live RTSP camera
          </button>
        </div>
        {error && (
          <div className="alert">
            {error}
            <button onClick={() => setError("")}>×</button>
          </div>
        )}
        {/* Preserve in-flight analysis and results when switching tabs. */}
        <div hidden={mode !== "classification"}>
          <VehicleLab apiUrl={apiUrl} active={mode === "classification"} />
        </div>
        {mode === "lab" ? (
          <PlateLab apiUrl={apiUrl} />
        ) : mode === "classification" ? null : (
          <>
            <section className="rtsp-grid">
              <div className="workspace-card camera-config">
                <div className="card-head">
                  <div>
                    <span className="step">01</span>
                    <h2>Camera connection</h2>
                  </div>
                  <span className={`live-state ${rtsp.status}`}>
                    {rtsp.status}
                  </span>
                </div>
                <form onSubmit={startRtsp}>
                  <label>
                    <span>RTSP URL</span>
                    <input
                      required
                      type="password"
                      autoComplete="off"
                      placeholder="rtsp://user:password@camera:554/stream"
                      value={rtspForm.url}
                      onChange={(e) =>
                        setRtspForm({ ...rtspForm, url: e.target.value })
                      }
                    />
                  </label>
                  <div className="form-row">
                    <label>
                      <span>Camera ID</span>
                      <input
                        value={rtspForm.camera_id}
                        onChange={(e) =>
                          setRtspForm({
                            ...rtspForm,
                            camera_id: e.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      <span>
                        Run time <em>seconds, optional</em>
                      </span>
                      <input
                        type="number"
                        min="1"
                        placeholder="Unlimited"
                        value={rtspForm.max_seconds}
                        onChange={(e) =>
                          setRtspForm({
                            ...rtspForm,
                            max_seconds: e.target.value,
                          })
                        }
                      />
                    </label>
                  </div>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={rtspForm.record}
                      onChange={(e) =>
                        setRtspForm({ ...rtspForm, record: e.target.checked })
                      }
                    />
                    <span>Record annotated stream</span>
                  </label>
                  <div className="camera-actions">
                    <button className="primary" disabled={rtsp.running}>
                      Connect camera →
                    </button>
                    <button
                      type="button"
                      className="danger"
                      disabled={!rtsp.running}
                      onClick={stopRtsp}
                    >
                      Stop session
                    </button>
                  </div>
                </form>
              </div>
              <div className="monitor">
                <div className="monitor-bar">
                  <span>
                    <i className={rtsp.running ? "pulse" : ""}></i>
                    {rtsp.camera_id || "Camera preview"}
                  </span>
                  <small>{rtsp.processed_frames || 0} frames</small>
                </div>
                {preview ? (
                  <img
                    src={preview}
                    alt="Live annotated camera feed"
                    onError={(e) => {
                      e.currentTarget.style.visibility = "hidden";
                    }}
                    onLoad={(e) => {
                      e.currentTarget.style.visibility = "visible";
                    }}
                  />
                ) : (
                  <div className="no-signal">
                    <span>⌁</span>
                    <strong>No camera connected</strong>
                    <small>Your annotated feed will appear here</small>
                  </div>
                )}
              </div>
            </section>
            <section className="results live-results">
              <div className="results-head">
                <div>
                  <p className="eyebrow">Live detections</p>
                  <h2>Plate activity</h2>
                </div>
                {rtsp.recording_url && (
                  <a className="secondary" href={apiUrl(rtsp.recording_url)}>
                    Open recording ↗
                  </a>
                )}
              </div>
              <div className="metrics">
                <Metric label="Frames" value={rtsp.processed_frames || 0} />
                <Metric label="Active tracks" value={rtsp.active_tracks || 0} />
                <Metric
                  label="Recognized"
                  value={rtsp.recognized_tracks || rtsp.tracks_recognized || 0}
                />
                <Metric
                  label="Processing FPS"
                  value={rtsp.processing_fps || 0}
                />
              </div>
              <ResultsTable rows={rtsp.results} />
            </section>
          </>
        )}
      </main>
    </div>
  );
}
export default App;
