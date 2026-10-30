'use client'

import type { ReactNode } from 'react'
import { mascotProfiles } from '@/data/mascots'
import { useMascot } from '@/providers/MascotProvider'
import { MascotArt } from './MascotArt'

/** 검색 중(loading) 또는 결과 없음 상태 안내 */
export function MascotState({ loading = false, children }: { loading?: boolean; children?: ReactNode }) {
  const { theme } = useMascot()
  return (
    <div className={`mascot-state ${loading ? 'is-loading' : ''}`} role={loading ? 'status' : undefined}>
      <MascotArt pose={loading ? 'walk' : theme === 'bomnyang' ? 'empty' : 'curious'} />
      <strong>{loading ? `${mascotProfiles[theme].name}와 친구들을 만나러 가는 중이에요` : '아직 꼭 맞는 친구를 찾지 못했어요'}</strong>
      <p>{loading ? '선택한 조건으로 예시 공고를 살펴보고 있어요. 잠시만 기다려 주세요.' : '조건을 조금 넓혀 볼까요? 새로운 인연을 천천히 함께 찾아봐요.'}</p>
      {loading ? (
        <>
          <span className="mascot-progress" aria-hidden="true"><i /></span>
          <small>문장·사진의 실제 AI 분석은 연결되어 있지 않아요.</small>
        </>
      ) : children}
    </div>
  )
}
