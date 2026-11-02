'use client'

import { useEffect, useMemo, useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { IllustratedMap } from '@/components/map/IllustratedMap'
import { MascotState } from '@/components/mascot/MascotState'
import { ApiError, fetchShelters } from '@/lib/api'
import type { ShelterSummary } from '@/types/animal'

const LIST_STEP = 8

/** 보호센터 화면 (웹·앱 공통 본문). 앱(compact)에서는 목록/지도 전환 버튼을 추가로 보여 줍니다. */
export function ShelterDirectory({ onFind, compact = false }: { onFind: (center: ShelterSummary) => void; compact?: boolean }) {
  const [shelters, setShelters] = useState<ShelterSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [mapView, setMapView] = useState(false)
  const [region, setRegion] = useState('전체 지역')
  const [limit, setLimit] = useState(LIST_STEP)
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchShelters(controller.signal)
      .then(list => { setShelters(list); setError(null) })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : '보호센터를 불러오지 못했어요.') })
    return () => controller.abort()
  }, [retry])

  const regionOptions = useMemo(() => [...new Set((shelters ?? []).map(s => s.sido).filter((s): s is string => !!s))].sort(), [shelters])
  const filtered = (shelters ?? []).filter(s => region === '전체 지역' || s.sido === region)
  const list = filtered.slice(0, limit)
  const current = list.find(s => s.regNo === selected) || list[0]

  return <section className="directory-page" data-view={mapView ? 'map' : 'list'}>
    <div className="page-heading"><div><div className="eyebrow"><Icon name="pin" size={15} />새로운 인연이 시작되는 곳</div><h1>친구들이 머무는 보호센터</h1><p>보호센터를 찾고, 그곳에서 기다리는 아이들을 만나 보세요.</p></div></div>
    {error
      ? <MascotState kind="error" detail={error} serverHint={error.includes('연결')}><button className="primary-button" onClick={() => setRetry(n => n + 1)}>다시 불러오기</button></MascotState>
      : !shelters
        ? <MascotState kind="loading" />
        : <>
          <div className="directory-filters">
            <label>지역<select value={region} onChange={e => { setRegion(e.target.value); setLimit(LIST_STEP) }}><option>전체 지역</option>{regionOptions.map(r => <option key={r}>{r}</option>)}</select></label>
            <span>보호센터 <strong>{filtered.length}</strong>곳</span>
          </div>
          {compact && <div className="mobile-directory-view" role="group" aria-label="보호센터 보기 방식"><button className={!mapView ? 'selected' : ''} aria-pressed={!mapView} onClick={() => setMapView(false)}><Icon name="grid" size={17} />목록으로</button><button className={mapView ? 'selected' : ''} aria-pressed={mapView} onClick={() => setMapView(true)}><Icon name="pin" size={17} />지도로</button></div>}
          <div className="directory-layout">
            <div className="directory-list">
              {list.map(s => <article key={s.regNo} className={`directory-card ${current?.regNo === s.regNo ? 'selected-center' : ''}`}>
                <div className="directory-card-top"><span>{s.sido} {s.sigungu}</span></div>
                <button className="center-name" onClick={() => setSelected(s.regNo)} aria-pressed={current?.regNo === s.regNo}>{s.name}<Icon name="pin" size={17} /></button>
                {s.tel && <p>전화 <a href={`tel:${s.tel}`}>{s.tel}</a></p>}
                <div className="directory-card-bottom"><span>보호 중인 친구 <b>{s.animalCount}마리</b></span><button onClick={() => onFind(s)} disabled={!s.animalCount}>이 센터 아이들 보기<Icon name="arrow" size={15} /></button></div>
              </article>)}
              {filtered.length > limit && <button className="load-more" onClick={() => setLimit(n => n + LIST_STEP)}>보호센터 더 보기 <span>{list.length} / {filtered.length}</span><Icon name="plus" size={15} /></button>}
            </div>
            <aside className="directory-map">
              <IllustratedMap>
                {list.slice(0, 8).map((s, i) => <button key={s.regNo} className={`map-pin pin-${i} ${current?.regNo === s.regNo ? 'active-pin' : ''}`} onClick={() => setSelected(s.regNo)} aria-pressed={current?.regNo === s.regNo}><Icon name="pin" size={16} />{s.sigungu || s.name.split(' ')[0]}</button>)}
                <span className="map-placeholder-label">지도 자리표시</span>
              </IllustratedMap>
              {current && <div className="selected-center-info"><span className="soft-icon"><Icon name="pin" size={22} /></span><div><strong>{current.name}</strong><p>{current.addr}</p><small>운영 시간은 보호소에 확인해 주세요</small></div><button className="icon-button" onClick={() => onFind(current)} disabled={!current.animalCount} aria-label={`${current.name}의 아이들 보기`}><Icon name="arrow" /></button></div>}
              <p className="reference-note">센터명·주소·전화·동물 수는 구조동물 공고 샘플(2026-10-30)에서 모았어요. 지도는 예시이고 주소는 시·군·구까지만 보여요.</p>
            </aside>
          </div>
        </>}
  </section>
}
