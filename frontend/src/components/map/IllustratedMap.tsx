import type { ReactNode } from 'react'

/** 보호센터 위치 설명용 예시 지도 배경. 핀과 안내 문구는 children으로 넣습니다. */
export function IllustratedMap({ children }: { children: ReactNode }) {
  return <div className="illustrated-map"><div className="map-river" /><span className="map-label label-seoul">서울</span><span className="map-label label-gyeonggi">경기도</span>{children}</div>
}
