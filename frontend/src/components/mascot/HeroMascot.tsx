'use client'

import { useEffect, useState } from 'react'
import { mascotProfiles } from '@/data/mascots'
import { useMascot } from '@/providers/MascotProvider'
import { MascotArt } from './MascotArt'

function randomPose(poses: readonly string[], except?: string) {
  const choices = poses.filter(item => item !== except)
  return choices[Math.floor(Math.random() * choices.length)]
}

/** 첫 화면 마스코트. 탭으로 돌아올 때마다 다른 포즈를 보여 줍니다. */
export function HeroMascot({ className = '' }: { className?: string }) {
  const { theme } = useMascot()
  const poses = mascotProfiles[theme].poses
  // 서버와 첫 렌더 결과를 맞추기 위해 기본 포즈로 시작하고, 마운트 후 무작위 포즈로 바꿉니다.
  const [pose, setPose] = useState<string>(poses[0])

  useEffect(() => {
    const frame = requestAnimationFrame(() => setPose(randomPose(poses)))
    function onVisibilityChange() {
      if (document.visibilityState !== 'visible') return
      setPose(previous => randomPose(poses, previous))
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [poses])

  return <MascotArt pose={pose} className={className} />
}
