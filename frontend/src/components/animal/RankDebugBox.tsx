import type { RankDebug } from '@/types/rank'

const TAG_RESULT = { match: '맞음', other: '다름', unknown: '정보 없음' } as const
const fmt = (n: number | null) => (n === null ? '없음' : n.toFixed(4))

/** dev 모드 순위 근거 (API 가 rank 를 보낼 때만). 카드 버튼 안에 들어가므로 버튼·링크 없이 글만 둡니다. */
export function RankDebugBox({ rank }: { rank?: RankDebug }) {
  if (!rank) return null
  const v = rank.vector
  return <span className="rank-debug" aria-label="순위 근거 (개발용)">
    <span className="rank-debug-head">
      <b>#{rank.position}</b>
      {rank.tagMatch && <span>태그 {rank.tagMatch}</span>}
      {v && <span>유사도 {fmt(v.similarity)} · 거리 {fmt(v.distance)}</span>}
    </span>
    {rank.filters.length > 0 && <span className="rank-debug-line"><i>필터</i>{rank.filters.map(f => `${f.name} ${f.condition} ← ${f.value ?? '값 없음'}`).join(' · ')}</span>}
    {rank.tags.length > 0 && <span className="rank-debug-line"><i>태그</i>{rank.tags.map(t => `${t.label}${t.exclusive ? '(반대 제외)' : ''} [${t.picked.join(',')}] ← [${t.have.join(',') || '-'}] ${TAG_RESULT[t.result]}`).join(' · ')}</span>}
    {v && <span className="rank-debug-line"><i>벡터</i>{v.kind} · {v.version} · {v.model}</span>}
    <span className="rank-debug-line"><i>정렬값</i>{rank.sortKey} {rank.sortValue ?? '없음'}</span>
  </span>
}
