const API = "/api/v1";

let refreshPromise: Promise<void> | null = null;
let sessionExpiredHandler: (() => void) | null = null;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function setSessionExpiredHandler(handler: (() => void) | null): () => void {
  sessionExpiredHandler = handler;
  return () => {
    if (sessionExpiredHandler === handler) sessionExpiredHandler = null;
  };
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  const data = await response.json().catch(() => ({ detail: response.statusText }));
  return new ApiError(response.status, data.detail ?? "Falha na operação");
}

async function refreshSession(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${API}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    }).then(async (response) => {
      if (!response.ok) throw await errorFromResponse(response);
    }).catch((cause) => {
      if (cause instanceof ApiError && [401, 403].includes(cause.status)) sessionExpiredHandler?.();
      throw cause;
    }).finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

async function fetchWithSession(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const request = () => fetch(`${API}${path}`, { ...init, headers, credentials: "include" });
  let response = await request();
  const canRefresh = !["/auth/login", "/auth/logout", "/auth/refresh"].includes(path);
  if (response.status === 401 && canRefresh) {
    await refreshSession();
    response = await request();
    if (response.status === 401) sessionExpiredHandler?.();
  }
  return response;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetchWithSession(path, init);
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function downloadQueryExport(): Promise<void> {
  const response = await fetchWithSession("/queries/contacts/export", { method: "POST" });
  if (!response.ok) throw await errorFromResponse(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? "contatos_query.xlsx";
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
