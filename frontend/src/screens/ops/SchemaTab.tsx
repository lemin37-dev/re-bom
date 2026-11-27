'use client'

import { useMemo, useState } from 'react'
import { loadErd, opsApi } from '@/lib/ops-api'
import type { ErdRef, ErdTable, OpsEnv, OpsSchemaTable } from '@/types/ops'
import { ErdCanvas, refColor } from './ErdCanvas'
import { fmtTime, shortHash } from './format'
import { ErrorCard } from './OverviewTab'
import { useOpsData } from './useOps'

const LAYER_TITLE: Record<string, string> = { bronze: 'Bronze', silver: 'Silver', gold: 'Gold', rds: 'RDS' }

export function SchemaTab({ env, onUnauthorized }: { env: OpsEnv; onUnauthorized: () => void }) {
  const erd = useOpsData(() => loadErd(), [], onUnauthorized)
  const schema = useOpsData(s => opsApi.schema(env, s), [env], onUnauthorized)
  const [focus, setFocus] = useState<string | null>(null)
  const [keyFilter, setKeyFilter] = useState<string | null>(null)

  const keys = useMemo(() => {
    const m = new Map<string, string>()
    for (const r of erd.data?.refs ?? []) if (!m.has(r.key)) m.set(r.key, refColor(r))
    return [...m.entries()]
  }, [erd.data])
  const compare = useMemo(() => new Map((schema.data?.tables ?? []).map(t => [t.table, t])), [schema.data])
  const detail = erd.data?.tables.find(t => t.table === focus) ?? null

  const pickTable = (name: string | null) => {
    setFocus(name)
    if (name) requestAnimationFrame(() => document.getElementById('ops-erd')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  return (
    <div className="ops-stack">
      <section className="ops-card" id="ops-erd">
        <div className="ops-drill-head">
          <div>
            <h2 className="ops-h2">테이블 흐름 ERD · DBML</h2>
            <p className="ops-muted ops-small">원본 docs/schema/bronze·silver·gold·rds.dbml · 층 사이 연결 links.dbml · API 이미지에 든 카탈로그</p>
          </div>
          {focus && <button type="button" className="ops-btn ops-btn-ghost" onClick={() => setFocus(null)}>선택 해제</button>}
        </div>
        {keys.length > 0 && (
          <div className="ops-keys" role="group" aria-label="연결 키로 거르기">
            <span className="ops-small ops-muted">연결 키</span>
            {keys.map(([k, color]) => (
              <button key={k} type="button" className={`ops-key${keyFilter === k ? ' is-on' : ''}`} aria-pressed={keyFilter === k} onClick={() => setKeyFilter(keyFilter === k ? null : k)}>
                <span className="ops-key-dot" style={{ background: color }} aria-hidden="true" />{k}
              </button>
            ))}
          </div>
        )}
        {erd.error && <ErrorCard message={erd.error.message} onRetry={erd.reload} />}
        {erd.loading && !erd.data && <p className="ops-muted">불러오는 중…</p>}
        {erd.data && <ErdCanvas erd={erd.data} focus={focus} keyFilter={keyFilter} compare={compare} onFocus={setFocus} />}
        <p className="ops-foot">
          색 실선 = 같은 키 값으로 잇는 처리 연결(Iceberg·S3 에는 외래키 없음) · 검은 굵은 선 = RDS 실제 외래키.
          표를 누르면 그 표의 연결만 진하게 보이고 아래에 칼럼 설명과 예시가 나와요.
        </p>
      </section>

      {detail && erd.data && <TableDetail table={detail} refs={erd.data.refs} compare={compare.get(detail.table)} onPick={pickTable} />}

      <section className="ops-card">
        <h2 className="ops-h2">스키마 비교 · 문서(DBML) 칼럼 vs 실제 칼럼</h2>
        <p className="ops-muted ops-small">
          실제값: RDS 는 information_schema, Silver·Gold 는 요약 태스크가 기록한 ops_schema_snapshot. 층마다 타입 표기가 달라 칼럼 이름으로 판정하고,
          지문(칼럼 이름·타입의 sha256)과 Iceberg schema-id 는 참고로 보여 줘요. Bronze 는 파일이라 비교하지 않아요.
        </p>
        {schema.error && <ErrorCard message={schema.error.message} onRetry={schema.reload} />}
        {schema.loading && !schema.data && <p className="ops-muted">불러오는 중…</p>}
        {schema.data && <CompareTable rows={schema.data.tables} onPick={pickTable} />}
      </section>

      {schema.data && (
        <section className="ops-card">
          <h2 className="ops-h2">RDS 마이그레이션 · 스키마 {schema.data.rdsVersion ?? '—'}</h2>
          {schema.data.pendingMigrations.length > 0
            ? <p className="ops-warn">아직 적용 안 된 파일: {schema.data.pendingMigrations.join(', ')} → 사람이 python -m rebom.common.migrate apply 로 적용</p>
            : <p className="ops-muted">이미지에 든 마이그레이션이 모두 적용돼 있어요.</p>}
          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead><tr><th scope="col">파일</th><th scope="col">적용</th><th scope="col">sha256</th><th scope="col">적용 시각</th><th scope="col">적용한 사람</th></tr></thead>
              <tbody>
                {schema.data.migrations.map(m => (
                  <tr key={m.filename}>
                    <td><code>{m.filename}</code></td>
                    <td>{m.applied ? '적용됨' : <span className="ops-warn">안 됨</span>}</td>
                    <td>{m.shaMatches === null ? '—' : m.shaMatches ? '같음' : <span className="ops-bad">다름 (파일이 바뀜)</span>}</td>
                    <td>{fmtTime(m.appliedAt)}</td>
                    <td>{m.appliedBy ?? '—'}{m.how ? ` · ${m.how}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}

export function verdict(c: OpsSchemaTable | undefined): { text: string; tone: 'ok' | 'bad' | 'none' } {
  if (!c) return { text: '—', tone: 'none' }
  if (c.observed === null) return { text: '대상 아님', tone: 'none' }
  if (!c.observed) return { text: '관측 없음', tone: 'none' }
  return c.matches ? { text: '일치', tone: 'ok' } : { text: '다름', tone: 'bad' }
}

function CompareTable({ rows, onPick }: { rows: OpsSchemaTable[]; onPick: (t: string) => void }) {
  return (
    <div className="ops-table-wrap">
      <table className="ops-table">
        <thead>
          <tr>
            <th scope="col">표</th><th scope="col">층</th><th scope="col">문서 칼럼</th><th scope="col">문서 지문</th>
            <th scope="col">실제 지문</th><th scope="col">schema-id · 바뀐 횟수</th><th scope="col">판정</th><th scope="col">다른 점</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const v = verdict(r)
            return (
              <tr key={r.table}>
                <td><button type="button" className="ops-link" onClick={() => onPick(r.table)}>{r.table}</button>{r.physicalName && r.physicalName !== r.table && <div className="ops-small ops-muted">{r.physicalName}</div>}</td>
                <td>{LAYER_TITLE[r.layer] ?? r.layer}</td>
                <td>{r.docColumns}</td>
                <td><code title={r.docFingerprint}>{shortHash(r.docFingerprint)}</code></td>
                <td><code title={r.fingerprint}>{shortHash(r.fingerprint)}</code></td>
                <td>{r.schemaId ?? '—'}{r.versions ? ` · ${r.versions}` : ''}</td>
                <td><span className={`ops-chip ops-chip-${v.tone === 'ok' ? 'ok' : v.tone === 'bad' ? 'failed' : 'skipped'}`}>{v.text}</span></td>
                <td className="ops-small">
                  {(r.missingInReal?.length ?? 0) > 0 && <div>실제에 없음: {r.missingInReal?.join(', ')}</div>}
                  {(r.extraInReal?.length ?? 0) > 0 && <div>문서에 없음: {r.extraInReal?.join(', ')}</div>}
                  {r.observedAt && <div className="ops-muted">관측 {fmtTime(r.observedAt)}</div>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function TableDetail({ table, refs, compare, onPick }: { table: ErdTable; refs: ErdRef[]; compare: OpsSchemaTable | undefined; onPick: (t: string) => void }) {
  const mine = refs.filter(r => r.from.table === table.table || r.to.table === table.table)
  const keyColor = new Map<string, string>()
  for (const r of mine) {
    const end = r.from.table === table.table ? r.from : r.to
    for (const c of end.columns) if (!keyColor.has(c)) keyColor.set(c, refColor(r))
  }
  const v = verdict(compare)
  const missing = new Set(compare?.missingInReal ?? [])
  return (
    <section className="ops-card ops-detail">
      <div className="ops-drill-head">
        <div>
          <h2 className="ops-h2"><code>{table.table}</code> <span className={`ops-chip ops-chip-${v.tone === 'ok' ? 'ok' : v.tone === 'bad' ? 'failed' : 'skipped'}`}>{v.text}</span></h2>
          <p className="ops-muted ops-small">{table.source} · {LAYER_TITLE[table.layer] ?? table.layer}{table.group ? ` · 영역 ${table.group}` : ''} · 칼럼 {table.columns.length}개</p>
        </div>
      </div>
      {table.note && <p className="ops-detail-note">{table.note}</p>}
      <div className="ops-table-wrap">
        <table className="ops-table">
          <thead><tr><th scope="col">연결 키</th><th scope="col">칼럼</th><th scope="col">타입</th><th scope="col">설명</th><th scope="col">예</th></tr></thead>
          <tbody>
            {table.columns.map(c => (
              <tr key={c.name} className={missing.has(c.name) ? 'is-missing' : ''}>
                <td>{keyColor.has(c.name) && <span className="ops-key-dot" style={{ background: keyColor.get(c.name) }} aria-label="연결 키" />}{c.pk && <span className="ops-tag">PK</span>}</td>
                <td><code>{c.name}</code>{c.notNull && <span className="ops-small ops-muted"> not null</span>}{missing.has(c.name) && <div className="ops-small ops-bad">실제 표에 없음</div>}</td>
                <td><code className="ops-small">{c.type}</code></td>
                <td className="ops-small">
                  <span className="ops-pre">{c.note}</span>
                  {c.values && (
                    <ul className="ops-values">
                      {c.values.map(val => <li key={val.value}><code>{val.value}</code>{val.note ? ` ${val.note}` : ''}</li>)}
                    </ul>
                  )}
                </td>
                <td className="ops-small"><code>{c.example ?? '—'}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="ops-h3">이 표의 연결 {mine.length}개</h3>
      <ul className="ops-links">
        {mine.map((r, i) => {
          const out = r.from.table === table.table
          const other = out ? r.to : r.from
          return (
            <li key={i}>
              <span className="ops-key-dot" style={{ background: refColor(r) }} aria-hidden="true" />
              <span>
                <code>{(out ? r.from : r.to).columns.join(', ')}</code> {out ? '→ 읽은 쪽' : '← 만든 쪽'}{' '}
                <button type="button" className="ops-link" onClick={() => onPick(other.table)}>{other.table}</button>.<code>{other.columns.join(', ')}</code>
                <span className="ops-muted ops-small"> · {r.key}{r.kind === 'fk' ? ' · RDS 외래키' : ''}{r.how ? ` · ${r.how}` : ''}</span>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
