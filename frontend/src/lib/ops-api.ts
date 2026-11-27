import { API_BASE_URL } from '@/lib/api'
import type { OpsAnimal, OpsEnv, OpsErd, OpsFailures, OpsOverview, OpsRuns, OpsSchema } from '@/types/ops'

/**
 * 운영 모니터링 API (/ops/*). 로그인하면 API 가 HttpOnly 서명 쿠키를 주고, 이후 요청은 그 쿠키로 통과한다.
 * 쿠키를 보내야 하므로 모든 요청에 credentials: 'include' (로컬 개발 3000 → 8000 도 같은 사이트라 SameSite=Strict 로 감).
 */
export class OpsError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      credentials: 'include',
      headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new OpsError(0, 'API 서버에 연결할 수 없어요.')
  }
  if (!res.ok) {
    const detail = await res.json().then(b => (typeof b?.detail === 'string' ? b.detail : null)).catch(() => null)
    throw new OpsError(res.status, detail ?? `API 오류 (${res.status})`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function query(params: Record<string, string | number | boolean | null | undefined>) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const opsApi = {
  login: (password: string) => call<void>('/ops/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => call<void>('/ops/logout', { method: 'POST' }),
  me: (signal?: AbortSignal) => call<{ ok: boolean }>('/ops/me', { signal }),
  overview: (env: OpsEnv, signal?: AbortSignal) => call<OpsOverview>(`/ops/overview${query({ env })}`, { signal }),
  runs: (env: OpsEnv, days = 14, signal?: AbortSignal) => call<OpsRuns>(`/ops/runs${query({ env, days })}`, { signal }),
  failures: (
    p: { env: OpsEnv; runId: string; stage?: string; reasonCode?: string; blocking?: boolean; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) =>
    call<OpsFailures>(
      `/ops/failures${query({ env: p.env, run_id: p.runId, stage: p.stage, reason_code: p.reasonCode, blocking: p.blocking || undefined, limit: p.limit, offset: p.offset })}`,
      { signal },
    ),
  animal: (env: OpsEnv, desertionNo: string, signal?: AbortSignal) =>
    call<OpsAnimal>(`/ops/animals/${encodeURIComponent(desertionNo)}${query({ env })}`, { signal }),
  erd: (signal?: AbortSignal) => call<OpsErd>('/ops/erd', { signal }),
  schema: (env: OpsEnv, signal?: AbortSignal) => call<OpsSchema>(`/ops/schema${query({ env })}`, { signal }),
}

let erdPromise: Promise<OpsErd> | null = null
/** ERD(문서 스키마)는 이미지에 든 값이라 바뀌지 않는다. 한 번만 받고 두 탭이 같이 쓴다. 실패하면 다음에 다시 받는다. */
export function loadErd(): Promise<OpsErd> {
  if (!erdPromise) erdPromise = opsApi.erd().catch(e => { erdPromise = null; throw e })
  return erdPromise
}
