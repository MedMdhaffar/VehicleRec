import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import "./PlateLab.css";
import AnalysisSettings from "./AnalysisSettings";

const names = {
  idara: "Administrative",
  tunis: "Private",
  nt: "Temporary",
  shorta: "Police",
};
const readable = (value) => String(value ?? "—").replaceAll("_", " ");
const percent = (value) =>
  Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
const number = (value, suffix = "") =>
  Number.isFinite(value)
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`
    : "—";
const status = (row) =>
  row.recognition_status ||
  (row.status === "accepted" ? "recognized" : row.status) ||
  "unresolved";
const category = (row) =>
  names[row.predicted_category] ||
  row.plate_category ||
  row.category ||
  "Unknown";

function PlateNumber({ row, fallback = "Number unresolved" }) {
  const fields = row.registration_fields;
  if (
    row.recognition_policy === "tunis_private" &&
    fields?.series &&
    fields?.registration_number
  ) {
    return (
      <span dir="ltr" className="lab-private-number">
        <bdi>{fields.series}</bdi>
        <bdi dir="rtl" lang="ar">
          تونس
        </bdi>
        <bdi>{fields.registration_number}</bdi>
      </span>
    );
  }
  return (
    <bdi>{row.normalized_number || row.plate_text || row.text || fallback}</bdi>
  );
}

function Icon({ name, size = 18 }) {
  const paths = {
    play: <path d="m9 5 11 7-11 7Z" />,
    upload: (
      <>
        <path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5" />
      </>
    ),
    scan: (
      <>
        <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 12h18" />
        <rect x="7" y="8" width="10" height="8" rx="1" />
      </>
    ),
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    download: <path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4" />,
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 5 5" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.scan}
    </svg>
  );
}

function Stat({ label, value, note, accent }) {
  return (
    <div className={`lab-stat ${accent ? "lab-stat-accent" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

function Evidence({ attempt }) {
  const debug = attempt?.debug || {};
  const classification = debug.classification || {};
  const recognition = debug.number_recognition || {};
  const checks = recognition.checks || {};
  return (
    <div className="lab-evidence">
      <div className="lab-section-label">
        CATEGORY EVIDENCE <span>DINOv3 + Arabic OCR</span>
      </div>
      <dl className="lab-dl">
        <div>
          <dt>Visual prediction</dt>
          <dd>{readable(classification.visual_category)}</dd>
        </div>
        <div>
          <dt>Final category</dt>
          <dd>{readable(classification.final_category)}</dd>
        </div>
        <div>
          <dt>Decision source</dt>
          <dd>{readable(classification.decision_source)}</dd>
        </div>
        <div>
          <dt>Nearest reference</dt>
          <dd>
            {classification.nearest_reference_id
              ? `#${classification.nearest_reference_id}`
              : "—"}
          </dd>
        </div>
        <div>
          <dt>Cosine similarity</dt>
          <dd>{classification.nearest_similarity?.toFixed(4) ?? "—"}</dd>
        </div>
        <div>
          <dt>Category margin</dt>
          <dd>{classification.category_margin?.toFixed(4) ?? "—"}</dd>
        </div>
        <div>
          <dt>Arabic OCR text</dt>
          <dd dir="auto">{classification.category_only_text || "—"}</dd>
        </div>
        <div>
          <dt>NT evidence</dt>
          <dd>{readable(classification.nt_evidence)}</dd>
        </div>
      </dl>
      <p className="lab-footnote">
        Similarity is a feature comparison score, not a probability.
      </p>
      <div className="lab-section-label">
        REGISTRATION OCR <span>PP-OCRv6</span>
      </div>
      <div className="lab-raw" dir="auto">
        {debug.plate_text_raw || "No OCR text recorded"}
      </div>
      <dl className="lab-dl">
        <div>
          <dt>OCR confidence</dt>
          <dd>{percent(debug.text_confidence)}</dd>
        </div>
        <div>
          <dt>Number policy</dt>
          <dd>{readable(recognition.policy)}</dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>{readable(recognition.outcome)}</dd>
        </div>
      </dl>
      {recognition.reason && (
        <div className="lab-notice">{readable(recognition.reason)}</div>
      )}
      <div className="lab-checks">
        {Object.entries(checks)
          .filter(([, value]) => typeof value === "boolean")
          .map(([key, value]) => (
            <div key={key}>
              <span className={value ? "lab-check-pass" : "lab-check-fail"}>
                {value ? "✓" : "×"}
              </span>
              {readable(key)}
            </div>
          ))}
      </div>
      {!Object.keys(debug).length && (
        <p className="lab-footnote">
          Detailed evidence is unavailable for this attempt.
        </p>
      )}
    </div>
  );
}

export default function PlateLab({ apiUrl }) {
  const [jobs, setJobs] = useState([]);
  const [settingsSchema, setSettingsSchema] = useState(null);
  const [analysisSettings, setAnalysisSettings] = useState({});
  const [run, setRun] = useState(null);
  const [selected, setSelected] = useState("");
  const [file, setFile] = useState(null);
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [tab, setTab] = useState("evidence");
  const [attemptId, setAttemptId] = useState("");
  const [dragging, setDragging] = useState(false);
  const [playbackError, setPlaybackError] = useState(false);
  const [compare, setCompare] = useState(null);
  const input = useRef(null);
  const player = useRef(null);
  const pending = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    axios
      .get(apiUrl("/api/license-plate/settings/"), {
        signal: controller.signal,
      })
      .then(({ data }) => {
        setSettingsSchema(data);
        setAnalysisSettings(
          Object.fromEntries(
            data.fields.map((field) => [field.key, field.default]),
          ),
        );
      })
      .catch((err) => {
        if (!axios.isCancel(err))
          setError(
            "Could not load analysis defaults. Check the API connection and refresh to retry.",
          );
      });
    axios
      .get(apiUrl("/api/license-plate/jobs/"), { signal: controller.signal })
      .then(async ({ data }) => {
        setJobs(data);
        const requested = new URLSearchParams(window.location.search).get(
          "job",
        );
        const jobId = requested || data[0]?.job_id;
        if (jobId) {
          const result = await axios.get(
            apiUrl(`/api/license-plate/jobs/${jobId}/debug/`),
            { signal: controller.signal },
          );
          setRun(result.data);
        }
      })
      .catch((err) => {
        if (!axios.isCancel(err))
          setError("Could not load saved runs. Check that Django is running.");
      });
    return () => {
      controller.abort();
      pending.current?.abort();
    };
  }, [apiUrl]);
  useEffect(() => {
    if (!busy) return undefined;
    const started = Date.now();
    const interval = setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(interval);
  }, [busy]);

  const rows = useMemo(
    () =>
      Object.entries(run?.tracks || {}).map(([id, row]) => ({
        ...row,
        track_id: id,
      })),
    [run],
  );
  const visible = rows.filter(
    (row) =>
      (filter === "all" ||
        (filter === "review"
          ? status(row) !== "recognized" ||
            row.identity_conflict ||
            row.category_conflict
          : row.predicted_category === filter)) &&
      `${row.track_id} ${row.normalized_number || row.plate_text || row.text || ""} ${category(row)}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const track = rows.find((row) => row.track_id === selected) || rows[0];
  const attempts = track?.attempts || [];
  const attempt =
    attempts.find((item) => item.sample_id === attemptId) ||
    attempts.find((item) => item.sample_id === track?.sample_id) ||
    attempts.at(-1);
  const recognized = rows.filter((row) => status(row) === "recognized").length;
  const review = rows.filter(
    (row) =>
      status(row) !== "recognized" ||
      row.identity_conflict ||
      row.category_conflict,
  ).length;

  async function loadRun(jobId) {
    if (!jobId) return;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    setError("");
    setPlaybackError(false);
    try {
      const { data } = await axios.get(
        apiUrl(`/api/license-plate/jobs/${jobId}/debug/`),
        { signal: controller.signal },
      );
      setRun(data);
      setSelected("");
      setAttemptId("");
      setFilter("all");
      setQuery("");
    } catch (err) {
      if (!axios.isCancel(err))
        setError(err.response?.data?.error || "Could not open this run.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  function chooseFile(next) {
    if (!next) return;
    if (!/\.(mp4|mov|avi|mkv|webm)$/i.test(next.name)) {
      setError("Choose an MP4, MOV, AVI, MKV or WebM video.");
      return;
    }
    setFile(next);
    setError("");
  }
  async function analyze(event) {
    event.preventDefault();
    if (!file || busy || !settingsSchema) return;
    setBusy(true);
    setError("");
    setElapsed(0);
    setUpload(0);
    const data = new FormData();
    data.append("video", file);
    data.append(
      "analysis_settings",
      JSON.stringify(
        Object.fromEntries(
          Object.entries(analysisSettings).map(([key, value]) => [
            key,
            Number(value),
          ]),
        ),
      ),
    );
    if (limit) data.append("max_frames", limit);
    try {
      const result = (
        await axios.post(apiUrl("/api/license-plate/analyze/"), data, {
          onUploadProgress: (progress) =>
            setUpload(
              progress.total
                ? Math.round((progress.loaded / progress.total) * 100)
                : 0,
            ),
        })
      ).data;
      setCompare(run);
      await loadRun(result.job_id);
      setJobs((await axios.get(apiUrl("/api/license-plate/jobs/"))).data);
    } catch (err) {
      setError(
        err.response?.data?.error ||
          "Analysis failed. Check the API connection and Django logs.",
      );
    } finally {
      setBusy(false);
    }
  }
  function seek(frame) {
    if (!player.current || !run?.source_fps || frame == null) return;
    player.current.currentTime = frame / run.source_fps;
    player.current.play().catch(() => {});
  }
  function exportRun() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `plate-lab-${run.job_id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <section
      className="plate-lab"
      aria-label="License plate debugging workspace"
    >
      <div className="lab-heading">
        <div>
          <div className="lab-kicker">
            <span /> OBSERVE · UNDERSTAND · ITERATE
          </div>
          <h2>
            A closer look at every plate<span>.</span>
          </h2>
          <p>
            Your footage, every track, and the evidence behind each decision.
          </p>
        </div>
        <div className="lab-version">
          <Icon name="scan" size={25} />
          <span>
            PLATE LAB<small>Local vision workspace</small>
          </span>
        </div>
      </div>
      <div className="lab-runbar">
        <div className="lab-run-select">
          <span className="lab-label">SAVED RUNS</span>
          <select
            aria-label="Open a saved run"
            value={run?.job_id || ""}
            disabled={busy || loading}
            onChange={(event) => loadRun(event.target.value)}
          >
            <option value="">Open a previous analysis…</option>
            {jobs.map((job) => (
              <option key={job.job_id} value={job.job_id}>
                {job.name} · {new Date(job.created_at * 1000).toLocaleString()}
              </option>
            ))}
          </select>
        </div>
        <span className="lab-run-count">
          {jobs.length} saved {jobs.length === 1 ? "run" : "runs"}
        </span>
        {run && (
          <button
            type="button"
            className="lab-button lab-button-light"
            onClick={exportRun}
          >
            <Icon name="download" /> Export evidence
          </button>
        )}
      </div>
      <form
        className={`lab-upload ${dragging ? "lab-dragging" : ""}`}
        onSubmit={analyze}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!busy) chooseFile(event.dataTransfer.files[0]);
        }}
      >
        <button
          className="lab-file"
          type="button"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          <span className="lab-upload-icon">
            <Icon name="upload" size={24} />
          </span>
          <span>
            <strong>{file?.name || "Bring a new video into focus"}</strong>
            <small>
              {file
                ? `${(file.size / 1048576).toFixed(1)} MB · Click to replace`
                : "Drop footage here or browse · MP4, MOV, AVI, MKV, WebM"}
            </small>
          </span>
        </button>
        <input
          hidden
          ref={input}
          type="file"
          accept="video/*,.mkv"
          onChange={(event) => chooseFile(event.target.files[0])}
        />
        <label className="lab-frame-limit">
          <span>
            Frame limit <small>optional</small>
          </span>
          <input
            type="number"
            min="1"
            step="1"
            placeholder="Entire video"
            value={limit}
            disabled={busy}
            onChange={(event) => setLimit(event.target.value)}
          />
        </label>
        <button
          className="lab-button lab-button-primary"
          disabled={!file || busy || loading || !settingsSchema}
        >
          {busy ? (
            <>
              <span className="lab-spinner" /> Processing {elapsed}s
            </>
          ) : (
            <>
              Analyze video <Icon name="arrow" />
            </>
          )}
        </button>
        <AnalysisSettings
          schema={settingsSchema}
          values={analysisSettings}
          onChange={setAnalysisSettings}
          busy={busy}
          saved={run?.analysis_settings}
        />
        {busy && (
          <div className="lab-progress" role="status">
            <span className="lab-pulse" />
            {upload < 100
              ? `Uploading footage · ${upload}%`
              : "Detecting, tracking and recognizing plates. Larger videos can take several minutes."}
            <span>{elapsed}s elapsed</span>
          </div>
        )}
      </form>
      {error && (
        <div className="lab-error" role="alert">
          {error}
          <button
            type="button"
            aria-label="Dismiss error"
            onClick={() => setError("")}
          >
            ×
          </button>
        </div>
      )}
      {loading && (
        <div className="lab-loading" role="status">
          <span className="lab-spinner" /> Loading run evidence…
        </div>
      )}
      {!run ? (
        <div className="lab-welcome">
          <div className="lab-welcome-graphic">
            <span className="lab-corner lab-tl" />
            <span className="lab-corner lab-tr" />
            <span className="lab-corner lab-bl" />
            <span className="lab-corner lab-br" />
            <Icon name="scan" size={56} />
            <span className="lab-scan-line" />
          </div>
          <span className="lab-label">YOUR NEXT INVESTIGATION STARTS HERE</span>
          <h3>See the decision. Follow the evidence.</h3>
          <p>
            Analyze a new video or open a saved run to inspect crops, compare
            category predictions, and understand unresolved numbers.
          </p>
          <div className="lab-welcome-steps">
            {[
              "Detect & track",
              "Compare crops",
              "Inspect category",
              "Validate OCR",
            ].map((text, i) => (
              <span key={text}>
                <b>0{i + 1}</b>
                {text}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <>
          {run.analysis_settings && (
            <details className="lab-run-settings">
              <summary>
                Settings used for this run{" "}
                <span>
                  {Object.keys(run.analysis_settings).length} parameters
                </span>
              </summary>
              <dl>
                {Object.entries(run.analysis_settings).map(([key, value]) => (
                  <div key={key}>
                    <dt>
                      {settingsSchema?.fields.find((field) => field.key === key)
                        ?.label || readable(key)}
                    </dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
          <div className="lab-stats">
            <Stat
              label="TRACKED PLATES"
              value={rows.length}
              note={`${number(run.frames)} frames processed`}
            />
            <Stat
              label="NUMBERS RECOGNIZED"
              value={recognized}
              note={
                rows.length
                  ? `${Math.round((recognized / rows.length) * 100)}% of tracks`
                  : "No tracks recorded"
              }
              accent
            />
            <Stat
              label="NEEDS REVIEW"
              value={review}
              note="Unresolved or conflicting evidence"
            />
            <Stat
              label="PROCESSING SPEED"
              value={number(run.processing_fps, " fps")}
              note={`${number(run.analysis_seconds, "s")} analysis time`}
            />
          </div>
          {compare && compare.job_id !== run.job_id && (
            <div className="lab-comparison">
              <span>
                Compared with{" "}
                <strong>
                  {compare.input_name || `run ${compare.job_id.slice(0, 8)}`}
                </strong>
              </span>
              <span>
                Recognized{" "}
                {
                  Object.values(compare.tracks || {}).filter(
                    (row) => status(row) === "recognized",
                  ).length
                }{" "}
                → {recognized}
              </span>
              <span>
                Speed {number(compare.processing_fps)} →{" "}
                {number(run.processing_fps)} fps
              </span>
              <button
                type="button"
                aria-label="Dismiss comparison"
                onClick={() => setCompare(null)}
              >
                ×
              </button>
            </div>
          )}
          <div className="lab-grid">
            <div className="lab-main-column">
              <div className="lab-panel lab-viewer">
                <div className="lab-panel-head">
                  <div>
                    <span className="lab-live-dot" />
                    <strong>Footage review</strong>
                  </div>
                  <span>
                    {run.input_name || `Run ${run.job_id.slice(0, 8)}`}
                  </span>
                </div>
                <div className="lab-video-stage">
                  {run.output_video_url ? (
                    <video
                      ref={player}
                      key={run.job_id}
                      controls
                      playsInline
                      preload="metadata"
                      src={apiUrl(run.output_video_url)}
                      onError={() => setPlaybackError(true)}
                    >
                      <track kind="captions" />
                    </video>
                  ) : (
                    <div className="lab-video-missing">
                      <Icon name="play" size={35} />
                      <p>No annotated video saved for this run.</p>
                    </div>
                  )}
                </div>
                {playbackError && (
                  <p className="lab-notice">
                    Video playback unavailable.{" "}
                    <a
                      href={apiUrl(run.output_video_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open the video directly ↗
                    </a>
                  </p>
                )}
                <div className="lab-video-caption">
                  <span>ANNOTATED OUTPUT</span>
                  <span>
                    {number(run.source_fps, " source fps")} <b>·</b>{" "}
                    {number(run.duration_seconds, "s")}
                  </span>
                </div>
              </div>
              <div className="lab-panel lab-tracks">
                <div className="lab-panel-head">
                  <div>
                    <strong>Track explorer</strong>
                    <span className="lab-count">{visible.length}</span>
                  </div>
                  <span>Select a track to inspect →</span>
                </div>
                <div className="lab-track-tools">
                  <div className="lab-search">
                    <Icon name="search" size={16} />
                    <input
                      aria-label="Search tracks"
                      placeholder="Search plate, category or track…"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </div>
                  <select
                    aria-label="Filter tracks"
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                  >
                    <option value="all">All tracks</option>
                    <option value="review">Needs review</option>
                    {Object.entries(names).map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="lab-track-list">
                  {visible.map((row) => (
                    <button
                      type="button"
                      key={row.track_id}
                      className={`lab-track ${track?.track_id === row.track_id ? "lab-track-selected" : ""}`}
                      onClick={() => {
                        setSelected(row.track_id);
                        setAttemptId("");
                      }}
                    >
                      <span className="lab-track-thumb">
                        {row.crops?.recognized ||
                        row.attempts?.at(-1)?.crop_url ? (
                          <img
                            src={apiUrl(
                              row.crops?.recognized ||
                                row.attempts.at(-1).crop_url,
                            )}
                            alt={`Plate crop for track ${row.track_id}`}
                          />
                        ) : (
                          <Icon name="scan" />
                        )}
                      </span>
                      <span className="lab-track-title">
                        <small>
                          TRACK {row.track_id} <span>· {category(row)}</span>
                        </small>
                        <strong dir="auto">
                          <PlateNumber row={row} />
                        </strong>
                      </span>
                      <span className="lab-track-score">
                        {percent(row.ocr_confidence ?? row.confidence)}
                        <small>OCR confidence</small>
                      </span>
                      <span
                        className={`lab-badge lab-${row.identity_conflict || row.category_conflict ? "conflict" : status(row)}`}
                      >
                        {row.identity_conflict || row.category_conflict
                          ? "Conflict"
                          : readable(status(row))}
                      </span>
                      <span className="lab-track-chevron">↗</span>
                    </button>
                  ))}
                  {!visible.length && (
                    <div className="lab-empty">
                      {rows.length
                        ? "No tracks match this filter."
                        : "No confirmed plate tracks were recorded."}
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div className="lab-panel lab-inspector">
              {track ? (
                <>
                  <div className="lab-panel-head">
                    <div>
                      <span className="lab-inspect-icon">
                        <Icon name="scan" size={16} />
                      </span>
                      <strong>Track {track.track_id}</strong>
                    </div>
                    <span className={`lab-badge lab-${status(track)}`}>
                      {readable(status(track))}
                    </span>
                  </div>
                  <div className="lab-inspector-title">
                    <span className="lab-label">{category(track)} PLATE</span>
                    <h3 dir="auto">
                      <PlateNumber
                        row={track}
                        fallback="Awaiting a valid number"
                      />
                    </h3>
                    {track.unresolved_reason && (
                      <p>{readable(track.unresolved_reason)}</p>
                    )}
                    {(track.identity_conflict || track.category_conflict) && (
                      <div className="lab-notice">
                        Conflicting evidence recorded for this track.
                      </div>
                    )}
                  </div>
                  <div className="lab-crop-grid">
                    {[
                      ["early", "First crop"],
                      ["recognized", "Recognized"],
                      ["final_best", "Best crop"],
                    ].map(([key, label]) => (
                      <div key={key}>
                        <div>
                          {track.crops?.[key] ? (
                            <a
                              href={apiUrl(track.crops[key])}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <img
                                src={apiUrl(track.crops[key])}
                                alt={`${label} for track ${track.track_id}`}
                              />
                            </a>
                          ) : (
                            <span>Unavailable</span>
                          )}
                        </div>
                        <span>{label}</span>
                      </div>
                    ))}
                  </div>
                  <div
                    className="lab-inspector-tabs"
                    role="tablist"
                    aria-label="Track details"
                  >
                    {["evidence", "attempts", "timing"].map((item) => (
                      <button
                        type="button"
                        role="tab"
                        aria-selected={tab === item}
                        className={tab === item ? "active" : ""}
                        onClick={() => setTab(item)}
                        key={item}
                      >
                        {item}
                        {item === "attempts" && (
                          <small>{attempts.length}</small>
                        )}
                      </button>
                    ))}
                  </div>
                  <div className="lab-inspector-body">
                    {tab === "evidence" && (
                      <>
                        {attempts.length > 1 && (
                          <label className="lab-attempt-select">
                            Recognition attempt
                            <select
                              value={attempt?.sample_id || ""}
                              onChange={(event) =>
                                setAttemptId(event.target.value)
                              }
                            >
                              {attempts.map((item, i) => (
                                <option
                                  key={item.sample_id}
                                  value={item.sample_id}
                                >
                                  Attempt {i + 1} ·{" "}
                                  {readable(item.recognition_status)}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                        <Evidence attempt={attempt} />
                      </>
                    )}
                    {tab === "attempts" && (
                      <div className="lab-attempts">
                        {attempts.map((item, i) => {
                          const debug = item.debug || {};
                          const frame = debug.frame_index;
                          return (
                            <div className="lab-attempt" key={item.sample_id}>
                              <span className="lab-attempt-number">
                                {String(i + 1).padStart(2, "0")}
                              </span>
                              <div>
                                <strong>
                                  <PlateNumber row={item} />
                                </strong>
                                <p>
                                  {readable(
                                    debug.recognition_trigger_reason ||
                                      "Recorded attempt",
                                  )}
                                </p>
                                <small>
                                  Frame {frame ?? "—"} ·{" "}
                                  {percent(item.ocr_confidence)}
                                </small>
                                {item.unresolved_reason && (
                                  <p className="lab-attempt-reason">
                                    {readable(item.unresolved_reason)}
                                  </p>
                                )}
                                <button
                                  type="button"
                                  className="lab-text-button"
                                  disabled={
                                    !run.source_fps ||
                                    frame == null ||
                                    !run.output_video_url
                                  }
                                  onClick={() => seek(frame)}
                                >
                                  Jump to frame <Icon name="arrow" size={13} />
                                </button>
                              </div>
                            </div>
                          );
                        })}
                        {!attempts.length && (
                          <p className="lab-footnote">
                            No recognition attempt recorded. This may be a short
                            or unscheduled track.
                          </p>
                        )}
                        <details className="lab-json">
                          <summary>Raw track evidence</summary>
                          <pre>{JSON.stringify(track, null, 2)}</pre>
                        </details>
                      </div>
                    )}
                    {tab === "timing" && (
                      <>
                        <div className="lab-section-label">
                          SELECTED ATTEMPT LATENCY
                        </div>
                        {Object.entries(
                          attempt?.debug?.timing || track.attempt_timing || {},
                        )
                          .filter(([key]) =>
                            [
                              "classification_ms",
                              "marker_ms",
                              "ocr_ms",
                              "parser_ms",
                              "queue_wait_ms",
                            ].includes(key),
                          )
                          .map(([key, value]) => (
                            <div className="lab-timing" key={key}>
                              <div>
                                <span>{readable(key.replace("_ms", ""))}</span>
                                <strong>{number(value, " ms")}</strong>
                              </div>
                              <div className="lab-timing-bar">
                                <span
                                  style={{
                                    width: `${Math.max(1, Math.min(100, (value / Math.max(1, attempt?.debug?.timing?.total_ms || track.attempt_timing?.total_ms || 1)) * 100))}%`,
                                  }}
                                />
                              </div>
                            </div>
                          ))}
                        <dl className="lab-dl">
                          <div>
                            <dt>Recognition attempts</dt>
                            <dd>{attempts.length}</dd>
                          </div>
                          <div>
                            <dt>Selected frame</dt>
                            <dd>
                              {track.selected_crop_frame ??
                                attempt?.debug?.frame_index ??
                                "—"}
                            </dd>
                          </div>
                          <div>
                            <dt>Track duration</dt>
                            <dd>{number(track.track_duration_seconds, "s")}</dd>
                          </div>
                          <div>
                            <dt>Best crop score</dt>
                            <dd>{number(track.final_best_quality)}</dd>
                          </div>
                        </dl>
                      </>
                    )}
                  </div>
                </>
              ) : (
                <div className="lab-empty">
                  Select a track to explore its evidence.
                </div>
              )}
            </div>
          </div>
          {!run.has_metrics && (
            <p className="lab-footnote">
              This older run has saved evidence but no persisted performance
              summary. New analyses include full metrics.
            </p>
          )}
        </>
      )}
      <div className="lab-footer">
        <span>
          <span className="lab-live-dot" /> Local inference · DINOv3 / ByteTrack
          / PaddleOCR
        </span>
        <span>Every prediction deserves a closer look.</span>
      </div>
    </section>
  );
}
