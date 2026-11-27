'use client'

import { useState } from 'react'
import { opsApi } from '@/lib/ops-api'
import type { OpsEnv } from '@/types/ops'
import { fmtNum, stageLabel, STATUS_LABELS } from './format'
import { useOpsData } from './useOps'

const BASE_STAGES = ['bronze', 'photo', 'silver_notice', 'silver_photo', 'silver_publish']

/** 최근 N일 실행별 단계 상태. 칸 = 실행 × 단계, 색 = 상태, 숫자 = 나온 건수. */
export function RunTrend({ env, onUnauthorized }: { env: OpsEnv; onUnauthorized: () => void }) {
  const [days, setDays] = useState(14)
  const res = useOpsData(s => opsApi.runs(env, days, s), [env, days], onUnauthorized)
  const runs = res.data?.runs ?? []
  const extra = Array.from(new Set(runs.flatMap(r => Object.keys(r.stages)).filter(s => !BASE_STAGES.includes(s)))).sort()
  const stages = [...BASE_STAGES, ...extra]

  return (
    <section className="ops-card">
      <div className="ops-drill-head">
        <h2 className="ops-h2">최근 실행 추이</h2>
        <label className="ops-field-inline">
          <span>기간</span>
          <select value={days} onChange={e => setDays(Number(e.target.value))}>
            <option value={7}>7일</option>
            <option value={14}>14일</option>
            <option value={30}>30일</option>
          </select>
        </label>
      </div>
      {res.loading && !res.data && <p className="ops-muted">불러오는 중…</p>}
      {res.error && <p className="ops-error">{res.error.message}</p>}
      {res.data && runs.length === 0 && <p className="ops-muted">이 기간의 실행 기록이 없어요.</p>}
      {runs.length > 0 && (
        <div className="ops-table-wrap">
          <table className="ops-table ops-trend">
            <thead>
              <tr>
                <th scope="col">실행</th>
                {stages.map(s => <th key={s} scope="col">{stageLabel(s)}</th>)}
              </tr>
            </thead>
            <tbody>
              {[...runs].reverse().map(r => (
                <tr key={r.runId}>
                  <th scope="row"><span className="ops-small">{r.date}</span><br /><code className="ops-small">{r.runId.slice(9, 15)}</code></th>
                  {stages.map(s => {
                    const st = r.stages[s]
                    return (
                      <td key={s} className={st ? `ops-cell ops-cell-${st.status}` : 'ops-cell'}>
                        {st ? <><span className="ops-sr">{STATUS_LABELS[st.status] ?? st.status} </span>{fmtNum(st.outputCount)}{(st.failedCount ?? 0) > 0 && <em> ✕{fmtNum(st.failedCount)}</em>}</> : '—'}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="ops-foot">칸 색: 초록 정상 · 노랑 주의 · 빨강 실패 · 회색 건너뜀. 숫자 = 그 단계가 내보낸 건수, ✕ = 실패 수.</p>
    </section>
  )
}
