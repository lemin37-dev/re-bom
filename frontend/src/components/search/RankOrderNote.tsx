import type { PageDebug } from '@/types/rank'

/** dev 모드에서 목록 위에 보이는 정렬 순서 설명 (API 가 debug 를 보낼 때만) */
export function RankOrderNote({ debug }: { debug: PageDebug | null }) {
  if (!debug) return null
  return <div className="rank-order-note" aria-label="정렬 순서 (개발용)">
    <b>DEV · {debug.dataMode}</b>
    <ol>{debug.order.map(line => <li key={line}>{line}</li>)}</ol>
    {debug.vector && <span>질의 벡터: {debug.vector.version} · {debug.vector.model} · {debug.vector.dim}차원</span>}
  </div>
}
