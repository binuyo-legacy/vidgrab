// Base URL comes from VITE_API_URL (production) or is empty (dev, via the Vite proxy).
const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

export const QUALITIES = [
  { value: "best", label: "Best available" },
  { value: "1080", label: "1080p or lower" },
  { value: "720", label: "720p or lower" },
  { value: "480", label: "480p or lower" },
  { value: "360", label: "360p or lower" },
];

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

async function readError(res) {
  let detail = null;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      detail = body.detail[0].msg.replace(/^Value error, /, "");
    }
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
