'use client'

import { AnimalPhoto } from '@/components/animal/AnimalPhoto'
import { Icon } from '@/components/icons/Icon'
import { IllustratedMap } from '@/components/map/IllustratedMap'
import type { Animal } from '@/types/animal'

/** 웹 검색 결과 지도 보기: 예시 지도 + 목록 앞쪽 아이들 (위치 정보가 붙기 전까지 거리순 아님) */
export function ResultMap({ animals, onOpen }: { animals: Animal[]; onOpen: (a: Animal) => void }) {
  return <div className="map-layout">
    <IllustratedMap>
      {animals.slice(0, 8).map((a, i) => <button key={a.id} className={`map-pin pin-${i}`} onClick={() => onOpen(a)}><Icon name="pin" size={16} />{a.shelter.sigungu || a.center.split(' ')[0]}{a.score !== undefined && <b>{a.score}%</b>}</button>)}
      <div className="map-note">보호센터 위치를 설명하기 위한 예시 지도예요. 핀 위치는 실제와 달라요.</div>
    </IllustratedMap>
    <aside className="map-aside"><h3>이 친구들이 기다려요</h3><p>방문 전 보호센터에 꼭 확인해 주세요.</p>
      {animals.slice(0, 4).map(a => <button className="map-list-item" key={a.id} onClick={() => onOpen(a)}><div style={{ background: a.bg }}><AnimalPhoto animal={a} small /></div><span><strong>{a.kind} · {a.ageLabel}</strong><small>{a.center}<br />{a.shelter.addr}</small></span><Icon name="arrow" size={16} /></button>)}
    </aside>
  </div>
}
