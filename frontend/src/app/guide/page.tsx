import type { Metadata } from 'next'
import { AdoptionGuide } from '@/screens/adoption-guide/AdoptionGuide'

export const metadata: Metadata = { title: '입양 절차 안내 | RE:봄' }

export default function GuidePage() {
  return <AdoptionGuide />
}
