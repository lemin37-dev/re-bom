import type { ErdTable } from '@/types/ops'

const KST = 'Asia/Seoul'

export function fmtNum(n: number | null | undefined) {
  return n === null || n === undefined ? '—' : n.toLocaleString('ko-KR')
}

/** ISO 시각 → "11/27 12:03" (KST) */
export function fmtTime(iso: string | null | undefined, withDate = true) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const opts: Intl.DateTimeFormatOptions = { timeZone: KST, hour: '2-digit', minute: '2-digit', hour12: false }
  if (withDate) Object.assign(opts, { month: 'numeric', day: 'numeric' })
  return new Intl.DateTimeFormat('ko-KR', opts).format(d)
}

export function fmtDuration(start: string | null, end: string | null) {
  if (!start || !end) return null
  const s = (new Date(end).getTime() - new Date(start).getTime()) / 1000
  if (!Number.isFinite(s) || s < 0) return null
  if (s < 90) return `${Math.round(s)}초`
  if (s < 5400) return `${Math.round(s / 60)}분`
  return `${(s / 3600).toFixed(1)}시간`
}

export const STAGE_LABELS: Record<string, string> = {
  bronze: '수집 (Bronze)',
  photo: '사진 받기',
  silver_notice: '공고 정제 (Silver)',
  silver_photo: '사진 정제',
  silver_publish: 'Silver 발행',
}

export function stageLabel(stage: string) {
  if (STAGE_LABELS[stage]) return STAGE_LABELS[stage]
  if (stage.startsWith('gold_')) return `모델 ${stage.slice(5)}`
  if (stage.startsWith('rds_')) return `RDS 반영 ${stage.slice(4)}`
  return stage
}

export function stageLayer(stage: string): 'bronze' | 'silver' | 'gold' | 'rds' {
  if (stage === 'bronze' || stage === 'photo') return 'bronze'
  if (stage.startsWith('silver')) return 'silver'
  if (stage.startsWith('gold')) return 'gold'
  return 'rds'
}

export const STATUS_LABELS: Record<string, string> = { ok: '정상', warn: '주의', failed: '실패', skipped: '건너뜀' }

/**
 * 원인 코드·단계의 설명은 DBML Enum(ops_reason_code, ops_stage)의 note 를 그대로 쓴다.
 * '설명 · 조치' 형식이면 둘로 나눈다. ERD 를 못 받으면 코드만 보인다.
 */
export function enumNotes(tables: ErdTable[] | undefined, table: string, column: string) {
  const col = tables?.find(t => t.table === table)?.columns.find(c => c.name === column)
  const out: Record<string, { label: string; action: string | null }> = {}
  for (const v of col?.values ?? []) {
    const note = v.note ?? ''
    const i = note.lastIndexOf(' · ')
    out[v.value] = i > 0 ? { label: note.slice(0, i), action: note.slice(i + 3) } : { label: note, action: null }
  }
  return out
}

export function shortHash(h: string | null | undefined) {
  return h ? h.slice(0, 10) : '—'
}
