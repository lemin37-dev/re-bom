import type { Metadata } from 'next'
import { ShelterDirectoryScreen } from '@/screens/shelter-directory/ShelterDirectoryScreen'

export const metadata: Metadata = { title: '보호센터 | RE:봄' }

export default function SheltersPage() {
  return <ShelterDirectoryScreen />
}
