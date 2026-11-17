/** 여러 칸이 같이 쓰는 모양. */

export const BUTTON =
  'min-w-16 cursor-pointer rounded-[5px] border border-edge bg-btn px-2.5 py-[3px] text-xs text-gray-100 ' +
  'hover:bg-btn-hover disabled:cursor-default disabled:text-gray-500 disabled:hover:bg-btn'

/** 안내 상자 (거절 이유 · 진행 상황) */
export const NOTICE = 'whitespace-pre-line rounded-md bg-btn px-2.5 py-2 text-xs text-gray-100'

/** 경고 상자 (형식 문제 · 연결 끊김) */
export const WARNING = 'whitespace-pre-line rounded-md bg-[#3a2621] px-2.5 py-2 text-xs text-[#f0b8a4]'

export const INPUT =
  'w-full rounded-[5px] border border-edge bg-ground px-2 py-1 text-sm text-gray-100 ' +
  'placeholder:text-gray-500 focus:border-accent focus:outline-none'

/** "3분 전" — ISO 시각이 얼마나 지났는지 */
export function agoIso(iso: string | null | undefined, now: number): string {
  if (!iso) return ''
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (Number.isNaN(seconds)) return ''
  if (seconds < 60) return '방금'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}분 전`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}시간 전`
  return `${Math.floor(hours / 24)}일 전`
}

export const OFFLINE = '대시보드 서버에 연결할 수 없습니다. start.bat 으로 다시 띄워 주세요.'

/** 409 · 404 의 본문 {"error": 이유} 에서 이유만 꺼낸다. 서버에 닿지 않으면(TypeError) 그렇다고. */
export function reasonOf(error: unknown): string {
  if (error instanceof TypeError) return OFFLINE
  const text = error instanceof Error ? error.message : String(error)
  try {
    return (JSON.parse(text) as { error?: string }).error ?? text
  } catch {
    return text
  }
}
