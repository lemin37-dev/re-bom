import type { Metadata, Viewport } from 'next'
import type { ReactNode } from 'react'
import { ExperienceShell } from '@/components/layout/ExperienceShell'
import { AppProviders } from '@/providers/AppProviders'
import '@/styles/globals.css'

export const metadata: Metadata = {
  title: 'RE:봄 — 새로운 인연을 만나다',
  description: '문장 · 사진 · 조건으로 전국 보호소의 유기동물과 나에게 맞는 인연을 찾아보세요.',
  icons: { icon: '/favicon.svg' },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <AppProviders>
          <ExperienceShell>{children}</ExperienceShell>
        </AppProviders>
      </body>
    </html>
  )
}
