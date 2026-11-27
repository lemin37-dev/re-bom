import type { Metadata } from 'next'
import { AdoptionSupportScreen } from '@/screens/adoption-guide/AdoptionGuide'

export const metadata: Metadata = { title: '우리 지역 입양 혜택 | RE:봄' }

export default function GuideSupportPage() {
  return <AdoptionSupportScreen />
}
