import type { Metadata } from 'next'
import { AdoptionEventsScreen } from '@/screens/adoption-guide/AdoptionGuide'

export const metadata: Metadata = { title: '전국 입양 행사 | RE:봄' }

export default function GuideEventsPage() {
  return <AdoptionEventsScreen />
}
