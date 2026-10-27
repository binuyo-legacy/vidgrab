import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  RATE_LIMIT_MESSAGE,
  StreamFailure,
  buildStreamUrl,
  parseFilename,
  shouldFallback,
  streamDownload,
} from "../api.js";

const URL_ = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

describe("buildStreamUrl", () => {
  it("builds an mp4 url with the quality as max height", () => {
    const u = new URL(buildStreamUrl("https://api.example.com/", { url: URL_, mode: "video", quality: "720" }));
    expect(u.origin + u.pathname).toBe("https://api.example.com/api/stream");
    expect(u.searchParams.get("url")).toBe(URL_);
    expect(u.searchParams.get("format")).toBe("mp4");
    expect(u.searchParams.get("quality")).toBe("720");
  });

  it("builds an mp3 url without a quality param", () => {
    const u = new URL(buildStreamUrl("https://api.example.com", { url: URL_, mode: "audio", quality: "best" }));
    expect(u.pathname).toBe("/api/stream");
    expect(u.searchParams.get("format")).toBe("mp3");
    expect(u.searchParams.has("quality")).toBe(false);
  });

  it("encodes the link so odd characters survive", () => {
    const tricky = "https://www.youtube.com/watch?v=abc&t=1s#x y";
    const u = new URL(buildStreamUrl("https://api.example.com", { url: tricky, mode: "video", quality: "best" }));
    expect(u.searchParams.get("url")).toBe(tricky);
  });
});

describe("parseFilename", () => {
  it("reads a plain filename", () => {
    expect(parseFilename('attachment; filename="my video.mp4"')).toBe("my video.mp4");
  });

  it("prefers the utf-8 filename*", () => {
    expect(parseFilename("attachment; filename=\"x.mp4\"; filename*=UTF-8''caf%C3%A9.mp4")).toBe("café.mp4");
  });

  it("returns null when there is no header", () => {
    expect(parseFilename(null)).toBeNull();
  });
});

describe("shouldFallback", () => {
  it("falls back on timeout, network and server errors", () => {
    expect(shouldFallback(new StreamFailure("x", { reason: "timeout" }))).toBe(true);
    expect(shouldFallback(new StreamFailure("x", { reason: "network" }))).toBe(true);
    expect(shouldFallback(new StreamFailure("x", { reason: "http", status: 500 }))).toBe(true);
    expect(shouldFallback(new StreamFailure("x", { reason: "http", status: 503 }))).toBe(true);
  });

  it("does not fall back on rate limit, cancel or a bad request", () => {
    expect(shouldFallback(new StreamFailure("x", { reason: "rate_limit", status: 429 }))).toBe(false);
    expect(shouldFallback(new StreamFailure("x", { reason: "cancelled" }))).toBe(false);
    expect(shouldFallback(new StreamFailure("x", { reason: "http", status: 400 }))).toBe(false);
  });
});

describe("streamDownload", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("returns the blob and filename from one request", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(new Blob(["abc"], { type: "video/mp4" }), {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="clip.mp4"' },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { blob, filename } = await streamDownload({ url: URL_, mode: "video", quality: "720" });
    expect(filename).toBe("clip.mp4");
    expect(blob.size).toBe(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/stream?");
  });

  it("maps 429 to a friendly 'wait a minute' message with no fallback", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 429 })));
    const err = await streamDownload({ url: URL_, mode: "video", quality: "720" }).catch((e) => e);
    expect(err).toBeInstanceOf(StreamFailure);
    expect(err.message).toBe(RATE_LIMIT_MESSAGE);
    expect(err.message).toMatch(/wait a minute/);
    expect(shouldFallback(err)).toBe(false);
  });

  it("uses the server's plain-language detail on other errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "that is not a youtube link" }), { status: 422 })),
    );
    const err = await streamDownload({ url: URL_, mode: "audio" }).catch((e) => e);
    expect(err.reason).toBe("http");
    expect(err.message).toBe("that is not a youtube link");
  });

  it("reports a network failure so the caller can fall back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await streamDownload({ url: URL_, mode: "video", quality: "best" }).catch((e) => e);
    expect(err.reason).toBe("network");
    expect(shouldFallback(err)).toBe(true);
  });

  it("times out and reports timeout so the caller can fall back", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url, { signal }) => new Promise((_res, rej) => {
        signal.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
      })),
    );
    const err = await streamDownload({ url: URL_, mode: "video", quality: "best", timeoutMs: 20 }).catch((e) => e);
    expect(err.reason).toBe("timeout");
    expect(shouldFallback(err)).toBe(true);
  });

  it("reports cancel without a fallback when the user stops it", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url, { signal }) => new Promise((_res, rej) => {
        signal.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
      })),
    );
    const pending = streamDownload({ url: URL_, mode: "audio", signal: controller.signal, timeoutMs: 5000 });
    controller.abort();
    const err = await pending.catch((e) => e);
    expect(err.reason).toBe("cancelled");
    expect(shouldFallback(err)).toBe(false);
  });
});
