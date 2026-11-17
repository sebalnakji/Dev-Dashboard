import { LEVEL_BAR, LEVEL_TEXT, levelOf, percent } from '../format'

/** 이름 · 막대 · 수치 한 줄. 카드의 기본 단위다. */
export function Gauge({
  label,
  value,
  amount,
  warnAt,
}: {
  label: string
  value: number | null
  /** "17 / 62 GiB" 처럼 실제 용량. 퍼센트만으로는 여유분을 가늠할 수 없다. */
  amount?: string
  /** 노란색으로 바뀌는 기준. 지표마다 다르다. */
  warnAt?: number
}) {
  const level = levelOf(value, warnAt)
  const width = value == null ? 0 : Math.min(100, Math.max(0, value))

  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-24 shrink-0 truncate text-gray-400" title={label}>
        {label}
      </span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${LEVEL_BAR[level]}`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className={`w-16 shrink-0 text-right tabular-nums ${LEVEL_TEXT[level]}`}>
        {percent(value)}
      </span>
      <span className="w-28 shrink-0 text-right text-xs tabular-nums text-gray-500">
        {amount ?? ''}
      </span>
    </div>
  )
}
