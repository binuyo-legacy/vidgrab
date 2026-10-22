import { useEffect, useRef, useState } from "react";
import { QUALITIES, fileUrl, getJob, startDownload } from "./api.js";
import Modal from "./Modal.jsx";
import "./modal.css";

const POLL_MS = 1000;
const REPO_URL = "https://github.com/binuyo-legacy/vidgrab";

function validateUrl(value) {
  const v = value.trim();
  if (!v) return "paste a link to get started.";
  try {
    const u = new URL(v);
    if (u.protocol !== "http:" && u.protocol !== "https:") {
      return "the link must start with http:// or https://";
    }
    if (!u.hostname.includes(".") && u.hostname !== "localhost") {
      return "that doesn't look like a full link.";
    }
    return "";
  } catch {
    return "that doesn't look like a link. check it and try again.";
  }
}

export default function App() {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState("video");
  const [quality, setQuality] = useState("best");
  const [phase, setPhase] = useState("idle"); // idle | starting | running | ready | error
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [errorOpen, setErrorOpen] = useState(false);
  const [touched, setTouched] = useState(false);
  const pollRef = useRef(null);
  const inputRef = useRef(null);

  const urlError = touched ? validateUrl(url) : "";
  const busy = phase === "starting" || phase === "running";

  useEffect(() => () => clearInterval(pollRef.current), []);

  function stopPolling() {
    clearInterval(pollRef.current);
    pollRef.current = null;
  }

  function reset() {
    stopPolling();
    setPhase("idle");
    setJob(null);
    setError("");
    setErrorOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setTouched(true);
    const problem = validateUrl(url);
    if (problem) {
      inputRef.current?.focus();
      return;
    }
    stopPolling();
    setError("");
    setErrorOpen(false);
    setJob(null);
    setPhase("starting");
    try {
      const started = await startDownload({
        url: url.trim(),
        mode,
        quality: mode === "video" ? quality : "best",
      });
      setJob({ id: started.id, status: "queued", progress: 0, message: "waiting for a free slot" });
      setPhase("running");
      pollRef.current = setInterval(() => poll(started.id), POLL_MS);
      poll(started.id);
    } catch (err) {
      setError(err.message);
      setErrorOpen(true);
      setPhase("error");
    }
  }

  function failWith(message) {
    stopPolling();
    setError(message);
    setErrorOpen(true);
    setPhase("error");
  }

  async function poll(id) {
    try {
      const data = await getJob(id);
      setJob(data);
      if (data.status === "done") {
        stopPolling();
        setPhase("ready");
      } else if (data.status === "error") {
        failWith(data.error || "the download failed. try again.");
      }
    } catch (err) {
      failWith(err.message);
    }
  }

  const percent = Math.round(job?.progress ?? 0);
  const requestedHeight = Number(quality);
  const gotHeight = job?.resolved_height;
  // Shown on the done state: the real height yt-dlp picked, and a note if it had to go lower.
  const qualityLabel = job?.quality_label || null;
  const downgraded = mode === "video" && requestedHeight && gotHeight && gotHeight < requestedHeight;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">V</span>
          <span className="name">VibeN</span>
        </div>
        <a className="repo-pill" href={REPO_URL} target="_blank" rel="noreferrer">github</a>
      </header>

      <section className="hero">
        <span className="eyebrow">video + mp3 downloader</span>
        <h1 className="headline">Download any video. <span className="grad">One paste.</span></h1>
        <p className="lede">
          Drop a link from YouTube, TikTok, Instagram, X, Vimeo and more. Pick video or mp3 and grab it.
        </p>
        <ul className="features" aria-label="features">
          <li><span className="dot" aria-hidden="true" />video or mp3</li>
          <li><span className="dot" aria-hidden="true" />works on most sites</li>
          <li><span className="dot" aria-hidden="true" />no signup</li>
        </ul>
      </section>

      <main className="card" aria-live="polite" id="grab">
        <h2>grab a video or audio</h2>
        <p className="sub">paste a link below to start.</p>

        <form onSubmit={onSubmit} noValidate>
          <label className="field">
            <span className="label">link</span>
            <input
              ref={inputRef}
              className={urlError ? "input invalid" : "input"}
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://"
              value={url}
              disabled={busy}
              aria-invalid={urlError ? "true" : "false"}
              aria-describedby={urlError ? "url-error" : undefined}
              onChange={(e) => {
                setUrl(e.target.value);
                if (touched) setTouched(true);
              }}
              onBlur={() => setTouched(true)}
              autoFocus
            />
          </label>
          {urlError && (
            <p id="url-error" className="hint error">{urlError}</p>
          )}

          <div className="row">
            <div className="segmented" role="radiogroup" aria-label="format">
              {[
                { v: "video", l: "video" },
                { v: "audio", l: "mp3" },
              ].map((o) => (
                <button
                  key={o.v}
                  type="button"
                  role="radio"
                  aria-checked={mode === o.v}
                  className={mode === o.v ? "seg active" : "seg"}
                  disabled={busy}
                  onClick={() => setMode(o.v)}
                >
                  {o.l}
                </button>
              ))}
            </div>

            <label className={`select-wrap ${mode === "audio" ? "dim" : ""}`}>
              <span className="sr-only">quality</span>
              <select
                className="select"
                value={quality}
                disabled={busy || mode === "audio"}
                onChange={(e) => setQuality(e.target.value)}
                title={mode === "audio" ? "quality applies to video only" : "video quality"}
              >
                {QUALITIES.map((q) => (
                  <option key={q.value} value={q.value}>{q.label}</option>
                ))}
              </select>
            </label>
          </div>

          <button className="primary" type="submit" disabled={busy}>
            {phase === "starting" ? "starting..." : busy ? "grabbing..." : mode === "audio" ? "get mp3" : "get video"}
          </button>
        </form>

        {(phase === "running" || phase === "starting") && (
          <section className="status" role="status">
            <div className="status-top">
              <span className="status-msg">{job?.message || "starting..."}</span>
              <span className="status-pct">{percent}%</span>
            </div>
            <div className="bar" aria-hidden="true">
              <div className="fill" style={{ width: `${percent}%` }} />
            </div>
            {mode === "audio" && <p className="hint">converting to mp3 takes a little longer.</p>}
          </section>
        )}

        {phase === "ready" && (
          <section className="status ready" role="status">
            <p className="status-msg">
              ready: {job?.filename || "your file"}
              {qualityLabel && <span className="quality-chip">{qualityLabel}</span>}
            </p>
            {downgraded && (
              <p className="hint">{requestedHeight}p wasn't available for this link, so you got the closest lower quality ({gotHeight}p).</p>
            )}
            <a className="primary download" href={fileUrl(job.id)} onClick={() => setTimeout(() => setPhase("done"), 400)}>
              download {mode === "audio" ? "mp3" : "video"}
            </a>
            <p className="hint">the file is removed from the server after you download it.</p>
          </section>
        )}

        {phase === "done" && (
          <section className="status ready" role="status">
            <p className="status-msg">saved to your device.</p>
            <button type="button" className="ghost" onClick={reset}>grab another</button>
          </section>
        )}

        {phase === "error" && !errorOpen && (
          <section className="status error-box" role="status">
            <p className="status-msg">{error}</p>
            <button type="button" className="ghost" onClick={reset}>try again</button>
          </section>
        )}
      </main>

      <Modal
        open={errorOpen}
        title="download didn't work"
        onClose={() => setErrorOpen(false)}
        actions={
          <>
            <button type="button" onClick={() => setErrorOpen(false)}>close</button>
            <button type="button" className="primary-action" onClick={() => { setErrorOpen(false); reset(); }}>
              try again
            </button>
          </>
        }
      >
        <div className="modal-icon" aria-hidden="true">!</div>
        <p>{error}</p>
      </Modal>

      <footer className="foot">
        <p className="foot-line">
          built by <span className="foot-strong">binuyo</span> ·{" "}
          <a href={REPO_URL} target="_blank" rel="noreferrer">source on github</a>
        </p>
        <p className="foot-fine">only download what you have the right to download.</p>
      </footer>
    </div>
  );
}
