'use client'

import type { ReactNode } from 'react'
import { mascotProfiles, type MascotTheme } from '@/data/mascots'
import { API_BASE_URL } from '@/lib/api'
import { useMascot } from '@/providers/MascotProvider'
import { MascotArt } from './MascotArt'

/**
 * 마스코트 상태 화면 종류 (마스코트 시트의 '로딩 화면 · 빈 페이지 안내 · 검색 결과 없음' 구성을 따름)
 * - loading: 목록·상세를 불러오는 중
 * - no-results: 검색 조건에 맞는 공고가 없음
 * - empty: 보여 줄 공고 자체가 없음 (조건 없이 비었을 때)
 * - error: 검색 서버 연결 실패 등
 * - not-found: 없는 공고·없는 화면
 */
export type MascotStateKind = 'loading' | 'no-results' | 'empty' | 'error' | 'not-found'

/** 테마별 포즈. 봄냥이 시트의 로딩=뛰기, 빈 페이지=빼꼼(waiting), 결과 없음=말풍선(empty) */
const POSES: Record<MascotStateKind, Record<MascotTheme, string>> = {
  loading: { bomnyang: 'run', remeong: 'run' },
  'no-results': { bomnyang: 'empty', remeong: 'lie' },
  empty: { bomnyang: 'waiting', remeong: 'rest' },
  error: { bomnyang: 'curious', remeong: 'worried' },
  'not-found': { bomnyang: 'sit', remeong: 'lookback' },
}

function copy(kind: MascotStateKind, name: string): { title: string; lines: string[] } {
  switch (kind) {
    case 'loading':
      return { title: '다시, 봄이 올 수 있도록', lines: [`${name}가 기다리는 친구들을 찾고 있어요.`] }
    case 'no-results':
      return { title: '일치하는 동물을 찾지 못했어요.', lines: ['다른 키워드로 검색해보거나', '조금 더 넓은 조건으로 찾아보세요.', '어쩌면, 봄이 곧 찾아올지도 몰라요.'] }
    case 'empty':
      return { title: '아직 등록된 동물이 없어요.', lines: ['조금만 기다려주세요.', '더 많은 동물 친구들이', '봄을 기다리고 있어요.'] }
    case 'error':
      return { title: '친구들 소식을 불러오지 못했어요.', lines: ['잠시 길이 막힌 것 같아요.', '조금 뒤에 다시 불러와 주세요.'] }
    case 'not-found':
      return { title: '찾는 친구가 여기 없어요.', lines: ['이미 새 가족을 만났거나,', '공고가 마무리되었을 수 있어요.'] }
  }
}

type Props = {
  kind: MascotStateKind
  /** 오류 원인 등 덧붙일 한 줄 (작게 표시) */
  detail?: string | null
  /** 서버 연결 실패일 때 실행 방법 안내를 함께 보여 줌 */
  serverHint?: boolean
  /** 버튼 등 */
  children?: ReactNode
}

/** 로딩·결과 없음·빈 목록·오류·없는 공고 안내 */
export function MascotState({ kind, detail, serverHint = false, children }: Props) {
  const { theme } = useMascot()
  const { title, lines } = copy(kind, mascotProfiles[theme].name)
  const loading = kind === 'loading'
  return (
    <div className={`mascot-state mascot-state-${kind} ${loading ? 'is-loading' : ''}`} role={loading ? 'status' : kind === 'error' ? 'alert' : undefined}>
      <MascotArt pose={POSES[kind][theme]} />
      <strong>{title}</strong>
      <p>{lines.map((line, i) => <span key={i}>{line}{i < lines.length - 1 && <br />}</span>)}</p>
      {loading && <span className="mascot-progress" aria-hidden="true"><i /></span>}
      {(detail || serverHint) && <small>{detail}{detail && serverHint && <br />}{serverHint && `검색 서버(${API_BASE_URL})가 켜져 있는지 확인해 주세요. 레포 루트의 scripts/serve/run_api 로 켤 수 있어요.`}</small>}
      {!loading && children}
    </div>
  )
}
