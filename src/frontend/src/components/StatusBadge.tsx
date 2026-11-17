import type { ServerStatus } from '../types'

const LOOK: Record<ServerStatus, { dot: string; text: string; label: string }> = {
  online: { dot: 'bg-emerald-400', text: 'text-emerald-300', label: '정상' },
  connecting: { dot: 'bg-gray-500', text: 'text-gray-400', label: '연결 중' },
  error: { dot: 'bg-rose-500', text: 'text-rose-300', label: '연결 끊김' },
}

export function StatusBadge({ status, title }: { status: ServerStatus; title?: string }) {
  const look = LOOK[status]
  return (
    <span className={`flex items-center gap-1.5 text-xs ${look.text}`} title={title}>
      <span className={`h-2 w-2 rounded-full ${look.dot}`} />
      {look.label}
    </span>
  )
}
