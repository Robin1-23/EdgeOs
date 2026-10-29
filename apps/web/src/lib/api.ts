// Server-side reads from the FastAPI service. Pages are rebuilt at most every REVALIDATE seconds (reports change nightly).

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
export const REVALIDATE = 300;

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; detail: string };

export async function apiGet<T>(path: string): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { next: { revalidate: REVALIDATE } });
  } catch {
    return { ok: false, status: 503, detail: "the data service is not reachable" };
  }
  if (res.ok) return { ok: true, data: (await res.json()) as T };
  const body = await res.json().catch(() => ({}));
  return { ok: false, status: res.status, detail: typeof body.detail === "string" ? body.detail : res.statusText };
}
