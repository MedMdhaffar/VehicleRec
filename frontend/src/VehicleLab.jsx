import { useEffect, useRef, useState } from "react";
import axios from "axios";
import "./PlateLab.css";
import "./VehicleLab.css";

const readable = (value) => (value || "unknown").replaceAll("_", " ");

export default function VehicleLab({ apiUrl, active }) {
  const videoRef = useRef(null);
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [schema, setSchema] = useState(null);
  const [settings, setSettings] = useState({});
  const [maxFrames, setMaxFrames] = useState("");
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!active) videoRef.current?.pause();
  }, [active, result]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      axios.get(apiUrl("/api/classification-manufacturer/jobs/"), { signal: controller.signal }),
      axios.get(apiUrl("/api/classification-manufacturer/settings/"), { signal: controller.signal }),
    ])
      .then(([saved, config]) => {
        setJobs(saved.data);
        setSchema(config.data);
        setSettings(config.data.defaults);
      })
      .catch((reason) => {
        if (!axios.isCancel(reason))
          setError("Could not load classification settings or saved runs. Reload to try again.");
      });
    return () => controller.abort();
  }, [apiUrl]);

  async function analyze(event) {
    event.preventDefault();
    if (!file || busy || !schema) return;
    setBusy(true);
    setError("");
    setResult(null);
    const data = new FormData();
    data.append("video", file);
    data.append("enable_manufacturer", "true");
    data.append("model_settings", JSON.stringify(settings));
    if (maxFrames) data.append("max_frames", maxFrames);
    try {
      const response = await axios.post(
        apiUrl("/api/classification-manufacturer/analyze/"),
        data,
      );
      setResult(response.data);
      setJobs((previous) =>
        [
          {
            job_id: response.data.job_id,
            report_url: response.data.report_url,
            tracks_detected: response.data.tracks_detected,
          },
          ...previous,
        ].slice(0, 50),
      );
    } catch (reason) {
      setError(reason.response?.data?.error || "Vehicle analysis failed.");
    } finally {
      setBusy(false);
    }
  }

  async function openJob(url) {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await axios.get(apiUrl(url));
      setResult(response.data);
    } catch {
      setError("Could not open the saved vehicle analysis.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="plate-lab vehicle-lab">
      <div className="lab-heading">
        <div>
          <span className="lab-kicker"><span /> VEHICLE INTELLIGENCE</span>
          <h2>Every vehicle. <span>A clearer picture.</span></h2>
          <p>Discover the manufacturer, body type and color. Explore the evidence behind every prediction.</p>
        </div>
        <div className="vehicle-model-badge">
          <span className="vehicle-scan-icon" aria-hidden="true">✦</span>
          <div>LFM2.5-VL <small>450M · Local vision model</small></div>
        </div>
      </div>
      <div className="lab-runbar">
        <div className="lab-run-select">
          <label className="lab-label" htmlFor="vehicle-jobs">SAVED RUNS</label>
          <select
            id="vehicle-jobs"
            value={result?.report_url || ""}
            disabled={busy || !jobs.length}
            onChange={(event) => openJob(event.target.value)}
          >
            <option value="" disabled>{jobs.length ? "Open a completed run" : "No completed runs yet"}</option>
            {jobs.map((job) => (
              <option key={job.job_id} value={job.report_url}>
                {job.job_id.slice(0, 8)} · {job.tracks_detected} vehicles
              </option>
            ))}
          </select>
        </div>
        <span className="lab-run-count">{jobs.length} saved runs</span>
      </div>
      <form onSubmit={analyze} className="vehicle-setup">
        <section className="vehicle-source">
          <div className="vehicle-panel-heading">
            <span className="vehicle-step">01</span>
            <div><h3>Your footage</h3><p>Start with a video to inspect its vehicles.</p></div>
          </div>
          <input
            ref={inputRef}
            id="vehicle-video"
            type="file"
            accept="video/*,.mkv,.avi"
            hidden
            disabled={busy}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
          <button
            type="button"
            className={`vehicle-dropzone ${dragging ? "is-dragging" : ""}`}
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (!busy) setFile(event.dataTransfer.files?.[0] || null);
            }}
          >
            <span className="vehicle-upload-symbol" aria-hidden="true">{file ? "✓" : "↑"}</span>
            <strong>{file ? file.name : "Drop your video here"}</strong>
            <span>{file ? `${(file.size / 1048576).toFixed(1)} MB · Click to replace` : "or click to browse your files"}</span>
            <small>MP4 · MOV · AVI · MKV · WebM</small>
          </button>
          <div className="vehicle-source-footer">
            <label htmlFor="vehicle-max-frames">Frame limit <small>Leave blank for the entire video</small></label>
            <input id="vehicle-max-frames" type="number" min="1" step="1" placeholder="All frames" value={maxFrames} disabled={busy}
              onChange={(event) => setMaxFrames(event.target.value)} />
          </div>
          <div className="vehicle-pipeline"><span>01 Detect &amp; track</span><b>→</b><span>02 Select crops</span><b>→</b><span>03 Recognize</span></div>
        </section>
        <section className="vehicle-settings">
          <div className="vehicle-panel-heading">
            <span className="vehicle-step">02</span>
            <div><h3>Model parameters</h3><p>Fine-tune the next analysis.</p></div>
            <button className="vehicle-reset" type="button" disabled={busy || !schema} onClick={() => setSettings({ ...schema.defaults })}>Reset</button>
          </div>
          <fieldset disabled={busy || !schema}>
            {!schema && <p className="helper" role="status">Loading model settings…</p>}
            {(schema?.fields || []).map((field) => (
              <label className="vehicle-setting" key={field.key} htmlFor={`vehicle-${field.key}`}>
                <span>{field.label}<small>{field.description}</small></span>
                <input id={`vehicle-${field.key}`} type="number" required min={field.min} max={field.max} step={field.step}
                  value={settings[field.key] ?? ""}
                  onChange={(event) => setSettings((previous) => ({ ...previous, [field.key]: event.target.value === "" ? "" : Number(event.target.value) }))} />
              </label>
            ))}
          </fieldset>
          <p className="vehicle-settings-note">Manufacturer, body type and color are predicted by the VLM. Uncertain attributes remain unknown.</p>
          <button className="lab-button lab-button-primary vehicle-analyze" disabled={!file || busy || !schema}>
            {busy ? "Analysis in progress…" : "Analyze vehicles →"}
          </button>
        </section>
      </form>
      {busy && <div className="vehicle-progress" role="status"><span className="lab-spinner" /><div><strong>Preparing your analysis</strong><p>You can switch tabs. Your run will continue here.</p></div></div>}
      {error && <div className="vehicle-error" role="alert">{error}</div>}
      {!result && !busy && <div className="vehicle-welcome">
        <span className="vehicle-scan-icon" aria-hidden="true">⌗</span>
        <span className="lab-label">FROM FOOTAGE TO UNDERSTANDING</span>
        <h3>One run. Three perspectives.</h3>
        <p>Upload a video or open a saved run to explore manufacturers, vehicle types and body colors alongside their source crops.</p>
        <div><span>Manufacturer</span><span>Body type</span><span>Body color</span></div>
      </div>}
      {result && (
        <section className="vehicle-results">
          <div className="results-head">
            <div><span className="lab-kicker">ANALYSIS COMPLETE</span><h2>Vehicle recognition results</h2></div>
            <a
              className="secondary"
              href={apiUrl(result.report_url)}
              target="_blank"
              rel="noreferrer"
            >
              Download JSON ↗
            </a>
          </div>
          <div className="vehicle-stats">
            {[["VEHICLE TRACKS", result.tracks_detected], ["FRAMES PROCESSED", result.frames_processed], ["PROCESSING TIME", `${result.processing_seconds}s`]].map(([label, value]) => (
              <div key={label}><span className="lab-label">{label}</span><strong>{value ?? "—"}</strong></div>
            ))}
          </div>
          {result.model_settings && <details className="vehicle-run-settings">
            <summary>Settings used for this run</summary>
            <dl>{Object.entries(result.model_settings).map(([key, value]) => <div key={key}><dt>{schema?.fields.find((field) => field.key === key)?.label || readable(key)}</dt><dd>{String(value)}</dd></div>)}</dl>
          </details>}
          {result.output_video_url && (
            <div className="video-player">
              <video
                ref={videoRef}
                key={result.job_id}
                controls
                playsInline
                preload="metadata"
                src={apiUrl(result.output_video_url)}
              />
            </div>
          )}
          {!result.vehicles?.length && (
            <div className="empty">No vehicle tracks found in this video.</div>
          )}
          {(result.vehicles || []).map((vehicle) => (
            <article
              className="vehicle-track"
              key={`${vehicle.track_id}-${vehicle.first_frame}`}
            >
              <div className="vehicle-track-heading"><h3>Track #{vehicle.track_id}</h3><span className={`vehicle-status ${vehicle.recognition_status}`}>{readable(vehicle.recognition_status)}</span></div>
              <div className="vehicle-attributes">
                {[["Manufacturer", vehicle.manufacturer], ["Body type", vehicle.vlm_vehicle_type], ["Body color", vehicle.color]].map(([label, value]) => (
                  <div key={label}><span>{label}</span><strong>{label === "Body color" && <i className={`vehicle-color color-${value}`} />}{readable(value)}</strong></div>
                ))}
              </div>
              <p>
                Attempts:{" "}
                {vehicle.recognition_attempts ?? 0} · Supporting crops:{" "}
                {vehicle.supporting_crops ?? 0}
              </p>
              {(vehicle.recognition_reason || vehicle.recognition_failure) && (
                <p>
                  {readable(
                    vehicle.recognition_reason || vehicle.recognition_failure,
                  )}
                </p>
              )}
              <div className="vehicle-crops">
                {Object.entries(vehicle.selected_crops || {})
                  .filter(([label]) => ["early", "best"].includes(label))
                  .map(([label, url]) => (
                    <figure key={label}>
                      <a href={apiUrl(url)} target="_blank" rel="noreferrer">
                        <img
                          src={apiUrl(url)}
                          loading="lazy"
                          alt={`${label} whole-vehicle crop for track ${vehicle.track_id}`}
                        />
                      </a>
                      <figcaption>{readable(label)} crop</figcaption>
                    </figure>
                  ))}
              </div>
              <details>
                <summary>VLM recognition evidence</summary>
                <p>
                  Model: {vehicle.recognition_model || "disabled"} · Inference:{" "}
                  {(vehicle.recognition_seconds || 0).toFixed(2)}s
                </p>
                <p>
                  Brand support: {vehicle.manufacturer_supporting_crops ?? 0} ·
                  Body support: {vehicle.body_type_supporting_crops ?? 0} ·
                  Color support: {vehicle.color_supporting_crops ?? 0}
                </p>
                <div className="vehicle-crops">
                  {(vehicle.recognition_evidence || []).map(
                    (attempt, index) => (
                      <figure key={index}>
                        {attempt.crop_url && (
                          <img
                            loading="lazy"
                            src={apiUrl(attempt.crop_url)}
                            alt={`Submitted crop ${index + 1} for track ${vehicle.track_id}`}
                          />
                        )}
                        <figcaption>
                          Attempt {index + 1} · Frame {attempt.frame_index}:{" "}
                          {attempt.error ||
                            `${readable(attempt.result?.manufacturer)} / ${readable(attempt.result?.vehicle_type)} / ${readable(attempt.result?.color)}`}
                        </figcaption>
                        {(attempt.raw_output || attempt.result?.raw_output) && <pre>{attempt.raw_output || attempt.result.raw_output}</pre>}
                      </figure>
                    ),
                  )}
                </div>
              </details>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
