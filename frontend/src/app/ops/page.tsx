import type { Metadata } from 'next'
import { OpsScreen } from '@/screens/ops/OpsScreen'

export const metadata: Metadata = { title: '운영 모니터링 | RE:봄', robots: { index: false, follow: false } }

/** 운영 모니터링 (/ops). 서비스 화면 틀(헤더·탭·푸터) 없이 그린다 (ExperienceShell 참고). */
export default function OpsRoute() {
  return <OpsScreen />
}
