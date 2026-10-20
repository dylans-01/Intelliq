/**
 * Thin fetch wrapper: JSON in/out, session cookie, CSRF and workspace headers,
 * typed errors. The server enforces all permissions; nothing here is a
 * security boundary.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;
let workspaceId: string | null = null;
let onUnauthorized: (() => void) | null = null;

export const session = {
  setCsrf(token: string | null) {
    csrfToken = token;
  },
  setWorkspace(id: string | null) {
    workspaceId = id;
  },
  onUnauthorized(handler: () => void) {
    onUnauthorized = handler;
  },
};

function messageFrom(body: unknown, status: number): { message: string; code?: string; detail?: unknown } {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    const message = typeof b.message === "string" ? b.message : undefined;
    if (message) return { message, code: typeof b.code === "string" ? b.code : undefined, detail: b.detail ?? b.errors ?? b.problems };
  }
  if (status === 429) return { message: "Too many requests. Please wait a moment." };
  if (status >= 500) return { message: "The server could not complete the request." };
  return { message: `Request failed (${status})` };
}

export async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; form?: FormData; signal?: AbortSignal; headers?: Record<string, string>; raw?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-Intelliq-CSRF"] = csrfToken;
  if (workspaceId) headers["X-Intelliq-Workspace"] = workspaceId;

  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.form ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    signal: options.signal,
  });
  if (options.raw && res.ok) return res as unknown as T;
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/login")) onUnauthorized?.();
    const { message, code, detail } = messageFrom(body, res.status);
    throw new ApiError(res.status, message, code, detail);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, { signal }),
  post: <T>(path: string, body?: unknown, extra?: { signal?: AbortSignal; headers?: Record<string, string> }) =>
    request<T>("POST", path, { body: body ?? {}, ...extra }),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, { body }),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, { body }),
  delete: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, { form }),
};

/** Download a POST response as a file (CSV export). */
export async function downloadPost(path: string, body: unknown, filename: string) {
  const res = await request<Response>("POST", path, { body, raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
