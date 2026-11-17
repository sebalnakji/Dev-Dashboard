/** 화면 표시용 서식. */

const NOT_MEASURED = '측정 중'

export function percent(value: number | null | undefined): string {
  return value == null ? NOT_MEASURED : `${value.toFixed(1)} %`
}

/**
 * 사용률에 따른 색.
 *
 * 카드의 목적이 한눈에 이상 여부를 판단하는 것이라, 숫자보다 색이 먼저 눈에 들어와야 한다.
 */
export type Level = 'normal' | 'warn' | 'danger'

export const WARN_AT = 70
export const DISK_WARN_AT = 80
/** 디스크는 차면 복구가 번거로워 조금 더 일찍 경고한다. */
export const DANGER_AT = 90

export function levelOf(value: number | null | undefined, warnAt = WARN_AT): Level {
  if (value == null) return 'normal'
  if (value >= DANGER_AT) return 'danger'
  if (value >= warnAt) return 'warn'
  return 'normal'
}

export const LEVEL_BAR: Record<Level, string> = {
  normal: 'bg-accent',
  warn: 'bg-amber-500',
  danger: 'bg-rose-500',
}

export const LEVEL_TEXT: Record<Level, string> = {
  normal: 'text-gray-200',
  warn: 'text-amber-300',
  danger: 'text-rose-300',
}

/** "3초 전" 처럼 마지막 갱신이 얼마나 지났는지. */
export function agoText(ts: number | null, now: number): string {
  if (ts == null) return '수집 전'
  const seconds = Math.max(0, Math.round(now / 1000 - ts))
  if (seconds < 60) return `${seconds}초 전`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}분 전`
  return `${Math.floor(minutes / 60)}시간 전`
}

const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

export function bytes(value: number | null | undefined): string {
  if (value == null) return '-'
  let size = value
  for (const unit of BYTE_UNITS) {
    if (Math.abs(size) < 1024 || unit === 'TiB') return `${size.toFixed(1)} ${unit}`
    size /= 1024
  }
  return `${size.toFixed(1)} TiB`
}

/** 네트워크 속도. 초당 바이트를 사람이 읽는 단위로. */
export function bytesPerSecond(value: number | null | undefined): string {
  return value == null ? '-' : `${bytes(value)}/s`
}

/** epoch 초를 시:분 으로. 차트 축과 툴팁에 쓴다. */
export function clockText(ts: number): string {
  const date = new Date(ts * 1000)
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

// 컨테이너가 색을 입혀 찍은 로그에는 ANSI 이스케이프가 섞여 있다.
// 그대로 두면 화면에 [0m 같은 글자가 보인다.
// ESC(제어문자)로 시작하는 게 ANSI 이스케이프의 정의라, 여기서는 의도한 것이다
// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g

export function stripAnsi(text: string): string {
  return text.replace(ANSI, '')
}

/** "17 / 62 GiB" 처럼 사용량과 전체를 한 줄로. 단위는 큰 쪽에 맞춘다. */
export function usedOfTotal(
  used: number | null | undefined,
  total: number | null | undefined,
): string | undefined {
  if (used == null || total == null) return undefined
  let unit = 'B'
  let divisor = 1
  for (const [name, size] of [
    ['TiB', 1024 ** 4],
    ['GiB', 1024 ** 3],
    ['MiB', 1024 ** 2],
    ['KiB', 1024],
  ] as const) {
    if (total >= size) {
      unit = name
      divisor = size
      break
    }
  }
  const digits = total / divisor >= 100 ? 0 : 1
  return `${(used / divisor).toFixed(digits)} / ${(total / divisor).toFixed(digits)} ${unit}`
}
