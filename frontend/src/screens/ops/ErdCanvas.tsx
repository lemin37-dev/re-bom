'use client'

import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ErdRef, ErdTable, OpsErd, OpsSchemaTable } from '@/types/ops'

const LAYER_TITLE: Record<string, string> = { bronze: 'Bronze · S3 파일', silver: 'Silver · Iceberg', gold: 'Gold · Iceberg', rds: 'RDS · PostgreSQL' }
const FK_COLOR = '#2b2b2b'
const MAX_PLAIN = 3 // 연결 키가 아닌 칼럼은 몇 개까지 보일지

export function refColor(r: ErdRef) {
  return r.kind === 'fk' ? FK_COLOR : r.color ?? '#8a8a8a'
}

type Box = { left: number; right: number; top: number; bottom: number; cx: number; cy: number }
type Line = { id: string; d: string; color: string; strong: boolean; fk: boolean }

/**
 * 층(열) × 영역(TableGroup) 으로 표를 놓고, 연결(Ref)을 칼럼 줄끼리 선으로 잇는다.
 * 선 위치는 화면에 그려진 칼럼 줄의 위치를 재서 계산한다(창 크기가 바뀌면 다시 잰다).
 */
export function ErdCanvas({ erd, focus, keyFilter, compare, onFocus }: {
  erd: OpsErd
  focus: string | null
  keyFilter: string | null
  compare: Map<string, OpsSchemaTable>
  onFocus: (table: string | null) => void
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const [boxes, setBoxes] = useState<Map<string, Box>>(new Map())
  const [size, setSize] = useState({ w: 0, h: 0 })

  const refs = useMemo(() => erd.refs.filter(r => !keyFilter || r.key === keyFilter), [erd.refs, keyFilter])
  const keyCols = useMemo(() => {
    const m = new Map<string, Map<string, string>>()
    for (const r of erd.refs) {
      for (const end of [r.from, r.to]) {
        const cols = m.get(end.table) ?? new Map<string, string>()
        for (const c of end.columns) if (!cols.has(c)) cols.set(c, refColor(r))
        m.set(end.table, cols)
      }
    }
    return m
  }, [erd.refs])
  const related = useMemo(() => {
    if (!focus) return null
    const s = new Set([focus])
    for (const r of refs) {
      if (r.from.table === focus) s.add(r.to.table)
      if (r.to.table === focus) s.add(r.from.table)
    }
    return s
  }, [focus, refs])
  const filtered = useMemo(() => (keyFilter ? new Set(refs.flatMap(r => [r.from.table, r.to.table])) : null), [keyFilter, refs])

  const measure = useCallback(() => {
    const el = wrap.current
    if (!el) return
    const base = el.getBoundingClientRect()
    const m = new Map<string, Box>()
    el.querySelectorAll<HTMLElement>('[data-anchor]').forEach(a => {
      const r = a.getBoundingClientRect()
      const left = r.left - base.left
      const top = r.top - base.top
      m.set(a.dataset.anchor as string, { left, right: left + r.width, top, bottom: top + r.height, cx: left + r.width / 2, cy: top + r.height / 2 })
    })
    setBoxes(m)
    setSize({ w: el.scrollWidth, h: el.scrollHeight })
  }, [])

  useLayoutEffect(() => {
    measure()
    const el = wrap.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => measure())
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure, erd, focus, keyFilter])

  const lines: Line[] = useMemo(() => {
    const out: Line[] = []
    refs.forEach((r, i) => {
      const a = boxes.get(`${r.from.table}.${r.from.columns[0]}`) ?? boxes.get(r.from.table)
      const b = boxes.get(`${r.to.table}.${r.to.columns[0]}`) ?? boxes.get(r.to.table)
      if (!a || !b) return
      let d: string
      if (Math.abs(a.cx - b.cx) < 60) {
        // 같은 열: 오른쪽으로 둥글게 돌아 잇는다
        const x = Math.max(a.right, b.right)
        const bend = 28 + Math.min(60, Math.abs(a.cy - b.cy) / 6)
        d = `M${a.right},${a.cy} C${x + bend},${a.cy} ${x + bend},${b.cy} ${b.right},${b.cy}`
      } else if (a.cx < b.cx) {
        const dx = (b.left - a.right) / 2
        d = `M${a.right},${a.cy} C${a.right + dx},${a.cy} ${b.left - dx},${b.cy} ${b.left},${b.cy}`
      } else {
        const dx = (a.left - b.right) / 2
        d = `M${a.left},${a.cy} C${a.left - dx},${a.cy} ${b.right + dx},${b.cy} ${b.right},${b.cy}`
      }
      const strong = focus ? r.from.table === focus || r.to.table === focus : true
      out.push({ id: `${i}`, d, color: refColor(r), strong, fk: r.kind === 'fk' })
    })
    return out.sort((x, y) => Number(x.strong) - Number(y.strong))
  }, [refs, boxes, focus])

  const byLayer = (layer: string) => {
    const groups = erd.groups.filter(g => g.layer === layer)
    const tables = erd.tables.filter(t => t.layer === layer)
    const sections = groups.map(g => ({ key: g.group, color: g.color, note: g.note, tables: tables.filter(t => t.group === g.group) }))
    const loose = tables.filter(t => !t.group || !groups.some(g => g.group === t.group))
    if (loose.length) sections.push({ key: '기타', color: null, note: null, tables: loose })
    return sections.filter(s => s.tables.length)
  }

  return (
    <div className="ops-erd-scroll">
      <div className="ops-erd" ref={wrap}>
        <svg className="ops-erd-lines" width={size.w} height={size.h} aria-hidden="true">
          {lines.map(l => (
            <path key={l.id} d={l.d} fill="none" stroke={l.color} strokeWidth={l.strong ? (l.fk ? 2.6 : 1.8) : 1}
              strokeOpacity={focus ? (l.strong ? 0.95 : 0.08) : 0.45} />
          ))}
        </svg>
        {erd.layers.map(layer => (
          <div key={layer} className={`ops-erd-col ops-layer-${layer}`}>
            <p className="ops-flow-layer">{LAYER_TITLE[layer] ?? layer}</p>
            {byLayer(layer).map(s => (
              <div key={s.key} className="ops-erd-group" style={s.color ? { borderColor: `${s.color}55`, background: `${s.color}0d` } : undefined}>
                <p className="ops-erd-group-name" title={s.note ?? undefined} style={s.color ? { color: s.color } : undefined}>{s.key}</p>
                {s.tables.map(t => (
                  <TableCard key={t.table} t={t} keys={keyCols.get(t.table)} compare={compare.get(t.table)}
                    dim={Boolean((related && !related.has(t.table)) || (filtered && !filtered.has(t.table)))}
                    on={focus === t.table} onClick={() => onFocus(focus === t.table ? null : t.table)} />
                ))}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function TableCard({ t, keys, compare, dim, on, onClick }: {
  t: ErdTable
  keys: Map<string, string> | undefined
  compare: OpsSchemaTable | undefined
  dim: boolean
  on: boolean
  onClick: () => void
}) {
  const keyRows = t.columns.filter(c => keys?.has(c.name) || c.pk)
  const plain = t.columns.filter(c => !keys?.has(c.name) && !c.pk)
  const shown = plain.slice(0, Math.max(0, MAX_PLAIN - Math.max(0, keyRows.length - 3)))
  const more = t.columns.length - keyRows.length - shown.length
  const bad = compare?.observed && !compare.matches
  return (
    <div className={`ops-erd-table${dim ? ' is-dim' : ''}${on ? ' is-on' : ''}${bad ? ' is-bad' : ''}`} data-anchor={t.table}>
      <button type="button" className="ops-erd-table-head" onClick={onClick} aria-pressed={on} title={t.note ?? undefined}>
        <span>{t.table}</span>
        {bad && <span className="ops-tag ops-tag-bad">다름</span>}
      </button>
      <ul>
        {keyRows.map(c => (
          <li key={c.name} data-anchor={`${t.table}.${c.name}`} className="is-key">
            <span className="ops-key-dot" style={{ background: keys?.get(c.name) ?? 'transparent' }} aria-hidden="true" />
            <span className="ops-erd-col-name">{c.name}{c.pk && <em> PK</em>}</span>
            <span className="ops-erd-col-type">{c.type}</span>
          </li>
        ))}
        {shown.map(c => (
          <li key={c.name}>
            <span className="ops-key-dot" aria-hidden="true" />
            <span className="ops-erd-col-name">{c.name}</span>
            <span className="ops-erd-col-type">{c.type}</span>
          </li>
        ))}
        {more > 0 && <li className="ops-erd-more">+ 칼럼 {more}개</li>}
      </ul>
    </div>
  )
}
