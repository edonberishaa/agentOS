import type { Metadata } from 'next'
import type { ReactElement, ReactNode } from 'react'
import { Nav } from '@/components/Nav'
import './globals.css'

export const metadata: Metadata = {
  title: 'Agent OS',
  description: 'Mission control for AI coding agents',
}

export default function RootLayout({ children }: { children: ReactNode }): ReactElement {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#0f0f0f] text-gray-200">
        <Nav />
        <main className="px-6 py-6">{children}</main>
      </body>
    </html>
  )
}
