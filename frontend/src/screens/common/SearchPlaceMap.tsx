'use client'

import { useEffect, useMemo, useState } from 'react'
import { AnimalPhoto } from '@/components/animal/AnimalPhoto'
import { Icon } from '@/components/icons/Icon'
import { KoreaMap } from '@/components/map/KoreaMap'
import { animalTitle } from '@/lib/animal'
import { ApiError, fetchAnimals, fetchPlaces, fetchShelters, type PlaceCount } from '@/lib/api'
import { DEFAULT_BASE, distanceKm, distanceLabel, groupByPlace, type Place, placeMarker, type Point, readMyLocation } from '@/lib/shelter-map'
import { useSearch } from '@/providers/SearchProvider'
import type { Animal, ShelterSummary } from '@/types/animal'

type Focus = { lat: number; lng: number; zoom: number } | null
type Me = Point & { accuracy: number }
/** 한 장소에서 불러온 아이들 (key: 장소 id + 검색 조건) */
type Picked = { key: string; animals: Animal[]; total: number; error: string | null }
const NEAR_ZOOM = 13

// 보호센터 이름·주소는 한 번만 받는다 (지도 점에는 번호와 마릿수만 오므로)
let shelterList: Promise<Map<string, ShelterSummary>> | null = null
function shelterInfo() {
  shelterList ??= fetchShelters().then(list => new Map(list.map(s => [s.regNo, s]))).catch(e => { shelterList = null; throw e })
  return shelterList
}

/**
 * 검색 결과 지도 보기 (웹·앱 공통). 목록과 같은 조건의 결과 전체를 보호센터 위치별 점(마릿수)으로 보여 줍니다.
 * 점을 누르면 그 보호센터의 아이들을 목록과 같은 순서(문장·사진과 닮은 순)로 불러옵니다.
 * 가까운 순의 기준은 내 위치, 받지 못했으면 서울 강남구. 내 위치는 화면에서만 쓰고 저장·전송하지 않습니다.
 */
export function SearchPlaceMap({ onOpen, compact = false }: { onOpen: (a: Animal) => void; compact?: boolean }) {
  const { listParams, listPhoto, searched, centerFilter } = useSearch()
  const active = searched || !!centerFilter
  const paramsKey = JSON.stringify(listParams)
  const [counts, setCounts] = useState<{ key: string; items: PlaceCount[]; error: string | null } | null>(null)
  const [names, setNames] = useState<Map<string, ShelterSummary>>(new Map())
  const [me, setMe] = useState<Me | null>(null)
  const [locating, setLocating] = useState<'idle' | 'busy' | 'denied'>('idle')
  const [focus, setFocus] = useState<Focus>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [picked, setPicked] = useState<Picked | null>(null)

  useEffect(() => { shelterInfo().then(setNames).catch(() => {}) }, [])

  // 검색 조건이 바뀌면 보호센터별 마릿수를 다시 받는다
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    fetchPlaces(listParams, controller.signal)
      .then(res => setCounts({ key: paramsKey, items: res.items, error: null }))
      .catch(e => { if (!controller.signal.aborted) setCounts({ key: paramsKey, items: [], error: e instanceof ApiError ? e.message : '지도를 불러오지 못했어요.' }) })
    return () => controller.abort()
  }, [active, listParams, paramsKey])

  const loading = active && counts?.key !== paramsKey
  const items = useMemo(() => (active && counts?.key === paramsKey ? counts.items : []), [active, counts, paramsKey])
  const { places, unplaced } = useMemo(() => groupByPlace(items, p => p.regNo), [items])
  const total = items.reduce((n, p) => n + p.count, 0)
  const lost = unplaced.reduce((n, p) => n + p.count, 0)
  const base = me ?? DEFAULT_BASE
  const ordered = useMemo(() => [...places].sort((a, b) => distanceKm(base, a) - distanceKm(base, b)), [places, base])
  const selected = places.find(p => p.id === selectedId) ?? null
  const countOf = (p: Place<PlaceCount>) => p.items.reduce((n, i) => n + i.count, 0)
  const nameOf = (p: Place<PlaceCount>) => {
    const byCount = [...p.items].sort((a, b) => b.count - a.count)
    return names.get(byCount[0].regNo)?.name ?? '보호센터'
  }
  const infoOf = (p: Place<PlaceCount>) => names.get(p.items[0].regNo)
  const markers = useMemo(() => places.map(p => placeMarker(p.id, countOf(p), `${nameOf(p)} · ${countOf(p)}마리${p.precision === 'region' ? ' (대략 위치)' : ''}`, 'forest')), [places, names]) // eslint-disable-line react-hooks/exhaustive-deps

  // 고른 장소의 아이들: 같은 건물에 등록된 보호센터 번호를 한 번에, 목록과 같은 조건·순서로
  const pickKey = selected ? `${selected.id}#${paramsKey}#${listPhoto ? 'photo' : ''}` : ''
  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    const shelter = selected.items.map(i => i.regNo).join(',')
    fetchAnimals({ ...listParams, shelter, page: 1, size: 100 }, controller.signal, listPhoto)
      .then(res => setPicked({ key: pickKey, animals: res.items, total: res.total, error: null }))
      .catch(e => { if (!controller.signal.aborted) setPicked({ key: pickKey, animals: [], total: 0, error: e instanceof ApiError ? e.message : '아이들을 불러오지 못했어요.' }) })
    return () => controller.abort()
  }, [pickKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const shown = picked?.key === pickKey ? picked : null

  function choose(id: string | null) {
    setSelectedId(id)
    const p = places.find(x => x.id === id)
    if (p) setFocus({ lat: p.lat, lng: p.lng, zoom: 12 })
  }

  async function locate() {
    setLocating('busy')
    try {
      const here = await readMyLocation()
      setMe(here); setSelectedId(null); setFocus({ lat: here.lat, lng: here.lng, zoom: NEAR_ZOOM }); setLocating('idle')
    } catch { setLocating('denied') }
  }

  const baseLabel = me ? '내 위치' : `${DEFAULT_BASE.label}`
  return <div className={`place-map-wrap${compact ? ' compact' : ''}`}>
    <div className="place-map-tools">
      <button type="button" className="locate-button" onClick={locate} disabled={locating === 'busy'}><Icon name="pin" size={15} />{locating === 'busy' ? '위치를 찾는 중…' : me ? '내 위치 다시 찾기' : '내 위치에서 가까운 순'}</button>
      <span>{active ? <>검색 결과 <b>{loading ? '…' : total}</b>마리 · {baseLabel}에서 가까운 보호센터 순{me ? ` (오차 약 ${distanceLabel(me.accuracy / 1000)})` : ''}</> : '검색하면 조건에 맞는 친구들을 지도에서 보여 드려요'}</span>
    </div>
    {locating === 'denied' && <p className="locate-status">위치를 가져오지 못했어요. 브라우저의 위치 권한을 확인해 주세요. 지금은 {DEFAULT_BASE.label} 기준이에요.</p>}
    <div className="place-map-layout">
      <KoreaMap label="검색 결과 보호센터 지도" markers={markers} selectedId={selectedId} onSelect={choose} me={me} focus={focus} height={compact ? 320 : 520} />
      <aside className="place-map-aside">
        {!active
          ? <><h3>검색 결과 0마리</h3><p>문장·사진·생활 조건으로 먼저 찾아 주세요. 찾은 친구들이 있는 보호센터를 지도에 보여 드려요.</p></>
          : counts?.error && !loading
            ? <><h3>지도를 불러오지 못했어요</h3><p>{counts.error}</p></>
            : selected
              ? <>
                <button type="button" className="back-link" onClick={() => setSelectedId(null)}><Icon name="arrow" size={14} />보호센터 목록</button>
                <h3>{nameOf(selected)}</h3>
                <p>{infoOf(selected)?.addr}{` · ${baseLabel}에서 ${distanceLabel(distanceKm(base, selected))}`}{selected.precision === 'region' && ' · 지도 위치는 대략이에요'}</p>
                {!shown
                  ? <p className="place-map-note">불러오는 중…</p>
                  : shown.error
                    ? <p className="place-map-note">{shown.error}</p>
                    : <>
                      {shown.animals.map(a => <button className="map-list-item" key={a.id} onClick={() => onOpen(a)}><div style={{ background: a.bg }}><AnimalPhoto animal={a} small /></div><span><strong>{animalTitle(a)}</strong><small>{a.weightLabel} · {a.color}</small></span><Icon name="arrow" size={16} /></button>)}
                      {shown.total > shown.animals.length && <p className="place-map-note">앞의 {shown.animals.length}마리만 보여요 (전체 {shown.total}마리)</p>}
                    </>}
              </>
              : <>
                <h3>이 친구들이 기다려요</h3>
                <p>{baseLabel}에서 가까운 보호센터 순이에요. 점이나 보호센터를 누르면 친구들을 볼 수 있어요. 방문 전 보호센터에 꼭 확인해 주세요.</p>
                {loading && <p className="place-map-note">불러오는 중…</p>}
                {!loading && !places.length && <p className="place-map-note">조건에 맞는 친구가 없어요.</p>}
                {ordered.map(p => <button className="place-row" key={p.id} onClick={() => choose(p.id)}><span><strong>{nameOf(p)}</strong><small>{infoOf(p)?.sido} {infoOf(p)?.sigungu} · {distanceLabel(distanceKm(base, p))}</small></span><b>{countOf(p)}마리</b><Icon name="arrow" size={15} /></button>)}
                {lost > 0 && <p className="place-map-note">위치를 모르는 보호센터의 {lost}마리는 목록으로 보기에서 볼 수 있어요.</p>}
              </>}
      </aside>
    </div>
  </div>
}
