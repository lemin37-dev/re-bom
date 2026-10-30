import type { Metadata } from 'next'
import { AboutScreen } from '@/screens/about/AboutScreen'

export const metadata: Metadata = { title: '서비스 소개 | RE:봄' }

export default function AboutRoute() {
  return <AboutScreen />
}
