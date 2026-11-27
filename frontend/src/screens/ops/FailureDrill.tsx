'use client'

import { useState } from 'react'
import { opsApi } from '@/lib/ops-api'
import type { OpsEnv, OpsFailure, OpsReasonCount } from '@/types/ops'
import { fmtNum, fmtTime, stageLabel } from './format'
import { useOpsData } from './useOps'

export type DrillGroup = 'collect' | 'clean' | 'model' | 'sync'
const PAGE = 50

type Props = {
  env: OpsEnv
  runId: string | null
  group: DrillGroup
  title: string
  source: string
  blockingOnly: boolean
  reasons: OpsReasonCount[]
  stageFocus: string | null
  onClearStage: () => void
  reasonNotes: Record<string, { label: string; action: string | null }>
  onTrace: (desertionNo: string) => void
  onClose: () => void
  onUnauthorized: () => void
}

/**
 * 실패 드릴다운: ① 원인별 → ② 건별 → ③ 증거(원문). 건별은 /ops/failures 를 원인 하나로 불러온다.
 * 부모가 카드·단계를 바꾸면 key 를 바꿔 고른 상태를 새로 시작한다.
 */
export function FailureDrill(p: Props) {
  const [picked, setPicked] = useState<OpsReasonCount | null>(null)
  const [offset, setOffset] = useState(0)
  const [item, setItem] = useState<OpsFailure | null>(null)

  const list = useOpsData(
    picked && p.runId
      ? s => opsApi.failures({ env: p.env, runId: p.runId as string, stage: picked.stage, reasonCode: picked.reasonCode, blocking: p.blockingOnly, limit: PAGE, offset }, s)
      : null,
    [picked?.stage, picked?.reasonCode, offset, p.env, p.runId, p.blockingOnly],
    p.onUnauthorized,
  )
  const visible = p.blockingOnly ? p.reasons.filter(r => r.blocking > 0) : p.reasons

  return (
    <section className="ops-card ops-drill">
      <div className="ops-drill-head">
        <div>
          <h2 className="ops-h2">실패 원인 · {p.title}</h2>
          <p className="ops-muted ops-small">{p.source}</p>
        </div>
        <div className="ops-row">
          {p.stageFocus && <button type="button" className="ops-tag ops-tag-btn" onClick={p.onClearStage}>{stageLabel(p.stageFocus)} ✕</button>}
          <button type="button" className="ops-btn ops-btn-ghost" onClick={p.onClose}>닫기</button>
        </div>
      </div>

      <div className="ops-drill-cols">
        <div>
          <p className="ops-step">① 원인별</p>
          {visible.length === 0 && <p className="ops-muted">{p.group === 'model' ? 'Gold 건별 기록은 아직 이 화면에서 볼 수 없어요. 단계 띠의 실패 수를 보세요.' : '이 범위의 실패가 없어요.'}</p>}
          <ul className="ops-pick">
            {visible.map(r => {
              const n = p.blockingOnly ? r.blocking : r.actionable
              const on = picked?.stage === r.stage && picked.reasonCode === r.reasonCode
              return (
                <li key={`${r.stage}:${r.reasonCode}`}>
                  <button type="button" className={on ? 'is-on' : ''} aria-pressed={on} onClick={() => { setPicked(r); setOffset(0); setItem(null) }}>
                    <span className="ops-pick-top"><code>{r.reasonCode}</code><strong>{fmtNum(n)}건</strong></span>
                    <span className="ops-small">{p.reasonNotes[r.reasonCode]?.label ?? stageLabel(r.stage)}</span>
                    {p.reasonNotes[r.reasonCode]?.action && <span className="ops-small ops-muted">조치: {p.reasonNotes[r.reasonCode]?.action}</span>}
                    {(r.knownException > 0 || r.recovered > 0) && (
                      <span className="ops-small ops-muted">
                        {r.knownException > 0 && `알려진 예외 ${r.knownException}건 `}{r.recovered > 0 && `재시도 성공 ${r.recovered}건`} (빼고 셈)
                      </span>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>

        <div>
          <p className="ops-step">② 건별{picked ? ` (${picked.reasonCode})` : ''}</p>
          {!picked && <p className="ops-muted">왼쪽에서 원인을 고르세요.</p>}
          {picked && list.loading && <p className="ops-muted">불러오는 중…</p>}
          {picked && list.error && <p className="ops-error">{list.error.message}</p>}
          {picked && list.data && (
            <>
              <ul className="ops-pick">
                {list.data.items.map(f => (
                  <li key={f.id ?? `${f.subjectKey}:${f.observedAt}`}>
                    <button type="button" className={item?.id === f.id ? 'is-on' : ''} aria-pressed={item?.id === f.id} onClick={() => setItem(f)}>
                      <span className="ops-pick-top">
                        <code>{f.subjectKey}</code>
                        <span className="ops-row-gap">
                          {f.blocking && <span className="ops-tag ops-tag-bad">차단</span>}
                          {f.knownException && <span className="ops-tag">알려진 예외</span>}
                          {f.recovered && <span className="ops-tag">재시도 성공</span>}
                        </span>
                      </span>
                      {f.message && <span className="ops-small">{f.message}</span>}
                      <span className="ops-small ops-muted">{f.subjectType} · 시도 {f.attempts ?? '—'} · {fmtTime(f.observedAt)}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <Pager total={list.data.total} offset={offset} onPage={setOffset} />
            </>
          )}
        </div>

        <div>
          <p className="ops-step">③ 증거 (원문)</p>
          {!item && <p className="ops-muted">건을 고르면 기록된 증거가 보여요.</p>}
          {item && <Evidence item={item} onTrace={p.onTrace} />}
        </div>
      </div>
    </section>
  )
}

function Pager({ total, offset, onPage }: { total: number; offset: number; onPage: (o: number) => void }) {
  if (total <= PAGE) return <p className="ops-foot">{fmtNum(total)}건</p>
  return (
    <div className="ops-row ops-foot">
      <button type="button" className="ops-btn ops-btn-ghost" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE))}>이전</button>
      <span>{fmtNum(offset + 1)}–{fmtNum(Math.min(total, offset + PAGE))} / {fmtNum(total)}</span>
      <button type="button" className="ops-btn ops-btn-ghost" disabled={offset + PAGE >= total} onClick={() => onPage(offset + PAGE)}>다음</button>
    </div>
  )
}

/** subject_key 에서 공고번호: 공고 = 번호, 사진 칸 = 번호#칸 */
export function desertionOf(f: OpsFailure) {
  if (f.subjectType !== 'animal' && f.subjectType !== 'photo_slot') return null
  const no = f.subjectKey.split('#')[0]
  return /^\d{6,20}$/.test(no) ? no : null
}

export function Evidence({ item, onTrace }: { item: OpsFailure; onTrace?: (no: string) => void }) {
  const no = desertionOf(item)
  const rows: [string, string][] = [
    ['stage', item.stage],
    ['subject', `${item.subjectType} · ${item.subjectKey}`],
    ['reason_code', item.reasonCode],
    ['run_id', item.runId ?? '—'],
    ...(item.publishId ? [['publish_id', item.publishId] as [string, string]] : []),
    ...(item.evidenceRef ? [['evidence_ref', item.evidenceRef] as [string, string]] : []),
    ...Object.entries(item.evidence).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)] as [string, string]),
  ]
  return (
    <div className="ops-evidence">
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd><code>{v}</code></dd></div>
        ))}
      </dl>
      {no && onTrace && <button type="button" className="ops-btn ops-btn-primary" onClick={() => onTrace(no)}>공고 {no} 추적</button>}
    </div>
  )
}
