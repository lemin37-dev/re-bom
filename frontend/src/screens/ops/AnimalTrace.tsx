'use client'

import { type FormEvent, useState } from 'react'
import { opsApi } from '@/lib/ops-api'
import type { OpsEnv, OpsFailure } from '@/types/ops'
import { Evidence } from './FailureDrill'
import { fmtTime, stageLabel, stageLayer } from './format'
import { useOpsData } from './useOps'

const SERVICE_FIELDS: [string, string][] = [
  ['status_group', '상태 묶음'],
  ['process_state_raw', '상태 원문'],
  ['closed_transition_at', '종료 전환'],
  ['visible_until', '상세 노출 끝'],
  ['photos', '사진 칸'],
  ['tags', '태그'],
  ['silver_publish_seq', '반영한 발행 순번'],
  ['synced_at', '마지막 동기화'],
]
const LAYERS = [
  { key: 'bronze', title: 'Bronze' },
  { key: 'silver', title: 'Silver' },
  { key: 'gold', title: 'Gold' },
  { key: 'rds', title: 'RDS' },
] as const

/**
 * 공고 하나 추적: 단계별 실패 기록 + RDS 서비스 표 상태. 보호자 정보는 API 가 애초에 주지 않는다.
 * 실패 목록에서 '공고 추적'을 누르면 부모가 key 를 그 번호로 바꿔 새로 시작한다.
 */
export function AnimalTrace({ env, initial, onUnauthorized, reasonNotes }: {
  env: OpsEnv
  initial: string | null
  onUnauthorized: () => void
  reasonNotes: Record<string, { label: string; action: string | null }>
}) {
  const [input, setInput] = useState(initial ?? '')
  const [no, setNo] = useState<string | null>(initial)
  const [open, setOpen] = useState<OpsFailure | null>(null)

  const res = useOpsData(no ? s => opsApi.animal(env, no, s) : null, [no, env], onUnauthorized)
  const valid = /^\d{6,20}$/.test(input.trim())
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (valid) { setNo(input.trim()); setOpen(null) }
  }

  const byLayer = (k: string) => res.data?.failures.filter(f => stageLayer(f.stage) === k) ?? []
  const service = res.data?.service

  return (
    <section className="ops-card">
      <h2 className="ops-h2">공고 하나 추적</h2>
      <form className="ops-row ops-trace-form" onSubmit={submit}>
        <label className="ops-field-inline">
          <span>공고번호</span>
          <input inputMode="numeric" value={input} onChange={e => setInput(e.target.value)} placeholder="448567202600142" />
        </label>
        <button type="submit" className="ops-btn ops-btn-primary" disabled={!valid}>추적</button>
      </form>
      {!no && <p className="ops-muted">실패 건별 목록에서 &lsquo;공고 추적&rsquo;을 누르거나 번호를 넣으세요. 실패 기록과 서비스(RDS) 상태를 층별로 보여 줘요.</p>}
      {no && res.loading && <p className="ops-muted">불러오는 중…</p>}
      {no && res.error && <p className="ops-error">{res.error.message}</p>}
      {res.data && (
        <div className="ops-trace">
          {LAYERS.map(l => {
            const fails = byLayer(l.key)
            return (
              <div key={l.key} className={`ops-trace-col ops-layer-${l.key}`}>
                <p className="ops-flow-layer">{l.title}</p>
                {l.key === 'rds' && (
                  service
                    ? <dl className="ops-kv">{SERVICE_FIELDS.map(([k, label]) => <div key={k}><dt>{label}</dt><dd>{fmtValue(k, service[k])}</dd></div>)}</dl>
                    : <p className="ops-small ops-warn">service_notice 에 없음 (아직 동기화 전이거나 검색 대상이 아님)</p>
                )}
                {fails.length === 0
                  ? <p className="ops-small ops-muted">실패 기록 없음</p>
                  : (
                    <ul className="ops-pick">
                      {fails.map(f => (
                        <li key={f.id ?? `${f.stage}:${f.observedAt}`}>
                          <button type="button" className={open?.id === f.id ? 'is-on' : ''} aria-pressed={open?.id === f.id} onClick={() => setOpen(open?.id === f.id ? null : f)}>
                            <span className="ops-pick-top"><code>{f.reasonCode}</code>{f.blocking && <span className="ops-tag ops-tag-bad">차단</span>}</span>
                            <span className="ops-small">{reasonNotes[f.reasonCode]?.label ?? stageLabel(f.stage)}</span>
                            <span className="ops-small ops-muted">{stageLabel(f.stage)} · {fmtTime(f.observedAt)}{f.subjectKey.includes('#') ? ` · 칸 ${f.subjectKey.split('#')[1]}` : ''}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
              </div>
            )
          })}
        </div>
      )}
      {open && <div className="ops-trace-evidence"><Evidence item={open} /></div>}
    </section>
  )
}

function fmtValue(key: string, v: string | number | null | undefined) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'string' && /At$|_at$|until$/.test(key) && /^\d{4}-\d{2}-\d{2}T/.test(v)) return fmtTime(v)
  return String(v)
}
