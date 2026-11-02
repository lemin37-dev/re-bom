import type { Animal } from '@/types/animal'

/** 'YYYYMMDD' → Date (현지 자정) */
export function parseYmd(ymd: string | null | undefined) {
  if (!ymd || !/^\d{8}$/.test(ymd)) return null
  return new Date(Number(ymd.slice(0, 4)), Number(ymd.slice(4, 6)) - 1, Number(ymd.slice(6, 8)))
}

function startOfToday() {
  const d = new Date()
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

const DAY = 24 * 60 * 60 * 1000

/** 공고 종료일까지 남은 날 (오늘이 종료일이면 0, 지났으면 음수) */
export function daysUntilNoticeEnd(a: Animal) {
  const end = parseYmd(a.noticeEnd)
  return end ? Math.round((end.getTime() - startOfToday().getTime()) / DAY) : null
}

/** 보호 중이고 공고 기간이 끝나 입양 상담을 할 수 있는 상태 */
export function isAvailable(a: Animal) {
  const left = daysUntilNoticeEnd(a)
  return a.protectedNow && (left === null || left < 0)
}

/** 'MM.DD' */
export function shortDate(ymd: string | null | undefined) {
  const d = parseYmd(ymd)
  return d ? `${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}` : '-'
}

export function noticePeriod(a: Animal) {
  return `${shortDate(a.noticeStart)} ~ ${shortDate(a.noticeEnd)}`
}

export function statusLabel(a: Animal) {
  if (!a.protectedNow) return a.state || '공고 종료'
  if (isAvailable(a)) return '입양 가능'
  return `공고 중 D-${daysUntilNoticeEnd(a)}`
}

export function availabilityLabel(a: Animal) {
  if (!a.protectedNow) return '보호가 끝난 공고예요'
  if (isAvailable(a)) return '지금 입양 상담 가능'
  const end = parseYmd(a.noticeEnd)!
  const next = new Date(end.getTime() + DAY)
  return `${next.getMonth() + 1}월 ${next.getDate()}일부터 입양 가능`
}

/** 구조일 → 공고 종료까지 중 오늘의 위치 (0~100) */
export function noticeProgress(a: Animal) {
  const start = parseYmd(a.happenDate) ?? parseYmd(a.noticeStart)
  const end = parseYmd(a.noticeEnd)
  if (!start || !end || end <= start) return 100
  const ratio = (startOfToday().getTime() - start.getTime()) / (end.getTime() - start.getTime())
  return Math.max(0, Math.min(100, Math.round(ratio * 100)))
}

/** 공고 문장에서 성격·생활 태그 추출 (태깅 모델이 붙기 전 단순 단어 규칙) */
export function behaviorTags(a: Animal) {
  const text = `${a.quote} ${a.sociality ?? ''}`
  const rules: [RegExp, string][] = [
    [/사람.*(따르|좋아)|친화|애교/, '사람을 잘 따름'],
    [/온순|순함|순하/, '온순함'],
    [/얌전|차분/, '차분함'],
    [/활발|에너지/, '활발함'],
    [/산책/, '산책 좋아함'],
    [/짖음.*(적|없)/, '짖음 적음'],
    [/배변/, '배변 관련 기록'],
    [/잘 지냄|합사/, '다른 동물과 잘 지냄'],
    [/겁|경계|예민|입질/, '낯선 환경에 조심스러움'],
  ]
  return rules.filter(([re]) => re.test(text)).map(([, tag]) => tag)
}

/** 체중으로 나눈 크기 */
export function sizeLabel(a: Animal) {
  if (a.kg === null) return null
  return a.kg < 8 ? '소형' : a.kg < 15 ? '중형' : '대형'
}

export function animalTitle(a: Animal) {
  return `${a.kind} · ${a.gender} · ${a.ageLabel}`
}

export function animalMeta(a: Animal) {
  return `${a.weightLabel} · ${a.color} · ${a.care}`
}
