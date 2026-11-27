'use client'

import { useCallback, useEffect, useState } from 'react'
import { OpsError, opsApi } from '@/lib/ops-api'
import type { OpsEnv } from '@/types/ops'
import { OpsLogin } from './OpsLogin'
import { OverviewTab } from './OverviewTab'
import { SchemaTab } from './SchemaTab'
import { useOpsData } from './useOps'

type Tab = 'overview' | 'schema'
type Auth = 'checking' | 'in' | 'out' | 'unavailable'

const ENV_KEY = 'rebom-ops-env'
const TAB_KEY = 'rebom-ops-tab'

function readPref<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key)
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback
  } catch {
    return fallback
  }
}

function writePref(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 저장 못 해도 화면은 그대로 */
  }
}

/**
 * 운영 모니터링 화면. 비밀번호로 들어오면 두 탭을 보인다.
 * 탭1 수집·처리 현황 ("오늘" = 마지막 수집일), 탭2 테이블 흐름(DBML)·스키마 지문 비교.
 */
export function OpsScreen() {
  const [auth, setAuth] = useState<Auth>('checking')
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [env, setEnv] = useState<OpsEnv>('test')
  const [tab, setTab] = useState<Tab>('overview')

  useEffect(() => {
    // 서버 렌더에는 localStorage 가 없어서, 마지막에 고른 환경·탭은 처음 한 번만 브라우저에서 반영
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEnv(readPref<OpsEnv>(ENV_KEY, ['test', 'prod'], 'test'))
    setTab(readPref<Tab>(TAB_KEY, ['overview', 'schema'], 'overview'))
    const ctl = new AbortController()
    opsApi.me(ctl.signal)
      .then(() => setAuth('in'))
      .catch(e => {
        if (ctl.signal.aborted) return
        if (e instanceof OpsError && e.status === 401) setAuth('out')
        else {
          setUnavailable(e instanceof OpsError ? e.message : String(e))
          setAuth('unavailable')
        }
      })
    return () => ctl.abort()
  }, [])

  const signedOut = useCallback(() => setAuth('out'), [])
  const overview = useOpsData(auth === 'in' ? s => opsApi.overview(env, s) : null, [auth, env], signedOut)

  const logout = async () => {
    await opsApi.logout().catch(() => undefined)
    setAuth('out')
  }

  const chooseEnv = (e: OpsEnv) => { setEnv(e); writePref(ENV_KEY, e) }
  const chooseTab = (t: Tab) => { setTab(t); writePref(TAB_KEY, t) }

  return (
    <div className="ops">
      <header className="ops-top">
        <div className="ops-top-title">
          <p className="ops-eyebrow">RE:봄 · /ops</p>
          <h1>운영 모니터링</h1>
        </div>
        {auth === 'in' && (
          <div className="ops-top-meta">
            <label className="ops-field-inline">
              <span>환경</span>
              <select value={env} onChange={e => chooseEnv(e.target.value as OpsEnv)}>
                <option value="test">test</option>
                <option value="prod">prod</option>
              </select>
            </label>
            <div className="ops-top-date">
              <span>최종 수집일</span>
              <strong>{overview.data?.date ?? '—'}</strong>
            </div>
            <button type="button" className="ops-btn ops-btn-ghost" onClick={logout}>나가기</button>
          </div>
        )}
      </header>

      {auth === 'checking' && <p className="ops-muted ops-pad">확인하는 중…</p>}
      {auth === 'unavailable' && (
        <div className="ops-card ops-notice ops-notice-bad">
          <strong>운영 화면을 열 수 없어요.</strong>
          <p>{unavailable}</p>
          <p className="ops-muted">API 가 db 모드이고 Secret rebom-ops(비밀번호 해시·서명 키)가 있어야 열립니다 (infra/k8s/web/README.md).</p>
        </div>
      )}
      {auth === 'out' && <OpsLogin onDone={() => setAuth('in')} />}

      {auth === 'in' && (
        <>
          <nav className="ops-tabs" aria-label="운영 화면 탭">
            <button type="button" aria-pressed={tab === 'overview'} className={tab === 'overview' ? 'is-on' : ''} onClick={() => chooseTab('overview')}>
              수집·처리 현황
            </button>
            <button type="button" aria-pressed={tab === 'schema'} className={tab === 'schema' ? 'is-on' : ''} onClick={() => chooseTab('schema')}>
              테이블 흐름·스키마
            </button>
          </nav>
          {tab === 'overview'
            ? <OverviewTab env={env} overview={overview} onUnauthorized={signedOut} />
            : <SchemaTab env={env} onUnauthorized={signedOut} />}
        </>
      )}
    </div>
  )
}
