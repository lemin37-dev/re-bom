'use client'

import { mascotProfiles, poseNames, type MascotTheme } from '@/data/mascots'
import { useMascot } from '@/providers/MascotProvider'

export function MascotArt({ pose = 'basic', mascot, className = '', decorative = true }: { pose?: string; mascot?: MascotTheme; className?: string; decorative?: boolean }) {
  const { theme } = useMascot()
  const selected = mascot || theme
  return (
    // 마스코트는 public/의 정적 webp를 그대로 사용합니다.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={`mascot-art ${className}`}
      src={`/mascots/${selected}-${pose}.webp`}
      alt={decorative ? '' : `${mascotProfiles[selected].name} ${poseNames[pose] || '모습'}`}
      draggable={false}
    />
  )
}
