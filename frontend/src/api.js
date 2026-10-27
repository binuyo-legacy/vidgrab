// Base URL comes from VITE_API_URL (production) or is empty (dev, via the Vite proxy).
const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

export const QUALITIES = [
  { value: "best", label: "Best available" },
  { value: "1080", label: "1080p or lower" },
  { value: "720", label: "720p or lower" },
  { value: "480", label: "480p or lower" },
  { value: "360", label: "360p or lower" },
];

// How long we wait for the one-request stream before giving up and using the job flow.
// The server converts the file before it sends anything, so this is effectively total time.
// Render's free tier can cold-start slowly, so these are generous.
export const STREAM_TIMEOUT_MS = {
  audio: 150_000,
  video: 240_000,
};

export const RATE_LIMIT_MESSAGE =
  "you've hit the download limit for now. wait a minute, then try again.";

const FALLBACK = {
  400: "that link does not look right. paste a full http or https link.",
  404: "this download could not be found. start it again.",
  409: "the file is not ready yet.",
  410: "this file was already downloaded or has expired. start it again.",
  422: "that link does not look right. paste a full http or https link.",
  429: "too many downloads are running right now. try again in a minute.",
  503: "the download server is missing a required tool right now. try again later.",
};

// Server detail messages are already written for people, so show them when we have one.
export function friendlyError(status, detail) {
  if (typeof detail === "string" && detail.trim()) return detail;
  return FALLBACK[status] || "something went wrong on the server. please try again.";
}

function detailFrom(body) {
  if (typeof body?.detail === "string") return body.detail;
  if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
    return body.detail[0].msg.replace(/^Value error, /, "");
  }
  return null;
}

async function readError(res) {
  let detail = null;
  try {
    detail = detailFrom(await res.json());
  } catch {
    /* body was not JSON */
  }
  return friendlyError(res.status, detail);
}

export async function startDownload({ url, mode, quality, maxDuration }) {
  const body = { url, mode, quality };
  if (maxDuration) body.max_duration = maxDuration;
  let res;
  try {
    res = await fetch(`${BASE}/api/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("couldn't reach the download server. check your connection and try again.");
  }
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function getJob(id) {
  let res;
  try {
    res = await fetch(`${BASE}/api/jobs/${encodeURIComponent(id)}`);
  } catch {
    throw new Error("lost connection to the server while checking progress.");
  }
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export function fileUrl(id) {
  return `${BASE}/api/jobs/${encodeURIComponent(id)}/file`;
}

// --- one-request stream (GET /api/stream) -------------------------------------------------

// Builds the stream URL. mp3 quality is a bitrate the server defaults for us, so we only send it for mp4.
export function buildStreamUrl(base, { url, mode, quality }) {
  const format = mode === "audio" ? "mp3" : "mp4";
  const params = new URLSearchParams({ url, format });
  if (format === "mp4" && quality) params.set("quality", quality);
  return `${String(base || "").replace(/\/+$/, "")}/api/stream?${params.toString()}`;
}

// Reads the filename out of a Content-Disposition header (supports filename* and filename).
export function parseFilename(header) {
  if (!header) return null;
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) {
    try {
      const name = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
      if (name) return name;
    } catch {
      /* fall through to the plain filename */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

// A failed stream attempt. `reason` tells the caller whether a fallback makes sense.
//   rate_limit: server said wait (429). no fallback, the message is the answer.
//   cancelled:  the user pressed cancel. no fallback.
//   timeout | network | http: the caller may fall back to the job flow.
export class StreamFailure extends Error {
  constructor(message, { reason, status = null }) {
    super(message);
    this.name = "StreamFailure";
    this.reason = reason;
    this.status = status;
  }
}

// Decides whether a stream failure should silently switch to the job flow.
export function shouldFallback(err) {
  if (!(err instanceof StreamFailure)) return true;
  if (err.reason === "rate_limit" || err.reason === "cancelled") return false;
  // these mean the request itself is wrong, the job flow would refuse it too
  if (err.reason === "http" && [400, 413].includes(err.status)) return false;
  return true;
}

// Fetches the finished file in one request. Resolves with { blob, filename } or throws StreamFailure.
export async function streamDownload({ url, mode, quality, signal, timeoutMs }) {
  const limit = timeoutMs ?? STREAM_TIMEOUT_MS[mode === "audio" ? "audio" : "video"];
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, limit);
  const forwardAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", forwardAbort);
  }

  const failure = () => {
    if (signal?.aborted) return new StreamFailure("cancelled.", { reason: "cancelled" });
    if (timedOut) {
      return new StreamFailure("the server took too long. trying a backup way...", { reason: "timeout" });
    }
    return new StreamFailure("couldn't reach the download server. trying a backup way...", { reason: "network" });
  };

  try {
    let res;
    try {
      res = await fetch(buildStreamUrl(BASE, { url, mode, quality }), { signal: controller.signal });
    } catch {
      throw failure();
    }

    if (res.status === 429) {
      throw new StreamFailure(RATE_LIMIT_MESSAGE, { reason: "rate_limit", status: 429 });
    }
    if (!res.ok) {
      const message = await readError(res);
      throw new StreamFailure(message, { reason: "http", status: res.status });
    }

    let blob;
    try {
      blob = await res.blob();
    } catch {
      throw failure();
    }
    const filename = parseFilename(res.headers.get("Content-Disposition")) || (mode === "audio" ? "audio.mp3" : "video.mp4");
    return { blob, filename };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

// Saves a blob to the device by way of a temporary object URL. Works on phones too.
export function saveBlob(blob, filename) {
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}
