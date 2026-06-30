import Link from 'next/link'
import type { ReactElement } from 'react'

const LINKS = [
  { href: '/', label: 'Mission Control' },
  { href: '/inbox', label: 'Inbox' },
  { href: '/timeline', label: 'Timeline' },
  { href: '/health', label: 'Health' },
]

export function Nav(): ReactElement {
  return (
    <nav className="flex items-center gap-6 border-b border-gray-800 px-6 py-4">
      <span className="font-bold text-indigo-400">Agent OS</span>
      {LINKS.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className="text-sm text-gray-300 hover:text-indigo-400"
        >
          {link.label}
        </Link>
      ))}
    </nav>
  )
}
