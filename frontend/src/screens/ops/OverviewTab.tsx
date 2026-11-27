'use client'

import { useMemo, useState } from 'react'
import { loadErd } from '@/lib/ops-api'
import type { OpsEnv, OpsOverview, OpsReasonCount, OpsStage } from '@/types/ops'
import { AnimalTrace } from './AnimalTrace'
import { FailureDrill, type DrillGroup } from './FailureDrill'
import { enumNotes, fmtDuration, fmtNum, fmtTime, stageLabel, stageLayer, STATUS_LABELS } from './format'
import { RunTrend } from './RunTrend'
import { type Loaded, useOpsData } from './useOps'

const GROUPS: Record<DrillGroup, { title: string; match: (stage: string) => boolean; blockingOnly?: boolean; source: string }> = {
  collect: { title: '수집 실패', match: s => s === 'bronze' || s === 'photo', source: '출처 ops_failure (수집 manifest·사진 기록에서 요약 태스크가 씀)' },
  clean: { title: '정제 차단', match: s => s.startsWith('silver'), blockingOnly: true, source: '출처 ops_failure · 실행을 멈춘 건만 (값 보류는 세지 않음)' },
  model: { title: '모델 처리 실패', match: s => s.startsWith('gold'), source: '건별은 Gold 처리 로그에 있어요. 지금 화면은 단계 요약의 실패 수만 보여요' },
  sync: { title: 'RDS 동기화 실패', match: s => s.startsWith('rds'), source: '출처 ops_failure (동기화 경로별)' },
}

const COLLECTION_TEXT = {
  ok: '정상 수집',
  aborted: '수집 중단',
  not_run: '오늘 실행 안 됨',
} as const

export function OverviewTab({ env, overview, onUnauthorized }: { env: OpsEnv; overview: Loaded<OpsOverview>; onUnauthorized: () => void }) {
  const [group, setGroup] = useState<DrillGroup | null>(null)
  const [stageFocus, setStageFocus] = useState<string | null>(null)
  const [trace, setTrace] = useState<string | null>(null)
  const erd = useOpsData(() => loadErd(), [], onUnauthorized)
  const reasonNotes = useMemo(() => enumNotes(erd.data?.tables, 'ops_failure', 'reason_code'), [erd.data])

  const { data, error, loading } = overview
  if (loading && !data) return <p className="ops-muted ops-pad">불러오는 중…</p>
  if (error && !data) return <ErrorCard message={error.message} onRetry={overview.reload} />
  if (!data) return null

  const c = data.cards
  const transitions = Object.entries(c.transitions ?? {})
  const transitionTotal = transitions.reduce((n, [, v]) => n + v, 0)
  const pick = (g: DrillGroup, stage: string | null = null) => {
    setGroup(g)
    setStageFocus(stage)
    requestAnimationFrame(() => document.getElementById('ops-drill')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  return (
    <div className="ops-stack">
      <CollectionBanner data={data} onReload={overview.reload} loading={loading} />

      <section className="ops-cards" aria-label="핵심 숫자">
        <Kpi label="받은 공고" value={c.received} hint="Bronze 가 받은 건수" />
        <Kpi label="신규" value={c.inserted} hint="Silver 에 새로 들어온 공고" />
        <Kpi label="내용 변경" value={c.updated} hint="정제행이 바뀐 공고 (updTm 제외 비교)" />
        <Kpi label="상태 변화" value={transitionTotal} hint="processState 가 바뀐 공고" />
        <Kpi label="수집 실패" value={c.collectFailures} tone="bad" hint="알려진 예외·재시도 성공 제외" onClick={() => pick('collect')} active={group === 'collect'} />
        <Kpi label="정제 차단" value={c.cleanBlocking} tone="bad" hint="Silver 실행을 멈춘 건" onClick={() => pick('clean')} active={group === 'clean'} />
        <Kpi label="모델 실패" value={c.modelFailures} tone="bad" hint="Gold 단계 실패 수" onClick={() => pick('model')} active={group === 'model'} />
        <Kpi label="동기화 실패" value={c.syncFailures} tone="bad" hint="RDS 반영 실패" onClick={() => pick('sync')} active={group === 'sync'} />
      </section>

      <StageFlow data={data} onPick={s => pick(groupOf(s), s)} />

      {group && (
        <div id="ops-drill">
          <FailureDrill
            key={`${group}:${stageFocus ?? ''}`}
            env={env}
            runId={data.runId}
            group={group}
            title={GROUPS[group].title}
            source={GROUPS[group].source}
            blockingOnly={Boolean(GROUPS[group].blockingOnly)}
            reasons={data.reasons.filter(r => GROUPS[group].match(r.stage) && (!stageFocus || r.stage === stageFocus))}
            stageFocus={stageFocus}
            onClearStage={() => setStageFocus(null)}
            reasonNotes={reasonNotes}
            onTrace={no => { setTrace(no); requestAnimationFrame(() => document.getElementById('ops-trace')?.scrollIntoView({ behavior: 'smooth' })) }}
            onClose={() => { setGroup(null); setStageFocus(null) }}
            onUnauthorized={onUnauthorized}
          />
        </div>
      )}

      <div className="ops-grid-2">
        <section className="ops-card">
          <h2 className="ops-h2">상태 변화 내역 <span className="ops-count">{fmtNum(transitionTotal)}건</span></h2>
          {transitions.length === 0
            ? <p className="ops-muted">이번 실행에서 상태가 바뀐 공고가 없어요.</p>
            : <ul className="ops-bars">{transitions.map(([k, v]) => <Bar key={k} label={k} n={v} max={transitions[0][1]} />)}</ul>}
          <p className="ops-foot">processState 문자열 비교. (없음) → 보호중 은 처음 본 공고예요.</p>
        </section>
        <ReasonSummary reasons={data.reasons} notes={reasonNotes} onPick={r => pick(groupOf(r.stage), r.stage)} />
      </div>

      <div id="ops-trace">
        <AnimalTrace key={trace ?? ''} env={env} initial={trace} onUnauthorized={onUnauthorized} reasonNotes={reasonNotes} />
      </div>

      <RunTrend env={env} onUnauthorized={onUnauthorized} />
    </div>
  )
}

function groupOf(stage: string): DrillGroup {
  const layer = stageLayer(stage)
  return layer === 'bronze' ? 'collect' : layer === 'silver' ? 'clean' : layer === 'gold' ? 'model' : 'sync'
}

function CollectionBanner({ data, onReload, loading }: { data: OpsOverview; onReload: () => void; loading: boolean }) {
  const col = data.collection
  const tone = col.status === 'ok' ? (col.warn ? 'warn' : 'ok') : 'bad'
  return (
    <section className={`ops-card ops-banner ops-tone-${tone}`} aria-live="polite">
      <div>
        <p className="ops-banner-state">
          <span className={`ops-dot ops-dot-${tone}`} aria-hidden="true" />
          {COLLECTION_TEXT[col.status]}{col.status === 'ok' && col.warn ? ' · 주의 건 있음' : ''}
        </p>
        <p className="ops-muted">
          오늘(KST) {col.today} · 마지막 실행 {fmtTime(col.lastRunAt)} ({col.lastRunStatus ? STATUS_LABELS[col.lastRunStatus] ?? col.lastRunStatus : '없음'})
          {' · '}마지막 성공 {fmtTime(col.lastSuccessAt)}
        </p>
      </div>
      <div className="ops-banner-ids">
        <code title="bronze_run_id">{data.runId ?? '실행 기록 없음'}</code>
        <button type="button" className="ops-btn ops-btn-ghost" onClick={onReload} disabled={loading}>{loading ? '새로고침…' : '새로고침'}</button>
      </div>
      {col.status === 'not_run' && (
        <p className="ops-banner-note">오늘 날짜의 수집 기록이 없어요. 아래 숫자는 마지막 수집일({data.date ?? '—'}) 기준이에요. API 가 4번 모두 실패한 날도 이렇게 보이며, 그 경우는 Slack 태스크 실패 알림으로 확인해요.</p>
      )}
    </section>
  )
}

function Kpi({ label, value, hint, tone, onClick, active }: { label: string; value: number | null; hint: string; tone?: 'bad'; onClick?: () => void; active?: boolean }) {
  const bad = tone === 'bad' && (value ?? 0) > 0
  const body = (
    <>
      <span className="ops-kpi-label">{label}</span>
      <strong className="ops-kpi-value">{fmtNum(value)}</strong>
      <span className="ops-kpi-hint">{hint}</span>
    </>
  )
  if (!onClick) return <div className="ops-kpi">{body}</div>
  return (
    <button type="button" className={`ops-kpi ops-kpi-btn${bad ? ' is-bad' : ''}${active ? ' is-active' : ''}`} onClick={onClick} aria-pressed={active}>
      {body}
    </button>
  )
}

function StageFlow({ data, onPick }: { data: OpsOverview; onPick: (stage: string) => void }) {
  const layers: { key: string; title: string }[] = [
    { key: 'bronze', title: 'Bronze' },
    { key: 'silver', title: 'Silver' },
    { key: 'gold', title: 'Gold' },
    { key: 'rds', title: 'RDS' },
  ]
  const byLayer = (k: string) => data.stages.filter(s => stageLayer(s.stage) === k)
  const publish = data.stages.find(s => s.stage === 'silver_publish')
  return (
    <section className="ops-card">
      <h2 className="ops-h2">단계 진행 · Bronze → Silver → Gold → RDS</h2>
      <p className="ops-muted ops-ids">
        bronze_run_id <code>{data.runId ?? '—'}</code>
        {publish?.publishId && <> · publish_id <code>{publish.publishId}</code></>}
      </p>
      {data.stages.length === 0
        ? <p className="ops-muted">이 실행의 단계 기록이 아직 없어요. 다음 수집 실행의 마지막 태스크(write_ops_summary)가 채워요.</p>
        : (
          <div className="ops-flow">
            {layers.map(l => (
              <div key={l.key} className={`ops-flow-col ops-layer-${l.key}`}>
                <p className="ops-flow-layer">{l.title}</p>
                {byLayer(l.key).length === 0 && <p className="ops-muted ops-small">기록 없음</p>}
                {byLayer(l.key).map(s => <StageBox key={s.stage} s={s} onPick={onPick} />)}
              </div>
            ))}
          </div>
        )}
      <p className="ops-foot">단계나 실패 카드를 누르면 아래에서 원인 → 건별 → 증거 순서로 내려가요.</p>
    </section>
  )
}

function StageBox({ s, onPick }: { s: OpsStage; onPick: (stage: string) => void }) {
  const dur = fmtDuration(s.startedAt, s.finishedAt)
  return (
    <button type="button" className={`ops-stage ops-status-${s.status}`} onClick={() => onPick(s.stage)}>
      <span className="ops-stage-head">
        <strong>{stageLabel(s.stage)}</strong>
        <span className={`ops-chip ops-chip-${s.status}`}>{STATUS_LABELS[s.status] ?? s.status}</span>
      </span>
      <span className="ops-stage-nums">
        {fmtNum(s.inputCount)} → {fmtNum(s.outputCount)}
        {(s.failedCount ?? 0) > 0 && <em> · 실패 {fmtNum(s.failedCount)}</em>}
      </span>
      <span className="ops-small ops-muted">{fmtTime(s.startedAt, false)}{dur ? ` · ${dur}` : ''}</span>
    </button>
  )
}

function Bar({ label, n, max }: { label: string; n: number; max: number }) {
  return (
    <li>
      <span className="ops-bar-label">{label}</span>
      <span className="ops-bar-track" aria-hidden="true"><span style={{ width: `${Math.max(4, (n / Math.max(1, max)) * 100)}%` }} /></span>
      <span className="ops-bar-n">{fmtNum(n)}</span>
    </li>
  )
}

function ReasonSummary({ reasons, notes, onPick }: { reasons: OpsReasonCount[]; notes: Record<string, { label: string; action: string | null }>; onPick: (r: OpsReasonCount) => void }) {
  return (
    <section className="ops-card">
      <h2 className="ops-h2">원인별 건수 <span className="ops-count">이번 실행</span></h2>
      {reasons.length === 0
        ? <p className="ops-muted">기록된 실패가 없어요.</p>
        : (
          <ul className="ops-reason-list">
            {reasons.map(r => (
              <li key={`${r.stage}:${r.reasonCode}`}>
                <button type="button" onClick={() => onPick(r)}>
                  <span><code>{r.reasonCode}</code> <span className="ops-muted ops-small">{stageLabel(r.stage)}</span></span>
                  <span className="ops-small">{notes[r.reasonCode]?.label ?? ''}</span>
                  <span className="ops-reason-n">
                    <strong>{fmtNum(r.actionable)}</strong>
                    {r.knownException > 0 && <span className="ops-tag">알려진 예외 {r.knownException}</span>}
                    {r.recovered > 0 && <span className="ops-tag">재시도 성공 {r.recovered}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      <p className="ops-foot">굵은 숫자 = 실제로 봐야 할 건. 알려진 예외(2023-02-11·2024-05-09·2025-09-09 null 레코드)와 재시도로 받아낸 건은 빼고 셉니다.</p>
    </section>
  )
}

export function ErrorCard({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="ops-card ops-notice ops-notice-bad" role="alert">
      <p>{message}</p>
      {onRetry && <button type="button" className="ops-btn ops-btn-ghost" onClick={onRetry}>다시 시도</button>}
    </div>
  )
}
