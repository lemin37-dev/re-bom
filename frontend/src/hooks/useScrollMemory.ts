'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from 'react'
import type { ViewMode } from '@/types/view-mode'

/**
 * 모드·경로별 스크롤 위치를 기억합니다.
 * - 웹 모드는 window, 앱 모드는 모바일 프레임 안의 스크롤 영역을 사용합니다.
 * - 경로나 모드가 바뀌면 `nextScroll`로 예약한 위치(없으면 기억해 둔 위치)로 이동합니다.
 */
export function useScrollMemory(mode: ViewMode, pathname: string, containerRef: RefObject<HTMLElement | null>) {
  const positions = useRef<Record<string, number>>({})
  const keyRef = useRef('')
  const pending = useRef<number | null>(null)

  const getScroll = useCallback(() => (mode === 'app' ? containerRef.current?.scrollTop || 0 : window.scrollY), [mode, containerRef])

  useEffect(() => {
    const target: Window | HTMLElement | null = mode === 'app' ? containerRef.current : window
    if (!target) return
    const onScroll = () => { positions.current[keyRef.current] = getScroll() }
    target.addEventListener('scroll', onScroll, { passive: true })
    return () => target.removeEventListener('scroll', onScroll)
  }, [mode, containerRef, getScroll])

  const scrollTo = useCallback((top: number) => {
    if (mode === 'app') containerRef.current?.scrollTo({ top, behavior: 'instant' })
    else window.scrollTo({ top, behavior: 'instant' })
  }, [mode, containerRef])

  useLayoutEffect(() => {
    const key = `${mode}:${pathname}`
    // 레이아웃 변경으로 인한 scroll 이벤트가 값을 덮어쓰기 전에 목표 위치를 먼저 읽어 둡니다.
    const top = pending.current ?? positions.current[key] ?? 0
    pending.current = null
    keyRef.current = key
    const frame = requestAnimationFrame(() => {
      if (mode === 'app') window.scrollTo({ top: 0 })
      scrollTo(top)
    })
    return () => cancelAnimationFrame(frame)
  }, [mode, pathname, scrollTo])

  /** 다음 화면 전환 후 이동할 위치 예약. null이면 해당 화면에서 기억해 둔 위치로 복원합니다. */
  const nextScroll = useCallback((top: number | null) => { pending.current = top }, [])

  return { nextScroll, scrollTo }
}
