'use client'

import { useCallback, useEffect, useState } from 'react'
import { OpsError } from '@/lib/ops-api'

export type Loaded<T> = { data: T | null; error: OpsError | null; loading: boolean; reload: () => void }
type Result<T> = { key: string; data: T | null; error: OpsError | null }

/**
 * API 하나를 불러 상태로 둔다. deps 가 바뀌면 다시 부르고 앞선 요청은 취소한다. 401 이면 onUnauthorized.
 * 결과에 요청 키를 같이 저장해 "지금 키의 결과가 아직 없음" = 불러오는 중으로 본다(이펙트 안에서 바로 상태를 바꾸지 않음).
 * 새로 불러오는 동안에는 앞 결과를 그대로 보여 준다.
 */
export function useOpsData<T>(
  load: ((signal: AbortSignal) => Promise<T>) | null,
  deps: unknown[],
  onUnauthorized?: () => void,
): Loaded<T> {
  const [tick, setTick] = useState(0)
  const [result, setResult] = useState<Result<T> | null>(null)
  const reload = useCallback(() => setTick(t => t + 1), [])
  const key = JSON.stringify([...deps, tick, Boolean(load)])

  useEffect(() => {
    if (!load) return
    const ctl = new AbortController()
    load(ctl.signal)
      .then(data => { if (!ctl.signal.aborted) setResult({ key, data, error: null }) })
      .catch(e => {
        if (ctl.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        const error = e instanceof OpsError ? e : new OpsError(0, String(e))
        if (error.status === 401) onUnauthorized?.()
        setResult(prev => ({ key, data: prev?.data ?? null, error }))
      })
    return () => ctl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key 가 deps·tick 을 모두 담는다
  }, [key])

  if (!load) return { data: null, error: null, loading: false, reload }
  const fresh = result?.key === key
  return { data: result?.data ?? null, error: fresh ? result.error : null, loading: !fresh, reload }
}
