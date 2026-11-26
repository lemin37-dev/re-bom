'use client'

import { useEffect, useMemo, useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { KoreaMap } from '@/components/map/KoreaMap'
import { MascotState } from '@/components/mascot/MascotState'
import { ApiError, fetchShelters } from '@/lib/api'
import { DEFAULT_BASE, distanceKm, distanceLabel, groupByPlace, placeId, placeMarker, type Point, readMyLocation, SHELTER_COORDS_AT, shelterPoint } from '@/lib/shelter-map'
import type { ShelterSummary } from '@/types/animal'

const LIST_STEP = 8
const ALL = '전체 지역'
const ALL_DISTRICT = '시·군·구 전체'

/** 보호센터 화면 (웹·앱 공통 본문). 앱(compact)에서는 목록/지도 전환 버튼을 추가로 보여 줍니다. */
export function ShelterDirectory({ onFind, compact = false }: { onFind: (center: ShelterSummary) => void; compact?: boolean }) {
  const [shelters, setShelters] = useState<ShelterSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [mapView, setMapView] = useState(false)
  const [region, setRegion] = useState(ALL)
  const [district, setDistrict] = useState(ALL_DISTRICT)
  const [limit, setLimit] = useState(LIST_STEP)
  const [selected, setSelected] = useState<string | null>(null)
  const [me, setMe] = useState<(Point & { accuracy: number }) | null>(null)
  const [locating, setLocating] = useState<'idle' | 'busy' | 'denied'>('idle')
  const [focus, setFocus] = useState<{ lat: number; lng: number; zoom: number } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchShelters(controller.signal)
      .then(list => { setShelters(list); setError(null) })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : '보호센터를 불러오지 못했어요.') })
    return () => controller.abort()
  }, [retry])

  const regionOptions = useMemo(() => [...new Set((shelters ?? []).map(s => s.sido).filter((s): s is string => !!s))].sort(), [shelters])
  const districtOptions = useMemo(() => region === ALL ? [] : [...new Set((shelters ?? []).filter(s => s.sido === region).map(s => s.sigungu).filter((s): s is string => !!s))].sort(), [shelters, region])
  // 가까운 순: 내 위치, 받지 못했으면 서울 강남구 기준 (위치를 모르는 보호센터는 맨 뒤)
  const base = me ?? DEFAULT_BASE
  const filtered = useMemo(() => {
    const inRegion = (shelters ?? []).filter(s => (region === ALL || s.sido === region) && (district === ALL_DISTRICT || s.sigungu === district))
    const far = (regNo: string) => { const p = shelterPoint(regNo); return p ? distanceKm(base, p) : Infinity }
    return [...inRegion].sort((a, b) => far(a.regNo) - far(b.regNo))
  }, [shelters, region, district, base])
  const list = filtered.slice(0, limit)
  const current = filtered.find(s => s.regNo === selected) || list[0]
  const currentPoint = shelterPoint(current?.regNo)
  // 같은 건물에 시·군마다 따로 등록된 보호센터는 지도에서 점 하나로 묶음
  const { places } = useMemo(() => groupByPlace(filtered, s => s.regNo), [filtered])
  const markers = useMemo(() => places.map(p => {
    const n = p.items.reduce((sum, s) => sum + s.animalCount, 0)
    return placeMarker(p.id, n, `${p.items[0].name} · 보호 중 ${n}마리${p.precision === 'region' ? ' (대략 위치)' : ''}`)
  }), [places])
  const currentPlace = currentPoint ? places.find(p => p.id === placeId(currentPoint)) : undefined
  const away = (regNo: string) => { const p = shelterPoint(regNo); return p ? distanceLabel(distanceKm(base, p)) : null }
  const baseLabel = me ? '내 위치' : DEFAULT_BASE.label

  async function locate() {
    setLocating('busy')
    try {
      const here = await readMyLocation()
      setMe(here); setSelected(null); setLimit(LIST_STEP); setFocus({ lat: here.lat, lng: here.lng, zoom: 13 }); setLocating('idle')
    } catch { setLocating('denied') }
  }

  /** 고른 보호센터들의 가운데로 지도를 옮김 (없으면 전국) */
  function focusOn(list: ShelterSummary[], zoom: number) {
    const pts = list.map(s => shelterPoint(s.regNo)).filter(p => p !== null)
    setFocus(!pts.length ? null : { lat: pts.reduce((n, p) => n + p.lat, 0) / pts.length, lng: pts.reduce((n, p) => n + p.lng, 0) / pts.length, zoom })
  }

  /** 시·도를 고르면 시·군·구 목록이 그 시·도의 것으로 바뀜 */
  function pickRegion(next: string) {
    setRegion(next); setDistrict(ALL_DISTRICT); setLimit(LIST_STEP); setSelected(null)
    if (next === ALL) setFocus(null)
    else focusOn((shelters ?? []).filter(s => s.sido === next), /특별시|광역시/.test(next) ? 10.5 : 8.5)
  }

  function pickDistrict(next: string) {
    setDistrict(next); setLimit(LIST_STEP); setSelected(null)
    focusOn((shelters ?? []).filter(s => s.sido === region && (next === ALL_DISTRICT || s.sigungu === next)), next === ALL_DISTRICT ? (/특별시|광역시/.test(region) ? 10.5 : 8.5) : 11)
  }

  /** 보호센터를 골라 지도를 그 위치로 */
  function choose(regNo: string | null) {
    setSelected(regNo)
    const p = shelterPoint(regNo)
    if (p) setFocus({ lat: p.lat, lng: p.lng, zoom: 12 })
  }

  return <section className="directory-page" data-view={mapView ? 'map' : 'list'}>
    <div className="page-heading"><div><div className="eyebrow"><Icon name="pin" size={15} />새로운 인연이 시작되는 곳</div><h1>친구들이 머무는 보호센터</h1><p>보호센터를 찾고, 그곳에서 기다리는 아이들을 만나 보세요.</p></div></div>
    {error
      ? <MascotState kind="error" detail={error} serverHint={error.includes('연결')}><button className="primary-button" onClick={() => setRetry(n => n + 1)}>다시 불러오기</button></MascotState>
      : !shelters
        ? <MascotState kind="loading" />
        : <>
          <div className="directory-filters">
            <label>지역<select value={region} onChange={e => pickRegion(e.target.value)} aria-label="시·도"><option>{ALL}</option>{regionOptions.map(r => <option key={r}>{r}</option>)}</select>
              <select value={district} onChange={e => pickDistrict(e.target.value)} disabled={region === ALL} aria-label="시·군·구"><option>{ALL_DISTRICT}</option>{districtOptions.map(d => <option key={d}>{d}</option>)}</select></label>
            <span className="directory-count">보호센터 <strong>{filtered.length}</strong>곳 · {baseLabel}에서 가까운 순{me ? ` (오차 약 ${distanceLabel(me.accuracy / 1000)})` : ''}</span>
            <button type="button" className="locate-button" onClick={locate} disabled={locating === 'busy'}><Icon name="pin" size={15} />{locating === 'busy' ? '위치를 찾는 중…' : me ? '내 위치 다시 찾기' : '내 위치에서 가까운 순'}</button>
          </div>
          {compact && <div className="mobile-directory-view" role="group" aria-label="보호센터 보기 방식"><button className={!mapView ? 'selected' : ''} aria-pressed={!mapView} onClick={() => setMapView(false)}><Icon name="grid" size={17} />목록으로</button><button className={mapView ? 'selected' : ''} aria-pressed={mapView} onClick={() => setMapView(true)}><Icon name="pin" size={17} />지도로</button></div>}
          <div className="directory-layout">
            <div className="directory-list">
              {list.map(s => <article key={s.regNo} className={`directory-card ${current?.regNo === s.regNo ? 'selected-center' : ''}`}>
                <div className="directory-card-top"><span>{s.sido} {s.sigungu}</span>{away(s.regNo) && <span className="distance-badge">{away(s.regNo)}</span>}</div>
                <button className="center-name" onClick={() => choose(s.regNo)} aria-pressed={current?.regNo === s.regNo}>{s.name}<Icon name="pin" size={17} /></button>
                {s.tel && <p>전화 <a href={`tel:${s.tel}`}>{s.tel}</a></p>}
                <div className="directory-card-bottom"><span>보호 중인 친구 <b>{s.animalCount}마리</b></span><button onClick={() => onFind(s)} disabled={!s.animalCount}>이 센터 아이들 보기<Icon name="arrow" size={15} /></button></div>
              </article>)}
              {filtered.length > limit && <button className="load-more" onClick={() => setLimit(n => n + LIST_STEP)}>보호센터 더 보기 <span>{list.length} / {filtered.length}</span><Icon name="plus" size={15} /></button>}
            </div>
            <aside className="directory-map">
              <KoreaMap label="보호센터 지도" markers={markers} selectedId={selected && currentPlace ? currentPlace.id : null} onSelect={id => choose(places.find(p => p.id === id)?.items[0].regNo ?? null)} me={me} focus={focus} height={compact ? 350 : 535} />
              {locating === 'denied' && <p className="locate-status">위치를 가져오지 못했어요. 브라우저의 위치 권한을 확인해 주세요. 지금은 {DEFAULT_BASE.label} 기준이에요.</p>}
              {current && <div className="selected-center-info"><span className="soft-icon"><Icon name="pin" size={22} /></span><div><strong>{current.name}</strong><p>{current.addr}{away(current.regNo) && ` · ${away(current.regNo)}`}</p><small>{currentPoint?.precision === 'region' ? '지도 위치는 대략이에요 · ' : !currentPoint ? '지도에 위치가 없어요 · ' : ''}운영 시간은 보호소에 확인해 주세요</small></div><button className="icon-button" onClick={() => onFind(current)} disabled={!current.animalCount} aria-label={`${current.name}의 아이들 보기`}><Icon name="arrow" /></button></div>}
              {currentPlace && currentPlace.items.length > 1 && <div className="same-place"><span>같은 곳에 등록된 보호센터 {currentPlace.items.length}곳 · 시·군마다 따로 등록돼요</span>
                {currentPlace.items.map(s => <button key={s.regNo} onClick={() => onFind(s)} disabled={!s.animalCount}>{s.name}<small>{s.animalCount}마리</small><Icon name="arrow" size={13} /></button>)}
              </div>}
              <p className="reference-note">센터명·전화·동물 수는 검색 API, 위치는 보호센터 주소를 카카오 지도로 찾은 값이에요({SHELTER_COORDS_AT} 기준). 내 위치는 화면에서만 쓰고 저장하지 않아요.</p>
            </aside>
          </div>
        </>}
  </section>
}
